from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Dict, Generator, Iterable, Optional, Tuple

import enum
import json
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    inspect,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, relationship, sessionmaker

from .config import get_settings
from .utils import compute_pattern_hash, resolve_path

Base = declarative_base()


def _uuid_str() -> str:
    return str(uuid.uuid4())


class PatternFormatEnum(str, enum.Enum):
    RAW = "raw"
    CSV = "csv"
    PRONTO = "pronto"


UNKNOWN_DEVICE_ID = "00000000-0000-0000-0000-000000000000"
UNKNOWN_ACTION_ID = "00000000-0000-0000-0000-000000000001"
UNKNOWN_NAME = "Unknown"
UNKNOWN_DESCRIPTION = "Default entity used when device or action is unspecified."


class Device(Base):
    __tablename__ = "devices"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    name = Column(String(128), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True, default="", server_default=text("''"))
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
    )

    actions = relationship(
        "Action",
        back_populates="device",
        cascade="all, delete-orphan",
        order_by="Action.created_at",
    )


class Action(Base):
    __tablename__ = "actions"
    __table_args__ = (UniqueConstraint("device_id", "name", name="uq_device_action"),)

    id = Column(String(36), primary_key=True, default=_uuid_str)
    device_id = Column(String(36), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True, default="", server_default=text("''"))
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
    )

    device = relationship("Device", back_populates="actions")
    patterns = relationship(
        "Pattern",
        back_populates="action",
        cascade="all, delete-orphan",
        order_by="Pattern.created_at",
    )


class Pattern(Base):
    __tablename__ = "patterns"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    action_id = Column(String(36), ForeignKey("actions.id", ondelete="CASCADE"), nullable=False, index=True)
    format = Column(String(16), nullable=False, default=PatternFormatEnum.RAW.value)
    data = Column(Text, nullable=False)
    repeat = Column(Integer, nullable=False, default=1, server_default="1")
    ik = Column(Integer, nullable=False, default=23000, server_default="23000")
    hash = Column(String(32), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
    )
    sent_at = Column(DateTime, nullable=True)

    action = relationship("Action", back_populates="patterns")


def get_engine() -> Engine:
    settings = get_settings()
    db_path = resolve_path(settings.database_path)
    settings.database_path = str(db_path)
    return create_engine(f"sqlite:///{db_path}", future=True)


engine = get_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    _migrate_legacy_schema()
    Base.metadata.create_all(bind=engine)
    _ensure_unknown_entities()


@contextmanager
def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_device_by_id(session: Session, device_id: str) -> Device | None:
    return session.get(Device, device_id)


def get_device_by_name(session: Session, name: str) -> Device | None:
    return (
        session.query(Device)
        .filter(Device.name == name)
        .one_or_none()
    )


def get_action_by_id(session: Session, action_id: str) -> Action | None:
    return session.get(Action, action_id)


def get_action_by_name(session: Session, device_id: str, name: str) -> Action | None:
    return (
        session.query(Action)
        .filter(Action.device_id == device_id, Action.name == name)
        .one_or_none()
    )


