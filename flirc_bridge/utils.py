"""Reusable utility helpers for flirc_bridge."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


TRUTHY_SENTINELS = {"1", "true", "yes", "on"}
REFRESH_SENTINELS = TRUTHY_SENTINELS | {"refresh"}


def coerce_bool(value: Optional[str], default: bool = False) -> bool:
    """Return *value* coerced to bool, falling back to *default* when unset."""

    if value is None:
        return default
    return value.strip().lower() in TRUTHY_SENTINELS


def coerce_int(value: Optional[str], default: int) -> int:
    """Return *value* coerced to int, falling back to *default* on error."""

    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def coerce_float(value: Optional[str], default: float) -> float:
    """Return *value* coerced to float, falling back to *default* on error."""

    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def slugify(*parts: str) -> str:
    """Return a lowercase, underscore-delimited identifier for *parts*."""

    tokens = []
    for part in parts:
        if not part:
            continue
        token = part.strip().lower().replace(" ", "_")
        if token:
            tokens.append(token)
    return "_".join(tokens)


def format_reason(reason: Any) -> str:
    """Format MQTT/paho reason codes consistently for logging."""

    try:
        name = reason.name  # type: ignore[attr-defined]
    except AttributeError:
        return str(reason)
    else:
        try:
            code = int(reason)
        except (TypeError, ValueError):
            return str(name)
        return f"{name} ({code})"


def resolve_path(raw_path: str) -> Path:
    """Expand *raw_path* to an absolute Path, ensuring parent directories exist."""

    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def should_refresh(value: Optional[str]) -> bool:
    """Return True when query-string toggle requests a refresh."""

    if value is None:
        return False
    return value.strip().lower() in REFRESH_SENTINELS


def scrub_dict(data: Dict[str, Any], values_to_remove: Iterable[Any]) -> Dict[str, Any]:
    """Recursively remove keys whose values match any item in *values_to_remove*."""

    banned_values = [item for item in values_to_remove if item]

    def _should_remove(value: Any) -> bool:
        return any(value == banned for banned in banned_values)

    def _scrub(node: Any) -> Any:
        if isinstance(node, dict):
            cleaned: Dict[str, Any] = {}
            for key, value in node.items():
                if _should_remove(value):
                    continue
                cleaned[key] = _scrub(value)
            return cleaned
        if isinstance(node, list):
            cleaned_list = []
            for item in node:
                if _should_remove(item):
                    continue
                cleaned_list.append(_scrub(item))
            return cleaned_list
        return node

    return _scrub(dict(data))


def compute_pattern_hash(
    fmt: str,
    data: Iterable[Any],
    *,
    repeat: int | None = None,
    ik: int | None = None,
) -> str:
    """Return md5 hex digest for a pattern payload.

    The hash incorporates format, normalized data (as strings), repeat, and ik values.
    """

    normalized = {
        "format": (fmt or "").lower(),
        "data": [str(item) for item in (data or [])],
        "repeat": 1 if repeat is None or repeat < 1 else int(repeat),
        "ik": 23 if ik is None or ik <= 0 else int(ik),
    }
    blob = json.dumps(normalized, separators=(",", ":"), sort_keys=True)
    return hashlib.md5(blob.encode("utf-8")).hexdigest()


__all__ = [
    "coerce_bool",
    "coerce_int",
    "coerce_float",
    "slugify",
    "format_reason",
    "resolve_path",
    "should_refresh",
    "scrub_dict",
    "compute_pattern_hash",
]
