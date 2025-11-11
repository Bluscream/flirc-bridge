from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Generator, Iterable

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
from .utils import resolve_path

Base = declarative_base()


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    actions = relationship("Action", back_populates="device", cascade="all, delete-orphan")

    def slug(self) -> str:
        return self.name.lower().replace(" ", "_")


class Action(Base):
    __tablename__ = "actions"
    __table_args__ = (UniqueConstraint("device_id", "name", name="uq_device_action"),)

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(128), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    device = relationship("Device", back_populates="actions")
    patterns = relationship("Pattern", back_populates="action", cascade="all, delete-orphan")

    def slug(self) -> str:
        return self.name.lower().replace(" ", "_")


class Pattern(Base):
    __tablename__ = "patterns"
    __table_args__ = (UniqueConstraint("action_id", "format", name="uq_action_format"),)

    id = Column(Integer, primary_key=True)
    action_id = Column(Integer, ForeignKey("actions.id", ondelete="CASCADE"), nullable=False)
    format = Column(String(32), nullable=False)
    data = Column(Text, nullable=False)
    hash = Column(String(64), nullable=True, index=True)
    repeat = Column(Integer, nullable=True, default=1, server_default="1")
    ik = Column(Integer, nullable=True, default=23000, server_default="23000")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
    )

    action = relationship("Action", back_populates="patterns")


def get_engine() -> Engine:
    settings = get_settings()
    db_path = resolve_path(settings.database_path)
    settings.database_path = str(db_path)
    return create_engine(f"sqlite:///{db_path}", future=True)


engine = get_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_pattern_columns()


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


def upsert_pattern(
    session: Session,
    device_name: str,
    action_name: str,
    format_name: str,
    data: str,
    data_hash: str,
    repeat: int | None,
    ik: int | None,
) -> Pattern:
    device = session.query(Device).filter(Device.name == device_name).one_or_none()
    if device is None:
        device = Device(name=device_name)
        session.add(device)
        session.flush()

    action = (
        session.query(Action)
        .filter(Action.device_id == device.id, Action.name == action_name)
        .one_or_none()
    )
    if action is None:
        action = Action(device_id=device.id, name=action_name)
        session.add(action)
        session.flush()

    pattern = (
        session.query(Pattern)
        .filter(Pattern.action_id == action.id, Pattern.format == format_name)
        .one_or_none()
    )
    repeat_value = 1 if repeat is None or repeat < 1 else repeat
    ik_value = 23000 if ik is None or ik <= 0 else ik

    if pattern is None:
        pattern = Pattern(
            action_id=action.id,
            format=format_name,
            data=data,
            hash=data_hash,
            repeat=repeat_value,
            ik=ik_value,
        )
        session.add(pattern)
    else:
        pattern.data = data
        pattern.hash = data_hash
        pattern.repeat = repeat_value
        pattern.ik = ik_value
    session.flush()
    return pattern


def remove_action(session: Session, device_name: str, action_name: str) -> bool:
    action: Action | None = (
        session.query(Action)
        .join(Device)
        .filter(Device.name == device_name, Action.name == action_name)
        .one_or_none()
    )
    if not action:
        return False
    session.delete(action)
    session.flush()

    # Remove device if it has no actions left.
    remaining = session.query(Action).filter(Action.device_id == action.device_id).count()
    if remaining == 0:
        device = session.get(Device, action.device_id)
        if device:
            session.delete(device)
            session.flush()
    return True


def iter_patterns(session: Session) -> Iterable[tuple[Device, Action, Pattern]]:
    for device in session.query(Device).order_by(Device.name).all():
        for action in sorted(device.actions, key=lambda a: a.name):
            for pattern in sorted(action.patterns, key=lambda p: p.format):
                yield device, action, pattern


def remove_pattern_format(session: Session, device_name: str, action_name: str, format_name: str) -> bool:
    pattern: Pattern | None = (
        session.query(Pattern)
        .join(Action)
        .join(Device)
        .filter(
            Device.name == device_name,
            Action.name == action_name,
            Pattern.format == format_name,
        )
        .one_or_none()
    )
    if not pattern:
        return False

    action_id = pattern.action_id
    action = session.get(Action, action_id)
    device_id = action.device_id if action else None

    session.delete(pattern)
    session.flush()

    remaining_for_action = session.query(Pattern).filter(Pattern.action_id == action_id).count()
    if remaining_for_action == 0 and action:
        session.delete(action)
        session.flush()

    if device_id is not None:
        remaining_actions = session.query(Action).filter(Action.device_id == device_id).count()
        if remaining_actions == 0:
            device = session.get(Device, device_id)
            if device:
                session.delete(device)
                session.flush()

    return True


def _ensure_pattern_columns() -> None:
    inspector = inspect(engine)
    if not inspector.has_table("patterns"):
        return
    column_names = {column["name"] for column in inspector.get_columns("patterns")}
    statements = []
    if "repeat" not in column_names:
        statements.append("ALTER TABLE patterns ADD COLUMN repeat INTEGER DEFAULT 1")
    if "ik" not in column_names:
        statements.append("ALTER TABLE patterns ADD COLUMN ik INTEGER DEFAULT 23000")
    if statements:
        with engine.begin() as connection:
            for stmt in statements:
                connection.execute(text(stmt))
        # Update existing rows to defaults where needed
        with engine.begin() as connection:
            connection.execute(text("UPDATE patterns SET repeat = 1 WHERE repeat IS NULL OR repeat < 1"))
            connection.execute(text("UPDATE patterns SET ik = 23000 WHERE ik IS NULL OR ik <= 0"))


__all__ = [
    "Action",
    "Device",
    "Pattern",
    "SessionLocal",
    "init_db",
    "get_session",
    "upsert_pattern",
    "remove_action",
    "remove_pattern_format",
    "iter_patterns",
]
