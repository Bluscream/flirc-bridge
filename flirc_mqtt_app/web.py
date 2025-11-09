from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .database import Action, Device, Pattern, get_session, init_db
from .irtools import IRTools, IRToolsError
from .mqtt_manager import MQTTManager
from .schemas import (
    ErrorResponse,
    PatternListResponse,
    PatternRecord,
    ReceivePatternRequest,
    ReceivePatternResponse,
    SendPatternRequest,
)
from .services import delete_pattern, export_patterns, pattern_record_to_db

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def create_app(settings_override=None) -> FastAPI:
    init_db()
    settings = settings_override or get_settings()
    irtools = IRTools()
    mqtt_manager = MQTTManager(settings=settings, irtools=irtools)
    mqtt_manager.start()

    app = FastAPI(
        title="Flirc MQTT Bridge",
        version="0.1.0",
        default_response_class=JSONResponse,
    )

    def get_app_state():
        return {"settings": settings, "irtools": irtools, "mqtt": mqtt_manager}

    @app.on_event("shutdown")
    def shutdown_event():
        mqtt_manager.stop()

    # ----------------------------------------------------------- web interface
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        with get_session() as session:
            records = export_patterns(session)
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "patterns": records,
                "settings": settings,
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

    @app.post(
        "/api/patterns",
        response_model=PatternRecord,
        status_code=status.HTTP_201_CREATED,
    )
    def create_pattern(record: PatternRecord, state=Depends(get_app_state)):
        with get_session() as session:
            pattern_record_to_db(session, record, mqtt=state["mqtt"])
        return record

    @app.put(
        "/api/patterns/{device}/{action}",
        response_model=PatternRecord,
    )
    def update_pattern(device: str, action: str, record: PatternRecord, state=Depends(get_app_state)):
        if record.device != device or record.action != action:
            raise HTTPException(status_code=400, detail="Device/action mismatch with payload")
        with get_session() as session:
            pattern_record_to_db(session, record, mqtt=state["mqtt"])
        return record

    @app.delete(
        "/api/patterns/{device}/{action}",
        response_model=dict,
    )
    def remove_pattern(device: str, action: str, state=Depends(get_app_state)):
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
        irtools: IRTools = state["irtools"]

        if payload.device and payload.action:
            with get_session() as session:
                query = (
                    session.query(Pattern)
                    .join(Action)
                    .join(Device)
                    .filter(Device.name == payload.device, Action.name == payload.action)
                )
                if payload.format:
                    query = query.filter(Pattern.format == payload.format)
                pattern: Optional[Pattern] = query.first()
                if not pattern:
                    raise HTTPException(status_code=404, detail="Stored pattern not found")
                data = json.loads(pattern.data)
                fmt = pattern.format
        else:
            fmt = payload.format  # type: ignore[assignment]
            data = payload.data  # type: ignore[assignment]

        try:
            stdout = irtools.send(fmt, data, carrier=payload.carrier, repeat=payload.repeat)  # type: ignore[arg-type]
        except IRToolsError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return {"status": "sent", "output": stdout}

    @app.post(
        "/api/receive",
        response_model=ReceivePatternResponse,
        responses={400: {"model": ErrorResponse}},
    )
    def receive_pattern(request: ReceivePatternRequest, state=Depends(get_app_state)):
        irtools: IRTools = state["irtools"]
        try:
            data, output = irtools.listen(request.format, request.timeout)
        except IRToolsError as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        if request.save:
            if not request.device or not request.action:
                raise HTTPException(status_code=400, detail="device/action required when save=true")
            record = PatternRecord(
                device=request.device,
                action=request.action,
                formats=[{"format": request.format, "data": data}],
            )
            with get_session() as session:
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
                    for format_name, data in formats.items():
                        if data is None:
                            continue
                        if isinstance(data, list):
                            entries = [str(item) for item in data]
                        else:
                            entries = [str(data)]
                        format_models.append({"format": format_name, "data": entries})
                    if format_models:
                        record = PatternRecord(
                            device=str(device),
                            action=str(action),
                            formats=format_models,
                        )
                        pattern_record_to_db(session, record, mqtt=state["mqtt"])
                        imported += 1

        return {"status": "ok", "imported": imported}

    return app


__all__ = ["create_app"]
