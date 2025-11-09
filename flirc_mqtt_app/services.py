from __future__ import annotations

import json
from typing import Dict, List

from sqlalchemy.orm import Session

from . import database
from .mqtt_manager import MQTTManager
from .schemas import PatternFormatModel, PatternRecord


def pattern_record_to_db(
    session: Session,
    record: PatternRecord,
    mqtt: MQTTManager | None = None,
) -> None:
    stored_formats: Dict[str, str] = {}
    for fmt in record.formats:
        json_payload = json.dumps(fmt.data)
        database.upsert_pattern(
            session=session,
            device_name=record.device,
            action_name=record.action,
            format_name=fmt.format,
            data=json_payload,
        )
        stored_formats[fmt.format] = json_payload

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
        for pattern in action.patterns:
            mqtt.publish_discovery(device, action, pattern)


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


def export_patterns(session: Session) -> Dict[str, Dict[str, Dict[str, List[str]]]]:
    export: Dict[str, Dict[str, Dict[str, List[str]]]] = {}
    for device, action, pattern in database.iter_patterns(session):
        export.setdefault(device.name, {}).setdefault(action.name, {})[pattern.format] = json.loads(
            pattern.data
        )
    return export


def export_patterns_json(session: Session) -> Dict[str, Dict[str, Dict[str, List[str]]]]:
    return export_patterns(session)
