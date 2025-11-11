from __future__ import annotations

import hashlib
import json
import logging
import platform
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse

from sqlalchemy.orm import Session

from ..database import (
    Action,
    Device,
    Pattern,
    create_action,
    create_device,
    get_session,
    UNKNOWN_NAME,
)
from ..mqtt import MQTTManager
from ..schemas import (
    ActionPayload,
    ActionUpdatePayload,
    ActionResponse,
    ErrorResponse,
    DeviceResponse,
    PatternFormatLiteral,
    PatternListResponse,
    PatternModel,
    PatternRecord,
    ReceivePatternRequest,
    ReceivePatternResponse,
    SendPatternPayload,
    DevicePayload,
    DeviceUpdatePayload,
)
from ..tool import (
    FlircUtil,
    FlircUtilError,
    IRTools,
    IRToolsError,
    ToolError,
    get_tool_cache,
)
from ..services import (
    build_action_response,
    build_device_response,
    build_pattern_record,
    delete_action,
    delete_device,
    delete_pattern,
    export_patterns,
    pattern_record_to_db,
)
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

    # Helper functions -----------------------------------------------------

    def _looks_like_uuid(value: Optional[str]) -> bool:
        if not value:
            return False
        try:
            UUID(str(value))
            return True
        except ValueError:
            return False

    def _split_pattern_values(raw: str) -> List[str]:
        if raw is None:
            return []
        if "," in raw:
            return [segment for segment in (part.strip() for part in raw.split(",")) if segment]
        return [raw.strip()] if raw.strip() else []

    def _get_device(session: Session, identifier: Optional[str], name_hint: Optional[str] = None) -> Optional[Device]:
        if identifier and _looks_like_uuid(identifier):
            device = session.get(Device, identifier)
            if device:
                return device
        target_name = name_hint or identifier
        if target_name:
            return (
                session.query(Device)
                .filter(Device.name == target_name)
                .one_or_none()
            )
        return None

    def _get_action(
        session: Session,
        identifier: Optional[str],
        name_hint: Optional[str],
        device: Optional[Device],
    ) -> Optional[Action]:
        if identifier and _looks_like_uuid(identifier):
            action = session.get(Action, identifier)
            if action:
                return action
        target_name = name_hint or identifier
        if target_name:
            query = session.query(Action).filter(Action.name == target_name)
            if device:
                query = query.filter(Action.device_id == device.id)
            return query.one_or_none()
        return None

    def _load_pattern_data(raw: Optional[str]) -> List[str]:
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return [raw]
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
        return [str(parsed)]

    def _merge_send_payload(request: Request, payload: Optional[SendPatternPayload]) -> SendPatternPayload:
        merged: Dict[str, Any] = {}
        params = request.query_params

        for key in ["device", "device_name", "action", "action_name", "format", "repeat", "ik", "save"]:
            if key in params:
                merged[key] = params.get(key)

        if params.getlist("data"):
            values: List[str] = []
            for item in params.getlist("data"):
                values.extend(_split_pattern_values(item))
            merged["data"] = values

        if params.getlist("pattern"):
            pattern_ids = [value for value in params.getlist("pattern") if _looks_like_uuid(value)]
            pattern_payloads = [value for value in params.getlist("pattern") if not _looks_like_uuid(value)]
            if pattern_ids:
                merged["pattern"] = pattern_ids[-1]
            if pattern_payloads:
                merged.setdefault("data", [])
                for value in pattern_payloads:
                    merged["data"].extend(_split_pattern_values(value))

        if payload is not None:
            merged.update(
                payload.model_dump(exclude_unset=True)
            )

        return SendPatternPayload(**merged)

    def _handle_send(method: str, payload: SendPatternPayload, state: Dict[str, Any]) -> Dict[str, Any]:
        irtools = state["irtools"]
        flirc = state["flirc_util"]
        source = f"{method.upper()} /api/send"
        results: List[Dict[str, Any]] = []

        with get_session() as session:
            stored_patterns: List[Pattern] = []
            device: Optional[Device] = None
            action: Optional[Action] = None

            if payload.pattern:
                pattern = session.get(Pattern, payload.pattern)
                if not pattern:
                    raise HTTPException(status_code=404, detail="Pattern not found")
                stored_patterns.append(pattern)
                action = pattern.action
                device = action.device if action else None
            elif payload.action:
                device = _get_device(session, payload.device, payload.device_name)
                action = _get_action(session, payload.action, payload.action_name, device)
                if not action:
                    raise HTTPException(status_code=404, detail="Action not found")
                if payload.device and device and action.device_id != device.id:
                    raise HTTPException(status_code=400, detail="Action does not belong to specified device")
                if device is None:
                    device = action.device
                stored_patterns.extend(sorted(action.patterns, key=lambda p: p.created_at or datetime.min))
            elif payload.device or payload.device_name:
                device = _get_device(session, payload.device, payload.device_name)
                if not device:
                    raise HTTPException(status_code=404, detail="Device not found")
                for action in device.actions:
                    stored_patterns.extend(sorted(action.patterns, key=lambda p: p.created_at or datetime.min))

            if stored_patterns:
                for pattern in stored_patterns:
                    action = pattern.action
                    device = action.device if action else None
                    pattern_data = _load_pattern_data(pattern.data)
                    repeat_value = payload.repeat if payload.repeat is not None else pattern.repeat or 1
                    ik_value = payload.ik if payload.ik is not None else pattern.ik or 23000
                    response = transmit_pattern(
                        pattern.format,
                        pattern_data,
                        ik_value,
                        repeat_value,
                        irtools,
                        flirc,
                        device=device.name if device else None,
                        action=action.name if action else None,
                        source=source,
                    )
                    pattern.sent_at = datetime.utcnow()
                    session.flush()
                    results.append(
                        {
                            "pattern_id": pattern.id,
                            "action_id": pattern.action_id,
                            "device_id": device.id if device else None,
                            "format": pattern.format,
                            "repeat": repeat_value,
                            "ik": ik_value,
                            "response": response,
                        }
                    )

            if payload.data:
                fmt = payload.format or "raw"
                data_values = payload.data
                repeat_value = payload.repeat or 1
                ik_value = payload.ik or 23000
                response = transmit_pattern(
                    fmt,
                    data_values,
                    ik_value,
                    repeat_value,
                    irtools,
                    flirc,
                    device=device.name if device else payload.device_name,
                    action=action.name if action else payload.action_name,
                    source=source,
                )
                result_entry = {
                    "pattern_id": None,
                    "action_id": action.id if action else None,
                    "device_id": device.id if device else None,
                    "format": fmt,
                    "repeat": repeat_value,
                    "ik": ik_value,
                    "response": response,
                }

                if payload.save:
                    record = PatternRecord(
                        device_id=payload.device if payload.device and _looks_like_uuid(payload.device) else (device.id if device else None),
                        device=payload.device_name if payload.device_name else (device.name if device else (None if (payload.device and _looks_like_uuid(payload.device)) else payload.device)),
                        action_id=payload.action if payload.action and _looks_like_uuid(payload.action) else (action.id if action else None),
                        action=payload.action_name if payload.action_name else (action.name if action else (None if (payload.action and _looks_like_uuid(payload.action)) else payload.action)),
                        patterns=[PatternModel(format=fmt, data=data_values, repeat=repeat_value, ik=ik_value)],
                    )
                    saved_patterns = pattern_record_to_db(session, record, mqtt=state["mqtt"])
                    if saved_patterns:
                        saved_pattern = saved_patterns[-1]
                        result_entry["saved_pattern_id"] = saved_pattern.id
                        result_entry["action_id"] = saved_pattern.action_id
                        result_entry["device_id"] = saved_pattern.action.device_id if saved_pattern.action else result_entry["device_id"]
                results.append(result_entry)

            if not results:
                raise HTTPException(status_code=400, detail="No patterns matched the request")

        return {"status": "sent", "count": len(results), "results": results}

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

        db_info: Dict[str, Any] = {
            "url": getattr(settings_obj, "database_uri", None),
        }
        db_path_value = getattr(settings_obj, "database_path", "") or None
        if db_path_value:
            db_path = Path(db_path_value)
            db_info["path"] = str(db_path)
            db_info["exists"] = db_path.exists()
            if db_path.exists():
                try:
                    stat = db_path.stat()
                    db_info["size_bytes"] = stat.st_size
                    db_info["modified"] = stat.st_mtime
                except OSError as exc:
                    db_info["stat_error"] = str(exc)
        else:
            db_info["path"] = None

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
        response_model=PatternListResponse,
        summary="Return all stored patterns as JSON structure",
    )
    def get_patterns_json():
        with get_session() as session:
            return export_patterns(session)

    # region LegacyCompat
    @router.get(
        "/api/patterns",
        response_model=PatternListResponse,
        summary="Return all stored patterns as JSON structure",
        include_in_schema=False,
    )
    def get_patterns_legacy():
        return get_patterns_json()
    # endregion

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
        "/api/devices",
        response_model=DeviceResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_device_route(
        payload: DevicePayload,
        state=Depends(get_app_state),
        _: None = Depends(require_auth),
    ):
        with get_session() as session:
            device = create_device(
                session,
                name=payload.name,
                description=payload.description or "",
            )
            response = build_device_response(device)
        return response

    @router.put(
        "/api/devices/{device_id}",
        response_model=DeviceResponse,
    )
    def update_device_route(
        device_id: str,
        payload: DeviceUpdatePayload,
        state=Depends(get_app_state),
        _: None = Depends(require_auth),
    ):
        with get_session() as session:
            device = session.get(Device, device_id)
            if not device:
                raise HTTPException(status_code=404, detail="Device not found")
            if payload.name is not None:
                name = payload.name.strip()
                if not name:
                    raise HTTPException(status_code=400, detail="Device name cannot be empty")
                device.name = name
            if payload.description is not None:
                device.description = payload.description
            session.flush()
            response = build_device_response(device)
        return response

    @router.delete(
        "/api/devices/{device_id}",
        response_model=dict,
    )
    def delete_device_route(
        device_id: str,
        state=Depends(get_app_state),
        _: None = Depends(require_auth),
    ):
        with get_session() as session:
            removed = delete_device(session, device_id, mqtt=state["mqtt"])
        if not removed:
            raise HTTPException(status_code=404, detail="Device not found")
        return {"status": "deleted"}

    @router.post(
        "/api/actions",
        response_model=ActionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_action_route(
        payload: ActionPayload,
        state=Depends(get_app_state),
        _: None = Depends(require_auth),
    ):
        with get_session() as session:
            device = _get_device(session, payload.device_id, payload.device_name)
            if not device and payload.device_name:
                device = create_device(session, name=payload.device_name, description="")
            if not device:
                device = create_device(session, name=payload.device_name or UNKNOWN_NAME, description="")
            action = create_action(
                session,
                device=device,
                name=payload.name,
                description=payload.description or "",
            )
            response = build_action_response(action)
        return response

    @router.put(
        "/api/actions/{action_id}",
        response_model=ActionResponse,
    )
    def update_action_route(
        action_id: str,
        payload: ActionUpdatePayload,
        state=Depends(get_app_state),
        _: None = Depends(require_auth),
    ):
        with get_session() as session:
            action = session.get(Action, action_id)
            if not action:
                raise HTTPException(status_code=404, detail="Action not found")
            if payload.name is not None:
                name = payload.name.strip()
                if not name:
                    raise HTTPException(status_code=400, detail="Action name cannot be empty")
                action.name = name
            if payload.description is not None:
                action.description = payload.description
            session.flush()
            response = build_action_response(action)
        return response

    @router.delete(
        "/api/actions/{action_id}",
        response_model=dict,
    )
    def delete_action_route(
        action_id: str,
        state=Depends(get_app_state),
        _: None = Depends(require_auth),
    ):
        with get_session() as session:
            removed = delete_action(session, action_id, mqtt=state["mqtt"])
        if not removed:
            raise HTTPException(status_code=404, detail="Action not found")
        return {"status": "deleted"}

    @router.post(
        "/api/patterns",
        response_model=PatternRecord,
        status_code=status.HTTP_201_CREATED,
    )
    def create_pattern(
        record: PatternRecord,
        state=Depends(get_app_state),
        _: None = Depends(require_auth),
    ):
        with get_session() as session:
            saved_patterns = pattern_record_to_db(session, record, mqtt=state["mqtt"])
            if not saved_patterns:
                raise HTTPException(status_code=400, detail="No pattern data provided")
            action = saved_patterns[-1].action or session.get(Action, saved_patterns[-1].action_id)
            response = build_pattern_record(action)
        return response

    @router.put(
        "/api/patterns/{pattern_id}",
        response_model=PatternRecord,
    )
    def update_pattern(
        pattern_id: str,
        record: PatternRecord,
        state=Depends(get_app_state),
        _: None = Depends(require_auth),
    ):
        if not record.patterns:
            raise HTTPException(status_code=400, detail="At least one pattern is required")
        primary = record.patterns[0]
        if primary.id and primary.id != pattern_id:
            raise HTTPException(status_code=400, detail="Pattern ID mismatch")
        primary.id = pattern_id
        with get_session() as session:
            saved_patterns = pattern_record_to_db(session, record, mqtt=state["mqtt"])
            action = saved_patterns[-1].action or session.get(Action, saved_patterns[-1].action_id)
            response = build_pattern_record(action)
        return response

    @router.delete(
        "/api/patterns/{pattern_id}",
        response_model=dict,
    )
    def remove_pattern(
        pattern_id: str,
        state=Depends(get_app_state),
        _: None = Depends(require_auth),
    ):
        with get_session() as session:
            removed = delete_pattern(session, pattern_id, mqtt=state["mqtt"])
        if not removed:
            raise HTTPException(status_code=404, detail="Pattern not found")
        return {"status": "deleted"}

    @router.api_route(
        "/api/send",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        response_model=dict,
        responses={400: {"model": ErrorResponse}},
    )
    def send_pattern_route(
        request: Request,
        payload: Optional[SendPatternPayload] = Body(None),
        state=Depends(get_app_state),
    ):
        merged_payload = _merge_send_payload(request, payload)
        return _handle_send(request.method, merged_payload, state)

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
                    # region LegacyCompat
                    legacy_hash = hashlib.sha256(data_string.encode("utf-8")).hexdigest()
                    existing = (
                        session.query(Pattern)
                        .filter(Pattern.hash == legacy_hash)
                        .first()
                    )
                    # endregion
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
                        patterns=[{"format": request.format, "data": data, "repeat": 1, "ik": 23000}],
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
                    pattern_models = []
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
                        pattern_model = {"format": format_name, "data": entries}
                        if hash_value:
                            pattern_model["hash"] = str(hash_value)
                        if repeat_value is not None:
                            try:
                                repeat_int = int(repeat_value)
                            except (TypeError, ValueError):
                                repeat_int = None
                            if repeat_int is not None and repeat_int >= 1:
                                pattern_model["repeat"] = repeat_int
                        if ik_value is not None:
                            try:
                                ik_int = int(ik_value)
                            except (TypeError, ValueError):
                                ik_int = None
                            if ik_int is not None and ik_int > 0:
                                pattern_model["ik"] = ik_int
                        pattern_models.append(pattern_model)
                    if pattern_models:
                        record = PatternRecord(
                            device=str(device),
                            action=str(action),
                            patterns=pattern_models,
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
