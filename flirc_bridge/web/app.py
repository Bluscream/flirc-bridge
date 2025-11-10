from __future__ import annotations

import hashlib
import json
import logging
import platform
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from types import SimpleNamespace

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..application import BridgeRuntime
from ..config import Settings, get_settings
from ..database import Action, Device, Pattern, get_session
from .. import flirc
from ..mqtt import MQTTManager
from ..schemas import (
    ErrorResponse,
    PatternListResponse,
    PatternFormatLiteral,
    PatternRecord,
    ReceivePatternRequest,
    ReceivePatternResponse,
    SendPatternRequest,
)
from ..services import delete_pattern, export_patterns, pattern_record_to_db

logger = logging.getLogger(__name__)


templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
APP_VERSION = "0.1.0"


def _parse_version_output(output: str, tool_hint: Optional[str] = None) -> Dict[str, Any]:
    text = (output or "").strip()
    data: Dict[str, Any] = {"raw": text}
    if tool_hint:
        data["tool"] = tool_hint
    return data


def _collect_version_info(command: str) -> Dict[str, Any]:
    """Deprecated helper retained for backwards compatibility."""
    return {"command": command, "error": "deprecated"}


def create_app(settings_override: Optional[Settings] = None, runtime: Optional[BridgeRuntime] = None) -> FastAPI:
    if runtime is None:
        runtime = BridgeRuntime(settings_override or get_settings())
    runtime.start()
    settings = runtime.settings

    app = FastAPI(
        title=f"{settings.mqtt_device_name} - Flirc Bridge",
        version=APP_VERSION,
        default_response_class=JSONResponse,
    )
    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).resolve().parent / "static"),
        name="static",
    )
    app.state.started_at = datetime.utcnow()

    def require_auth(request: Request, header_token: Optional[str] = Header(default=None, alias="X-Auth-Token")) -> None:
        if not settings.web_token:
            return
        provided = header_token or request.query_params.get("token")
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            provided = auth_header.split(" ", 1)[1].strip()
        if provided == settings.web_token:
            return
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    def get_app_state():
        return {
            "settings": settings,
            "mqtt": runtime.mqtt_manager,
            "runtime": runtime,
        }

    @app.on_event("shutdown")
    def shutdown_event():
        runtime.stop()

    def _load_stored_pattern(
        device: str,
        action: str,
        format_name: Optional[PatternFormatLiteral] = None,
    ) -> Dict[str, Any]:
        with get_session() as session:
            query = (
                session.query(Pattern)
                .join(Action)
                .join(Device)
                .filter(Device.name == device, Action.name == action)
            )
            if format_name:
                query = query.filter(Pattern.format == format_name)
            pattern: Optional[Pattern] = query.first()
            if not pattern:
                raise HTTPException(status_code=404, detail="Stored pattern not found")
            payload = pattern.data or "[]"
            fmt = pattern.format
            pattern_hash = pattern.hash
        logger.info(
            "Loaded stored pattern device=%s action=%s format=%s hash=%s",
            device,
            action,
            fmt,
            pattern_hash,
        )
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"Stored pattern is invalid JSON: {exc}") from exc
        return {"format": fmt, "data": data}

    def _transmit_pattern(
        fmt: str,
        data: Optional[List[str]],
        carrier: Optional[int],
        repeat: Optional[int],
        *,
        device: Optional[str] = None,
        action: Optional[str] = None,
        source: str = "/api/send",
    ) -> Dict[str, Any]:
        data_repr = data if data is None else json.dumps(data)
        if device and action:
            logger.info(
                "[%s] Dispatching stored pattern device=%s action=%s format=%s carrier=%s repeat=%s data=%s",
                source,
                device,
                action,
                fmt,
                carrier,
                repeat,
                data_repr,
            )
        else:
            logger.info(
                "[%s] Dispatching custom pattern format=%s carrier=%s repeat=%s data=%s",
                source,
                fmt,
                carrier,
                repeat,
                data_repr,
            )
        try:
            stdout = flirc.irtools_send(fmt, data or [], carrier=carrier, repeat=repeat)
            logger.info(
                "[%s] IRTools send successful: %s",
                source,
                stdout.strip() if isinstance(stdout, str) else stdout,
            )
            return {"status": "sent", "output": stdout}
        except flirc.IRToolsError as primary_exc:
            try:
                fallback_output = flirc.flirc_send_ir(data or [])
                logger.warning(
                    "[%s] IRTools failed (%s); falling back to flirc_util. Output=%s",
                    source,
                    primary_exc,
                    fallback_output,
                )
                return {"status": "sent", "output": fallback_output, "fallback": "flirc_util"}
            except flirc.FlircUtilError as secondary_exc:
                raise HTTPException(status_code=500, detail=f"IRTools failed: {primary_exc}; flirc_util failed: {secondary_exc}")

    # ----------------------------------------------------------- web interface
    @app.get(
        "/api/status",
        response_model=dict,
        summary="Return component status and version information",
    )
    def get_status(request: Request, state=Depends(get_app_state)):
        settings_obj = state["settings"]
        mqtt_state: Optional[MQTTManager] = state["mqtt"]

        settings_payload = asdict(settings_obj)
        settings_payload.pop("mqtt_password", None)
        settings_payload.pop("web_token", None)

        started_at = getattr(request.app.state, "started_at", None)
        uptime_seconds: Optional[float] = None
        if started_at:
            uptime_seconds = (datetime.utcnow() - started_at).total_seconds()

        db_path = Path(settings_obj.database_path)
        db_info: Dict[str, Any] = {
            "path": str(db_path),
            "exists": db_path.exists(),
        }
        if db_path.exists():
            try:
                stat = db_path.stat()
                db_info["size_bytes"] = stat.st_size
                db_info["modified"] = stat.st_mtime
            except OSError as exc:  # pragma: no cover - filesystem issue
                db_info["stat_error"] = str(exc)

        try:
            with get_session() as session:
                db_info["device_count"] = session.query(Device).count()
                db_info["action_count"] = session.query(Action).count()
                db_info["pattern_count"] = session.query(Pattern).count()
        except Exception as exc:  # pragma: no cover - database unavailable
            db_info["error"] = str(exc)

        try:
            irtools_info = flirc.irtools_version_info()
        except flirc.IRToolsError as exc:
            irtools_info = {"tool": "irtools", "error": str(exc)}

        try:
            flirc_settings = flirc.flirc_settings_info()
            details = flirc_settings.get("details", {})
            settings_map = flirc_settings.get("settings", {})
            flirc_summary = {
                "version": flirc_settings.get("version"),
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
                "recorded_keys": flirc_settings.get("recorded_keys", []),
            }
        except flirc.FlircUtilError as exc:
            flirc_summary = {"tool": "flirc_util", "error": str(exc)}

        environment_info = {
            "python_version": sys.version.split()[0],
            "python_build": sys.version,
            "platform": platform.platform(),
            "executable": sys.executable,
        }

        mqtt_info = {
            "enabled": bool(settings_obj.mqtt_enabled),
            "broker": settings_obj.mqtt_broker,
            "port": settings_obj.mqtt_port,
            "client_id": settings_obj.mqtt_prefix,
            "base_topic": settings_obj.mqtt_base_topic,
            "discovery_prefix": settings_obj.mqtt_discovery_prefix,
            "retain": settings_obj.mqtt_retain,
            "running": mqtt_state is not None,
        }
        if settings_obj.mqtt_username:
            mqtt_info["username"] = settings_obj.mqtt_username

        return {
            "application": {
                "name": app.title,
                "version": app.version,
                "started_at": started_at.isoformat() if started_at else None,
                "uptime_seconds": uptime_seconds,
            },
            "environment": environment_info,
            "settings": settings_payload,
            "database": db_info,
            "tools": {
                "irtools": irtools_info,
                "flirc": flirc_summary,
            },
            "mqtt": mqtt_info,
            "features": {
                "auto_store_patterns": settings_obj.auto_store_patterns,
            },
        }

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        with get_session() as session:
            records = export_patterns(session)
        display_settings_data = asdict(settings)
        display_settings_data.pop("mqtt_password", None)
        display_settings_data.pop("web_token", None)
        display_settings = SimpleNamespace(**display_settings_data)
        return templates.TemplateResponse(
            "index.html.jinja",
            {
                "request": request,
                "patterns": records,
                "settings": display_settings,
                "mqtt_available": runtime.mqtt_manager is not None,
                "requires_token": bool(settings.web_token),
            },
        )

    # ------------------------------------------------------------------- API's
    @app.get(
        "/api/patterns.json",
        response_model=dict,
        summary="Return all stored patterns as JSON structure",
    )
    def get_patterns_json():
        with get_session() as session:
            return export_patterns(session)

    @app.get(
        "/api/patterns",
        response_model=dict,
        summary="Return all stored patterns as JSON structure",
        include_in_schema=False,
    )
    def get_patterns_legacy():
        return get_patterns_json()

    @app.post(
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
        except Exception as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=500, detail=str(exc))
        return {"published": published}

    @app.post(
        "/api/patterns",
        response_model=PatternRecord,
        status_code=status.HTTP_201_CREATED,
    )
    def create_pattern(record: PatternRecord, state=Depends(get_app_state), _: None = Depends(require_auth)):
        with get_session() as session:
            pattern_record_to_db(session, record, mqtt=state["mqtt"])
        return record

    @app.put(
        "/api/patterns/{device}/{action}",
        response_model=PatternRecord,
    )
    def update_pattern(device: str, action: str, record: PatternRecord, state=Depends(get_app_state), _: None = Depends(require_auth)):
        if record.device != device or record.action != action:
            raise HTTPException(status_code=400, detail="Device/action mismatch with payload")
        with get_session() as session:
            pattern_record_to_db(session, record, mqtt=state["mqtt"])
        return record

    @app.delete(
        "/api/patterns/{device}/{action}",
        response_model=dict,
    )
    def remove_pattern(device: str, action: str, state=Depends(get_app_state), _: None = Depends(require_auth)):
        with get_session() as session:
            removed = delete_pattern(session, device, action, mqtt=state["mqtt"])
        if not removed:
            raise HTTPException(status_code=404, detail="Pattern not found")
        return {"status": "deleted"}

    @app.post(
        "/api/send",
        response_model=dict,
        responses={400: {"model": ErrorResponse}},
    )
    def send_pattern(payload: SendPatternRequest, state=Depends(get_app_state)):
        if payload.device and payload.action:
            loaded = _load_stored_pattern(payload.device, payload.action, payload.format)
            fmt = loaded["format"]
            data = loaded["data"]
            carrier = payload.carrier
            repeat = payload.repeat
        else:
            if payload.format is None or payload.data is None:
                raise HTTPException(status_code=400, detail="format and data are required for custom patterns")
            fmt = payload.format  # type: ignore[assignment]
            data = payload.data  # type: ignore[assignment]
            carrier = payload.carrier
            repeat = payload.repeat

        return _transmit_pattern(
            fmt,
            data,
            carrier,
            repeat,
            device=payload.device,
            action=payload.action,
            source="POST /api/send",
        )

    @app.get(
        "/api/send",
        response_model=dict,
        responses={400: {"model": ErrorResponse}},
        summary="Send a stored pattern via query parameters",
    )
    def send_pattern_get(
        device: str,
        action: str,
        format: Optional[PatternFormatLiteral] = None,
        carrier: Optional[int] = None,
        repeat: Optional[int] = None,
        state=Depends(get_app_state),
    ):
        loaded = _load_stored_pattern(device, action, format)
        return _transmit_pattern(
            loaded["format"],
            loaded["data"],
            carrier,
            repeat,
            device=device,
            action=action,
            source="GET /api/send",
        )

    @app.post(
        "/api/receive",
        response_model=ReceivePatternResponse,
        responses={400: {"model": ErrorResponse}},
    )
    def receive_pattern(request: ReceivePatternRequest, state=Depends(get_app_state), _: None = Depends(require_auth)):
        settings_obj = state["settings"]
        try:
            data, output = flirc.irtools_listen(request.format, request.timeout)
        except flirc.IRToolsError as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        should_save = request.save
        if not should_save and settings_obj.auto_store_patterns:
            # Auto-store when enabled and request didn't explicitly ask to skip saving
            should_save = True

        if should_save:
            device_name = request.device or "Auto Stored Device"
            data_string = json.dumps(data)
            data_hash = hashlib.sha256(data_string.encode("utf-8")).hexdigest()
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
                    existing = (
                        session.query(Pattern)
                        .filter(Pattern.hash.is_(None), Pattern.data == data_string)
                        .first()
                    )
                if existing is None:
                    record = PatternRecord(
                        device=device_name,
                        action=action_name,
                        formats=[{"format": request.format, "data": data}],
                    )
                    pattern_record_to_db(session, record, mqtt=state["mqtt"])

        return ReceivePatternResponse(format=request.format, data=data, raw_output=output)

    @app.post(
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
                        if isinstance(entry, dict) and "data" in entry:
                            data_values = entry.get("data")
                            hash_value = entry.get("hash")
                        else:
                            data_values = entry
                            hash_value = None
                        if data_values is None:
                            continue
                        if isinstance(data_values, list):
                            entries = [str(item) for item in data_values]
                        else:
                            entries = [str(data_values)]
                        format_model = {"format": format_name, "data": entries}
                        if hash_value:
                            format_model["hash"] = str(hash_value)
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

    @app.get(
        "/api/logs",
        summary="Return flirc device logs",
    )
    def get_device_logs(state=Depends(get_app_state), _: None = Depends(require_auth)):
        try:
            log_output = flirc.flirc_device_log()
        except flirc.FlircUtilError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return PlainTextResponse(log_output or "")

    @app.post(
        "/api/test",
        response_model=dict,
        summary="Run flirc device unit tests",
    )
    def run_unit_test(state=Depends(get_app_state), _: None = Depends(require_auth)):
        try:
            result = flirc.flirc_unit_test()
        except flirc.FlircUtilError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        payload = {
            "exitcode": result.returncode,
            "stdout": (result.stdout or "").strip(),
            "stderr": (result.stderr or "").strip(),
        }
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=payload)
        return payload

    return app


__all__ = ["create_app"]
