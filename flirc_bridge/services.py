from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from . import database
from .mqtt import MQTTManager
from .schemas import (
    ActionResponse,
    DeviceResponse,
    PatternModel,
    PatternRecord,
    PatternListResponse,
)
from .utils import compute_pattern_hash


def pattern_record_to_db(
    session: Session,
    record: PatternRecord,
    mqtt: MQTTManager | None = None,
) -> List[database.Pattern]:
    device = _resolve_device(session, record)
    action = _resolve_action(session, record, device)

    saved_patterns: List[database.Pattern] = []
    for pattern_model in record.patterns:
        saved = _save_pattern_model(session, action, pattern_model)
        saved_patterns.append(saved)

    session.flush()

    if mqtt and device and action:
        if saved_patterns:
            mqtt.refresh_action(device, action)
        else:
            mqtt.clear_discovery(device, action)

    return saved_patterns


def delete_device(
    session: Session,
    device_id: str,
    mqtt: MQTTManager | None = None,
) -> bool:
    device = session.get(database.Device, device_id)
    if not device:
        return False

    if mqtt:
        for action in list(device.actions):
            mqtt.clear_discovery(device, action)

    session.delete(device)
    session.flush()
    return True


def delete_action(
    session: Session,
    action_id: str,
    mqtt: MQTTManager | None = None,
) -> bool:
    action = session.get(database.Action, action_id)
    if not action:
        return False
    device = action.device

    if mqtt and device:
        mqtt.clear_discovery(device, action)

    session.delete(action)
    session.flush()

    if device and not device.actions:
        session.delete(device)
        session.flush()

    return True


def delete_pattern(
    session: Session,
    pattern_id: str,
    mqtt: MQTTManager | None = None,
) -> bool:
    pattern = session.get(database.Pattern, pattern_id)
    if not pattern:
        return False

    action = pattern.action
    device = action.device if action else None

    session.delete(pattern)
    session.flush()

    if mqtt and device and action:
        if action.patterns:
            mqtt.refresh_action(device, action)
        else:
            mqtt.clear_discovery(device, action)

    if action and not action.patterns:
        session.delete(action)
        session.flush()

    if device and not device.actions:
        session.delete(device)
        session.flush()

    return True


def export_patterns(session: Session) -> PatternListResponse:
    devices: List[DeviceResponse] = [
        build_device_response(device)
        for device in session.query(database.Device).order_by(database.Device.created_at).all()
    ]
    return PatternListResponse(devices=devices)


def export_patterns_json(session: Session) -> Dict[str, List[Dict[str, object]]]:
    response = export_patterns(session)
    return response.model_dump()


def build_device_response(device: database.Device) -> DeviceResponse:
    actions = [build_action_response(action) for action in device.actions]
    return DeviceResponse(
        id=device.id,
        name=device.name,
        description=device.description,
        created_at=_isoformat(device.created_at),
        updated_at=_isoformat(device.updated_at),
        actions=actions,
    )


def build_action_response(action: database.Action) -> ActionResponse:
    patterns = [build_pattern_model(pattern) for pattern in action.patterns]
    return ActionResponse(
        id=action.id,
        device_id=action.device_id,
        name=action.name,
        description=action.description,
        created_at=_isoformat(action.created_at),
        updated_at=_isoformat(action.updated_at),
        patterns=patterns,
    )


def build_pattern_model(pattern: database.Pattern) -> PatternModel:
    payload_data = _load_pattern_data(pattern.data)
    repeat_value = pattern.repeat or 1
    ik_value = pattern.ik or 23
    pattern_hash = pattern.hash
    if not pattern_hash or len(pattern_hash) != 32:
        pattern_hash = compute_pattern_hash(pattern.format, payload_data, repeat=repeat_value, ik=ik_value)
        pattern.hash = pattern_hash
    return PatternModel(
        id=pattern.id,
        format=pattern.format,
        data=payload_data,
        repeat=repeat_value,
        ik=ik_value,
        hash=pattern_hash,
        created_at=_isoformat(pattern.created_at),
        updated_at=_isoformat(pattern.updated_at),
        sent_at=_isoformat(pattern.sent_at),
    )


def build_pattern_record(action: database.Action) -> PatternRecord:
    device = action.device
    patterns = [
        build_pattern_model(pattern)
        for pattern in sorted(action.patterns, key=lambda p: p.created_at or datetime.min)
    ]
    return PatternRecord(
        device_id=device.id if device else None,
        device=device.name if device else None,
        device_description=device.description if device else "",
        action_id=action.id,
        action=action.name,
        action_description=action.description,
        patterns=patterns,
    )


def _resolve_device(session: Session, record: PatternRecord) -> database.Device:
    if record.device_id:
        device = database.get_device_by_id(session, record.device_id)
        if device:
            return device
    if record.device:
        device = database.get_device_by_name(session, record.device)
        if device:
            return device
        return database.create_device(
            session,
            name=record.device,
            description=record.device_description,
        )
    return database.create_device(
        session,
        name=None,
        description=record.device_description,
    )


def _resolve_action(session: Session, record: PatternRecord, device: database.Device) -> database.Action:
    if record.action_id:
        action = database.get_action_by_id(session, record.action_id)
        if action:
            return action
    if record.action:
        action = database.get_action_by_name(session, device.id, record.action)
        if action:
            return action
        return database.create_action(
            session,
            device=device,
            name=record.action,
            description=record.action_description,
        )
    return database.create_action(
        session,
        device=device,
        name=record.action,
        description=record.action_description,
    )


def _save_pattern_model(
    session: Session,
    action: database.Action,
    model: PatternModel,
) -> database.Pattern:
    repeat_value = model.repeat if model.repeat >= 1 else 1
    ik_value = model.ik if model.ik > 0 else 23
    payload_json = json.dumps(model.data)
    hash_value = compute_pattern_hash(model.format, model.data, repeat=repeat_value, ik=ik_value)

    if model.id:
        pattern = session.get(database.Pattern, model.id)
        if pattern:
            pattern.format = model.format
            pattern.data = payload_json
            pattern.repeat = repeat_value
            pattern.ik = ik_value
            pattern.hash = hash_value
            pattern.updated_at = datetime.utcnow()
            return pattern

    return database.create_pattern(
        session,
        action=action,
        fmt=model.format,
        data=payload_json,
        repeat=repeat_value,
        ik=ik_value,
        pattern_id=model.id,
    )


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


def _isoformat(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    return value.replace(microsecond=0).isoformat()