def create_device(
    session: Session,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Device:
    name = name or UNKNOWN_NAME
    description = description or ""
    device = Device(
        id=device_id or _uuid_str(),
        name=name,
        description=description,
    )
    session.add(device)
    session.flush()
    return device


def create_action(
    session: Session,
    *,
    device: Device,
    name: Optional[str] = None,
    description: Optional[str] = None,
    action_id: Optional[str] = None,
) -> Action:
    name = name or UNKNOWN_NAME
    description = description or ""
    action = Action(
        id=action_id or _uuid_str(),
        device_id=device.id,
        name=name,
        description=description,
    )
    session.add(action)
    session.flush()
    return action


def create_pattern(
    session: Session,
    *,
    action: Action,
    fmt: str,
    data: str,
    repeat: int,
    ik: int,
    pattern_id: Optional[str] = None,
) -> Pattern:
    normalized_repeat = repeat if repeat >= 1 else 1
    normalized_ik = ik if ik > 0 else 23000
    normalized_data = _normalize_data_for_hash(data)
    hash_value = compute_pattern_hash(fmt, normalized_data, repeat=normalized_repeat, ik=normalized_ik)
    pattern = Pattern(
        id=pattern_id or _uuid_str(),
        action_id=action.id,
        format=fmt,
        data=data,
        repeat=normalized_repeat,
        ik=normalized_ik,
        hash=hash_value,
    )
    session.add(pattern)
    session.flush()
    return pattern


def delete_pattern(session: Session, pattern_id: str) -> bool:
    pattern = session.get(Pattern, pattern_id)
    if not pattern:
        return False
    action = pattern.action
    device = action.device if action else None
    session.delete(pattern)
    session.flush()

    if action and not action.patterns:
        session.delete(action)
        session.flush()

    if device and not device.actions:
        session.delete(device)
        session.flush()

    return True


def iter_patterns(session: Session) -> Iterable[Tuple[Device, Action, Pattern]]:
    devices = (
        session.query(Device)
        .order_by(Device.name)
        .all()
    )
    for device in devices:
        for action in device.actions:
            for pattern in action.patterns:
                yield device, action, pattern


# region LegacyMigration
def _migrate_legacy_schema() -> None:
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    if "devices" not in table_names or "actions" not in table_names or "patterns" not in table_names:
        return

    device_columns = inspector.get_columns("devices")
    if not device_columns:
        return

    first_col_type = device_columns[0]["type"].__class__.__name__.lower()
    if "integer" not in first_col_type:
        # Already on new schema
        return

    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE patterns RENAME TO patterns_legacy"))
        connection.execute(text("ALTER TABLE actions RENAME TO actions_legacy"))
        connection.execute(text("ALTER TABLE devices RENAME TO devices_legacy"))

    Base.metadata.create_all(bind=engine)

    with SessionLocal() as session, session.begin():
        legacy_devices = session.execute(
            text("SELECT id, name FROM devices_legacy ORDER BY id")
        ).all()
        legacy_actions = session.execute(
            text("SELECT id, device_id, name FROM actions_legacy ORDER BY id")
        ).all()
        legacy_patterns = session.execute(
            text(
                "SELECT id, action_id, format, data, hash, repeat, ik, created_at, updated_at, NULL as sent_at "
                "FROM patterns_legacy ORDER BY id"
            )
        ).all()

        device_map: Dict[int, Device] = {}
        for legacy_id, name in legacy_devices:
            clean_name = name or UNKNOWN_NAME
            if clean_name.strip().lower() == UNKNOWN_NAME.lower():
                device = create_device(
                    session,
                    name=UNKNOWN_NAME,
                    description=UNKNOWN_DESCRIPTION,
                    device_id=UNKNOWN_DEVICE_ID,
                )
            else:
                device = create_device(session, name=clean_name)
            device_map[legacy_id] = device

        action_map: Dict[int, Action] = {}
        for legacy_id, device_id, name in legacy_actions:
            parent_device = device_map.get(device_id) or device_map.get(0)
            if parent_device is None:
                parent_device = create_device(
                    session,
                    name=UNKNOWN_NAME,
                    description=UNKNOWN_DESCRIPTION,
                    device_id=UNKNOWN_DEVICE_ID,
                )
                device_map[device_id] = parent_device
            clean_name = name or UNKNOWN_NAME
            if (
                parent_device.id == UNKNOWN_DEVICE_ID
                and clean_name.strip().lower() == UNKNOWN_NAME.lower()
            ):
                action = create_action(
                    session,
                    device=parent_device,
                    name=UNKNOWN_NAME,
                    description=UNKNOWN_DESCRIPTION,
                    action_id=UNKNOWN_ACTION_ID,
                )
            else:
                action = create_action(session, device=parent_device, name=clean_name)
            action_map[legacy_id] = action

        for (
            legacy_id,
            action_id,
            fmt,
            data,
            hash_value,
            repeat,
            ik,
            created_at,
            updated_at,
            sent_at,
        ) in legacy_patterns:
            parent_action = action_map.get(action_id)
            if parent_action is None:
                parent_action = action_map.get(0)
            if parent_action is None:
                parent_device = device_map.get(0)
                if parent_device is None:
                    parent_device = create_device(
                        session,
                        name=UNKNOWN_NAME,
                        description=UNKNOWN_DESCRIPTION,
                        device_id=UNKNOWN_DEVICE_ID,
                    )
                    device_map[0] = parent_device
                parent_action = create_action(
                    session,
                    device=parent_device,
                    name=UNKNOWN_NAME,
                    description=UNKNOWN_DESCRIPTION,
                    action_id=UNKNOWN_ACTION_ID,
                )
                action_map[action_id] = parent_action

            repeat_value = int(repeat) if repeat not in (None, "") else 1
            ik_value = int(ik) if ik not in (None, "") else 23000
            fmt_value = (fmt or PatternFormatEnum.RAW.value).lower()
            normalized_data = _normalize_data_for_hash(data)
            hash_value = compute_pattern_hash(fmt_value, normalized_data, repeat=repeat_value, ik=ik_value)

            payload_json = data if data is not None else "[]"
            pattern = Pattern(
                id=_uuid_str(),
                action_id=parent_action.id,
                format=fmt_value,
                data=payload_json,
                repeat=repeat_value,
                ik=ik_value,
                hash=hash_value,
                created_at=created_at or datetime.utcnow(),
                updated_at=updated_at or datetime.utcnow(),
                sent_at=sent_at,
            )
            session.add(pattern)

    with engine.begin() as connection:
        connection.execute(text("DROP TABLE patterns_legacy"))
        connection.execute(text("DROP TABLE actions_legacy"))
        connection.execute(text("DROP TABLE devices_legacy"))
# endregion


def _normalize_data_for_hash(raw: str) -> Iterable[str]:
    if raw is None:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return [str(raw)]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return [str(parsed)]


def _ensure_unknown_entities() -> None:
    with SessionLocal() as session, session.begin():
        device = session.get(Device, UNKNOWN_DEVICE_ID)
        if not device:
            device = Device(
                id=UNKNOWN_DEVICE_ID,
                name=UNKNOWN_NAME,
                description=UNKNOWN_DESCRIPTION,
            )
            session.add(device)

        action = session.get(Action, UNKNOWN_ACTION_ID)
        if not action:
            action = Action(
                id=UNKNOWN_ACTION_ID,
                device_id=device.id,
                name=UNKNOWN_NAME,
                description=UNKNOWN_DESCRIPTION,
            )
            session.add(action)


__all__ = [
    "Action",
    "Device",
    "Pattern",
    "SessionLocal",
    "init_db",
    "get_session",
    "create_device",
    "create_action",
    "create_pattern",
    "delete_pattern",
    "get_device_by_id",
    "get_device_by_name",
    "get_action_by_id",
    "get_action_by_name",
    "iter_patterns",
    "UNKNOWN_DEVICE_ID",
    "UNKNOWN_ACTION_ID",
    "UNKNOWN_NAME",
]
