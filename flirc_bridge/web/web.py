from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from types import SimpleNamespace

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..application import BridgeRuntime
from ..config import Settings, get_settings
from ..database import Action, Device, Pattern, get_session
from ..tool import FlircUtil, IRTools, ToolError, get_flirc_util, get_irtools, initialize_tools, send_ir_pattern
from ..schemas import PatternFormatLiteral
from ..services import export_patterns
from .api import create_api_router

logger = logging.getLogger(__name__)


templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
APP_VERSION = "0.1.0"


def _parse_version_output(output: str, tool_hint: Optional[str] = None) -> Dict[str, Any]:
    text = (output or "").strip()
    data: Dict[str, Any] = {"raw": text}
    if tool_hint:
        data["tool"] = tool_hint
    return data


def create_app(settings_override: Optional[Settings] = None, runtime: Optional[BridgeRuntime] = None) -> FastAPI:
    if runtime is None:
        runtime = BridgeRuntime(settings_override or get_settings())
    runtime.start()
    initialize_tools()
    settings = runtime.settings

    irtools = runtime.irtools
    flirc_util = runtime.flirc_util
    app = FastAPI(
        title=f"{settings.instance_name} - Flirc Bridge",
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
            "irtools": get_irtools(),
            "flirc_util": get_flirc_util(),
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
            repeat_value = pattern.repeat or 1
            ik_value = pattern.ik or 23
        logger.info(
            "Loaded stored pattern device=%s action=%s format=%s hash=%s repeat=%s ik=%s",
            device,
            action,
            fmt,
            pattern_hash,
            repeat_value,
            ik_value,
        )
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"Stored pattern is invalid JSON: {exc}") from exc
        return {"format": fmt, "data": data, "repeat": repeat_value, "ik": ik_value}

    def _transmit_pattern(
        fmt: str,
        data: Optional[List[str]],
        ik: Optional[int],
        repeat: Optional[int],
        irtools: IRTools,
        flirc: FlircUtil,
        *,
        device: Optional[str] = None,
        action: Optional[str] = None,
        source: str = "/api/send",
    ) -> Dict[str, Any]:
        data_repr = data if data is None else json.dumps(data)
        effective_repeat = 1 if repeat is None or repeat < 1 else repeat
        effective_ik = 23 if ik is None or ik <= 0 else ik
        if device and action:
            logger.info(
                "[%s] Dispatching stored pattern device=%s action=%s format=%s ik=%s repeat=%s data=%s",
                source,
                device,
                action,
                fmt,
                effective_ik,
                effective_repeat,
                data_repr,
            )
        else:
            logger.info(
                "[%s] Dispatching custom pattern format=%s ik=%s repeat=%s data=%s",
                source,
                fmt,
                effective_ik,
                effective_repeat,
                data_repr,
            )
        try:
            result = send_ir_pattern(
                fmt,
                data or [],
                ik=effective_ik,
                repeat=effective_repeat,
                irtools=irtools,
                flirc_util=flirc,
            )
            logger.info(
                "[%s] IR send successful via %s: %s",
                source,
                result["tool"],
                (result["output"] or "").strip(),
            )
            if result.get("fallback"):
                logger.warning(
                    "[%s] IRTools failed (%s); flirc_util fallback succeeded",
                    source,
                    result.get("irtools_error"),
                )
            return {"status": "sent", **result}
        except ToolError as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    def _build_template_context(
        request: Request,
        *,
        active_page: str,
        extra: Optional[Dict[str, Any]] = None,
        app_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        display_settings_data = asdict(settings)
        display_settings_data.pop("mqtt_password", None)
        display_settings_data.pop("web_token", None)
        display_settings = SimpleNamespace(**display_settings_data)
        context: Dict[str, Any] = {
            "request": request,
            "settings": display_settings,
            "mqtt_available": runtime.mqtt_manager is not None,
            "requires_token": bool(settings.web_token),
            "active_page": active_page,
            "current_year": datetime.utcnow().year,
            "app_context": app_context
            or {"patterns": {}, "requiresToken": bool(settings.web_token)},
        }
        if extra:
            context.update(extra)
        return context

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        with get_session() as session:
            pattern_response = export_patterns(session)
        devices = pattern_response.devices
        devices_json = pattern_response.model_dump()["devices"]
        context = _build_template_context(
            request,
            active_page="send",
            extra={"devices": devices},
            app_context={"devices": devices_json, "requiresToken": bool(settings.web_token)},
        )
        return templates.TemplateResponse("index.html.jinja", context)

    @app.get("/manage", response_class=HTMLResponse)
    def manage_page(request: Request):
        with get_session() as session:
            pattern_response = export_patterns(session)
        devices = pattern_response.devices
        devices_json = pattern_response.model_dump()["devices"]
        context = _build_template_context(
            request,
            active_page="manage",
            extra={"devices": devices},
            app_context={"devices": devices_json, "requiresToken": bool(settings.web_token)},
        )
        return templates.TemplateResponse("manage.html.jinja", context)

    @app.get("/logs", response_class=HTMLResponse)
    def logs_page(request: Request):
        context = _build_template_context(
            request,
            active_page="logs",
        )
        return templates.TemplateResponse("logs.html.jinja", context)

    @app.get("/status", response_class=HTMLResponse)
    def status_page(request: Request):
        context = _build_template_context(
            request,
            active_page="status",
        )
        return templates.TemplateResponse("status.html.jinja", context)

    api_router = create_api_router(
        runtime=runtime,
        get_app_state=get_app_state,
        require_auth=require_auth,
        load_stored_pattern=_load_stored_pattern,
        transmit_pattern=_transmit_pattern,
    )
    app.include_router(api_router)

    return app


__all__ = ["create_app"]
