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
    text,
)
from sqlalchemy.engine import Engine, make_url
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
    ik = Column(Integer, nullable=False, default=23, server_default="23")
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
    url = make_url(settings.database_uri)
    if url.drivername.startswith("sqlite"):
        if url.database:
            db_path = resolve_path(url.database)
            url = url.set(database=str(db_path))
            settings.database_path = str(db_path)
        else:
            settings.database_path = None
    else:
        settings.database_path = None
    settings.database_uri = str(url)
    return create_engine(str(url), future=True)


engine = get_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


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


def _generate_unique_device_name(session: Session) -> str:
    while True:
        candidate = _uuid_str()
        if get_device_by_name(session, candidate) is None:
            return candidate


def _generate_unique_action_name(session: Session, device_id: str) -> str:
    while True:
        candidate = _uuid_str()
        if get_action_by_name(session, device_id, candidate) is None:
            return candidate


def create_device(
    session: Session,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Device:
    description = (description or "").strip()
    cleaned_name = (name or "").strip()
    if not cleaned_name:
        cleaned_name = _generate_unique_device_name(session)
    device = Device(
        id=device_id or _uuid_str(),
        name=cleaned_name,
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
    description = (description or "").strip()
    cleaned_name = (name or "").strip()
    if not cleaned_name:
        cleaned_name = _generate_unique_action_name(session, device.id)
    action = Action(
        id=action_id or _uuid_str(),
        device_id=device.id,
        name=cleaned_name,
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
    normalized_ik = ik if ik > 0 else 23
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
]
