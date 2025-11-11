from __future__ import annotations

import hashlib
import json
import logging
import platform
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse

from ..database import Action, Device, Pattern, get_session
from ..mqtt import MQTTManager
from ..schemas import (
    ErrorResponse,
    PatternFormatLiteral,
    PatternRecord,
    ReceivePatternRequest,
    ReceivePatternResponse,
    SendPatternRequest,
)
from ..services import delete_pattern, delete_pattern_format, export_patterns, pattern_record_to_db
from ..utils import compute_pattern_hash, should_refresh

logger = logging.getLogger(__name__)


LoadPatternFn = Callable[[str, str, Optional[PatternFormatLiteral]], Dict[str, Any]]
TransmitPatternFn = Callable[
    [str, Optional[Any], Optional[int], Optional[int], IRTools, FlircUtil],
    Dict[str, Any],
]


def create_api_router(
    *,
    runtime,
    get_app_state: Callable[[], Dict[str, Any]],
    require_auth: Callable[..., None],
    load_stored_pattern: LoadPatternFn,
    transmit_pattern: Callable[..., Dict[str, Any]],
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/status",
        response_model=dict,
        summary="Return component status and version information",
    )
    def get_status(request: Request, state=Depends(get_app_state)):
        settings_obj = state["settings"]
        mqtt_state: Optional[MQTTManager] = state["mqtt"]
        irtools_instance: IRTools = state["irtools"]
        flirc_instance: FlircUtil = state["flirc_util"]

        settings_payload = asdict(settings_obj)
        settings_payload.pop("mqtt_password", None)
        settings_payload.pop("web_token", None)

        started_at = getattr(request.app.state, "started_at", None)
        uptime_seconds: Optional[float] = None
        if started_at:
            uptime_seconds = (datetime.utcnow() - started_at).total_seconds()

        db_path = Path(settings_obj.database_path)
        db_info: Dict[str, Any] = {
            "exists": db_path.exists(),
        }
        if db_path.exists():
            try:
                stat = db_path.stat()
                db_info["size_bytes"] = stat.st_size
                db_info["modified"] = stat.st_mtime
            except OSError as exc:
                db_info["stat_error"] = str(exc)

        try:
            with get_session() as session:
                db_info["device_count"] = session.query(Device).count()
                db_info["action_count"] = session.query(Action).count()
                db_info["pattern_count"] = session.query(Pattern).count()
        except Exception as exc:
            db_info["error"] = str(exc)

        refresh_flag = should_refresh(request.query_params.get("refresh"))
        tool_error: Optional[str] = None
        try:
            tool_cache = get_tool_cache(refresh=refresh_flag)
        except ToolError as exc:
            tool_cache = {}
            tool_error = str(exc)

        irtools_cache = tool_cache.get("irtools", {})
        irtools_summary = None
        if irtools_cache:
            irtools_summary = dict(irtools_cache.get("version_info") or {})
            irtools_summary["path"] = irtools_cache.get("path")
            irtools_summary["filesize"] = irtools_cache.get("filesize")
            irtools_summary["timestamp"] = irtools_cache.get("timestamp")
            irtools_summary.pop("version_raw", None)
        else:
            try:
                irtools_summary = irtools_instance.version_info()
            except IRToolsError as exc:
                irtools_summary = {"error": str(exc if tool_error is None else tool_error)}
        if tool_error and not irtools_cache and irtools_summary is not None and "error" not in irtools_summary:
            irtools_summary["error"] = tool_error

        flirc_cache = tool_cache.get("flirc_util", {})
        if flirc_cache:
            flirc_settings = flirc_cache.get("settings_info", {})
        else:
            try:
                flirc_settings = flirc_instance.settings_info()
            except FlircUtilError as exc:
                flirc_settings = {"error": str(exc if tool_error is None else tool_error)}
                flirc_cache = {}

        details = flirc_settings.get("details", {}) if isinstance(flirc_settings, dict) else {}
        settings_map = flirc_settings.get("settings", {}) if isinstance(flirc_settings, dict) else {}
        flirc_summary = {
            "path": flirc_cache.get("path"),
            "filesize": flirc_cache.get("filesize"),
            "timestamp": flirc_cache.get("timestamp"),
            "version": flirc_cache.get("version")
            or (flirc_settings.get("version") if isinstance(flirc_settings, dict) else None),
            "fw_version": details.get("fw_version"),
            "sku": details.get("sku") or settings_map.get("product_sku"),
            "branch": details.get("branch"),
            "config": details.get("config"),
            "hash": details.get("hash"),
            "sleep_detection": settings_map.get("sleep_detection"),
            "noise_canceler": settings_map.get("noise_canceler"),
            "inter-key_delay": settings_map.get("inter_key_delay"),
            "variant": settings_map.get("variant"),
            "builtin_profiles": settings_map.get("builtin_profiles"),
            "memory_info": settings_map.get("memory_info"),
            "recorded_keys": flirc_settings.get("recorded_keys", []) if isinstance(flirc_settings, dict) else [],
        }
        if "error" in flirc_settings:
            flirc_summary = {"error": flirc_settings["error"]}

        environment_info = {
            "python_version": sys.version.split()[0],
            "python_build": sys.version,
            "platform": platform.platform(),
            "executable": sys.executable,
        }

        return {
            "application": {
                "name": request.app.title,
                "version": request.app.version,
                "started_at": started_at.isoformat() if started_at else None,
                "uptime_seconds": uptime_seconds,
                "mqtt_connected": mqtt_state is not None,
            },
            "environment": environment_info,
            "settings": settings_payload,
            "database": db_info,
            "irtools": irtools_summary,
            "flirc_util": flirc_summary,
            "cached_at": tool_cache.get("generated_at"),
        }

    @router.get(
        "/api/patterns.json",
        response_model=dict,
        summary="Return all stored patterns as JSON structure",
    )
    def get_patterns_json():
        with get_session() as session:
            return export_patterns(session)

    @router.get(
        "/api/patterns",
        response_model=dict,
        summary="Return all stored patterns as JSON structure",
        include_in_schema=False,
    )
    def get_patterns_legacy():
        return get_patterns_json()

    @router.post(
        "/api/mqtt/publish",
        response_model=dict,
        summary="Clear and republish MQTT discovery topics",
    )
    def publish_mqtt_discovery():
        if runtime.mqtt_manager is None:
            raise HTTPException(status_code=503, detail="MQTT is disabled")
        try:
            published = runtime.reset_mqtt_discovery()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return {"published": published}

    @router.post(
        "/api/patterns",
        response_model=PatternRecord,
        status_code=status.HTTP_201_CREATED,
    )
    def create_pattern(record: PatternRecord, state=Depends(get_app_state), _: None = Depends(require_auth)):
        with get_session() as session:
            pattern_record_to_db(session, record, mqtt=state["mqtt"])
        return record

    @router.put(
        "/api/patterns/{device}/{action}",
        response_model=PatternRecord,
    )
    def update_pattern(device: str, action: str, record: PatternRecord, state=Depends(get_app_state), _: None = Depends(require_auth)):
        if record.device != device or record.action != action:
            raise HTTPException(status_code=400, detail="Device/action mismatch with payload")
        with get_session() as session:
            pattern_record_to_db(session, record, mqtt=state["mqtt"])
        return record

    @router.delete(
        "/api/patterns/{device}/{action}",
        response_model=dict,
    )
    def remove_pattern(device: str, action: str, state=Depends(get_app_state), _: None = Depends(require_auth)):
        with get_session() as session:
            removed = delete_pattern(session, device, action, mqtt=state["mqtt"])
        if not removed:
            raise HTTPException(status_code=404, detail="Pattern not found")
        return {"status": "deleted"}

    @router.delete(
        "/api/patterns/{device}/{action}/{format_name}",
        response_model=dict,
    )
    def remove_pattern_format(device: str, action: str, format_name: str, state=Depends(get_app_state), _: None = Depends(require_auth)):
        with get_session() as session:
            removed = delete_pattern_format(session, device, action, format_name, mqtt=state["mqtt"])
        if not removed:
            raise HTTPException(status_code=404, detail="Pattern format not found")
        return {"status": "deleted"}

    @router.post(
        "/api/send",
        response_model=dict,
        responses={400: {"model": ErrorResponse}},
    )
    def send_pattern(payload: SendPatternRequest, state=Depends(get_app_state)):
        if payload.device and payload.action:
            loaded = load_stored_pattern(payload.device, payload.action, payload.format)
            fmt = loaded["format"]
            data = loaded["data"]
            repeat = payload.repeat if payload.repeat is not None else loaded.get("repeat")
            ik = payload.ik if payload.ik is not None else loaded.get("ik")
        else:
            if payload.format is None or payload.data is None:
                raise HTTPException(status_code=400, detail="format and data are required for custom patterns")
            fmt = payload.format
            data = payload.data
            repeat = payload.repeat
            ik = payload.ik if payload.ik is not None else payload.carrier

        return transmit_pattern(
            fmt,
            data,
            ik,
            repeat,
            state["irtools"],
            state["flirc_util"],
            device=payload.device,
            action=payload.action,
            source="POST /api/send",
        )

    @router.get(
        "/api/send",
        response_model=dict,
        responses={400: {"model": ErrorResponse}},
        summary="Send a stored pattern via query parameters",
    )
    def send_pattern_get(
        device: str,
        action: str,
        format: Optional[PatternFormatLiteral] = None,
        ik: Optional[int] = None,
        carrier: Optional[int] = None,
        repeat: Optional[int] = None,
        state=Depends(get_app_state),
    ):
        loaded = load_stored_pattern(device, action, format)
        repeat_value = repeat if repeat is not None else loaded.get("repeat")
        ik_value = ik if ik is not None else carrier
        if ik_value is None:
            ik_value = loaded.get("ik")
        return transmit_pattern(
            loaded["format"],
            loaded["data"],
            ik_value,
            repeat_value,
            state["irtools"],
            state["flirc_util"],
            device=device,
            action=action,
            source="GET /api/send",
        )

    @router.post(
        "/api/receive",
        response_model=ReceivePatternResponse,
        responses={400: {"model": ErrorResponse}},
    )
    def receive_pattern(request: ReceivePatternRequest, state=Depends(get_app_state), _: None = Depends(require_auth)):
        settings_obj = state["settings"]
        try:
            data, output = state["irtools"].listen(request.format, request.timeout)
        except IRToolsError as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        should_save = request.save
        if not should_save and settings_obj.auto_store_patterns:
            should_save = True

        if should_save:
            device_name = request.device or "Auto Stored Device"
            data_string = json.dumps(data)
            data_hash = compute_pattern_hash(request.format, data, repeat=1, ik=23000)
            if request.action:
                action_name = request.action
            else:
                digest = data_hash[:8]
                action_name = f"Auto {request.format.upper()} {digest}"
            with get_session() as session:
                existing = (
                    session.query(Pattern)
                    .filter(Pattern.hash == data_hash)
                    .first()
                )
                if existing is None:
                    legacy_hash = hashlib.sha256(data_string.encode("utf-8")).hexdigest()
                    existing = (
                        session.query(Pattern)
                        .filter(Pattern.hash == legacy_hash)
                        .first()
                    )
                if existing is None:
                    existing = (
                        session.query(Pattern)
                        .filter(Pattern.hash.is_(None), Pattern.data == data_string)
                        .first()
                    )
                if existing is None:
                    record = PatternRecord(
                        device=device_name,
                        action=action_name,
                        formats=[{"format": request.format, "data": data, "repeat": 1, "ik": 23000}],
                    )
                    pattern_record_to_db(session, record, mqtt=state["mqtt"])

        return ReceivePatternResponse(format=request.format, data=data, raw_output=output)

    @router.post(
        "/api/ingest",
        response_model=dict,
        summary="Bulk import patterns using the patterns.json structure",
    )
    def ingest_patterns(
        payload: dict = Body(..., description="Full patterns.json structure"),
        state=Depends(get_app_state),
        _: None = Depends(require_auth),
    ):
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Payload must be an object")

        imported = 0
        with get_session() as session:
            for device, actions in payload.items():
                if not isinstance(actions, dict):
                    continue
                for action, formats in actions.items():
                    if not isinstance(formats, dict):
                        continue
                    format_models = []
                    for format_name, entry in formats.items():
                        if entry is None:
                            continue
                        repeat_value = None
                        ik_value = None
                        if isinstance(entry, dict) and "data" in entry:
                            data_values = entry.get("data")
                            hash_value = entry.get("hash")
                            repeat_value = entry.get("repeat")
                            ik_value = entry.get("ik")
                        else:
                            data_values = entry
                            hash_value = None
                            repeat_value = None
                            ik_value = None
                        if data_values is None:
                            continue
                        if isinstance(data_values, list):
                            entries = [str(item) for item in data_values]
                        else:
                            entries = [str(data_values)]
                        format_model = {"format": format_name, "data": entries}
                        if hash_value:
                            format_model["hash"] = str(hash_value)
                        if repeat_value is not None:
                            try:
                                repeat_int = int(repeat_value)
                            except (TypeError, ValueError):
                                repeat_int = None
                            if repeat_int is not None and repeat_int >= 0:
                                format_model["repeat"] = repeat_int
                        if ik_value is not None:
                            try:
                                ik_int = int(ik_value)
                            except (TypeError, ValueError):
                                ik_int = None
                            if ik_int is not None and ik_int > 0:
                                format_model["ik"] = ik_int
                        format_models.append(format_model)
                    if format_models:
                        record = PatternRecord(
                            device=str(device),
                            action=str(action),
                            formats=format_models,
                        )
                        pattern_record_to_db(session, record, mqtt=state["mqtt"])
                        imported += 1

        return {"status": "ok", "imported": imported}

    @router.get(
        "/api/logs",
        summary="Return flirc device logs",
    )
    def get_device_logs(request: Request, state=Depends(get_app_state), _: None = Depends(require_auth)):
        refresh_flag = should_refresh(request.query_params.get("refresh"))
        cache = {}
        try:
            cache = get_tool_cache(refresh=refresh_flag)
        except ToolError:
            cache = {}

        flirc_cache = cache.get("flirc_util", {})
        log_output = flirc_cache.get("device_log")
        if log_output is None or refresh_flag:
            flirc_util: FlircUtil = state["flirc_util"]
            try:
                log_output = flirc_util.device_log()
            except FlircUtilError as exc:
                raise HTTPException(status_code=500, detail=str(exc))
            flirc_cache["device_log"] = log_output
        return PlainTextResponse(log_output or "")

    @router.post(
        "/api/test",
        response_model=dict,
        summary="Run flirc device unit tests",
    )
    def run_unit_test(request: Request, state=Depends(get_app_state), _: None = Depends(require_auth)):
        refresh_flag = should_refresh(request.query_params.get("refresh"))
        cache = {}
        try:
            cache = get_tool_cache(refresh=refresh_flag)
        except ToolError:
            cache = {}

        flirc_cache = cache.get("flirc_util", {})
        unit_payload = flirc_cache.get("unit_test")
        if unit_payload is None or refresh_flag:
            flirc_util: FlircUtil = state["flirc_util"]
            try:
                result = flirc_util.unit_test()
            except FlircUtilError as exc:
                raise HTTPException(status_code=500, detail=str(exc))
            unit_payload = {
                "returncode": result.returncode,
                "stdout": (result.stdout or "").strip(),
                "stderr": (result.stderr or "").strip(),
            }
            flirc_cache["unit_test"] = unit_payload
        if isinstance(unit_payload, dict) and unit_payload.get("returncode", 0) != 0 and "error" not in unit_payload:
            raise HTTPException(status_code=500, detail=unit_payload)
        if unit_payload is None:
            raise HTTPException(status_code=500, detail="Unit test output unavailable")
        return unit_payload

    return router


__all__ = ["create_api_router"]
