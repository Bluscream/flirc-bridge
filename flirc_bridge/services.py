from __future__ import annotations

import json
from typing import Dict

from sqlalchemy.orm import Session

from . import database
from .mqtt import MQTTManager
from .schemas import PatternRecord
from .utils import compute_pattern_hash


def pattern_record_to_db(
    session: Session,
    record: PatternRecord,
    mqtt: MQTTManager | None = None,
) -> None:
    for fmt in record.formats:
        json_payload = json.dumps(fmt.data)
        repeat_value = fmt.repeat if fmt.repeat is not None and fmt.repeat >= 1 else 1
        ik_value = fmt.ik if fmt.ik is not None and fmt.ik > 0 else 23000
        data_hash = compute_pattern_hash(fmt.format, fmt.data, repeat=repeat_value, ik=ik_value)
        database.upsert_pattern(
            session=session,
            device_name=record.device,
            action_name=record.action,
            format_name=fmt.format,
            data=json_payload,
            data_hash=data_hash,
            repeat=repeat_value,
            ik=ik_value,
        )

    if mqtt:
        device = session.query(database.Device).filter(database.Device.name == record.device).one()
        action = (
            session.query(database.Action)
            .filter(
                database.Action.device_id == device.id,
                database.Action.name == record.action,
            )
            .one()
        )
        mqtt.refresh_action(device, action)


def delete_pattern(
    session: Session,
    device_name: str,
    action_name: str,
    mqtt: MQTTManager | None = None,
) -> bool:
    if mqtt:
        action = (
            session.query(database.Action)
            .join(database.Device)
            .filter(database.Device.name == device_name, database.Action.name == action_name)
            .one_or_none()
        )
        if action:
            mqtt.clear_discovery(action.device, action)
    return database.remove_action(session, device_name, action_name)


def delete_pattern_format(
    session: Session,
    device_name: str,
    action_name: str,
    format_name: str,
    mqtt: MQTTManager | None = None,
) -> bool:
    action = None
    device = None
    remaining_before = 0
    if mqtt:
        action = (
            session.query(database.Action)
            .join(database.Device)
            .filter(database.Device.name == device_name, database.Action.name == action_name)
            .one_or_none()
        )
        if action:
            device = action.device
            remaining_before = (
                session.query(database.Pattern)
                .filter(database.Pattern.action_id == action.id)
                .count()
            )
    removed = database.remove_pattern_format(session, device_name, action_name, format_name)
    if not removed:
        return False

    if mqtt and action and device:
        if remaining_before <= 1:
            mqtt.clear_discovery(device, action)
        else:
            mqtt.refresh_action(device, action)
    return True


def export_patterns(session: Session) -> Dict[str, Dict[str, Dict[str, Dict[str, object]]]]:
    export: Dict[str, Dict[str, Dict[str, Dict[str, object]]]] = {}
    for device, action, pattern in database.iter_patterns(session):
        payload_data = json.loads(pattern.data)
        repeat_value = pattern.repeat or 1
        ik_value = pattern.ik or 23000
        pattern_hash = pattern.hash
        if not pattern_hash or len(pattern_hash) != 32:
            pattern_hash = compute_pattern_hash(pattern.format, payload_data, repeat=repeat_value, ik=ik_value)
            pattern.hash = pattern_hash
            session.flush()
        export.setdefault(device.name, {}).setdefault(action.name, {})[pattern.format] = {
            "data": payload_data,
            "hash": pattern_hash,
            "repeat": pattern.repeat or 1,
            "ik": pattern.ik or 23000,
        }
    return export


def export_patterns_json(session: Session) -> Dict[str, Dict[str, Dict[str, Dict[str, object]]]]:
    return export_patterns(session)
