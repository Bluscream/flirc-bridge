from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load .env file if present in the project root.
load_dotenv()


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


@dataclass(slots=True)
class Settings:
    """Application configuration loaded from environment variables."""

    database_path: str = os.environ.get("DB_PATH", "patterns.db")
    irtools_path: str = os.environ.get("IRTOOLS_PATH", "irtools")
    flirc_util_path: str = os.environ.get("FLIRC_UTIL_PATH", "flirc_util")
    log_file: Optional[str] = os.environ.get("LOG_FILE")

    mqtt_broker: str = os.environ.get("MQTT_BROKER", "localhost")
    mqtt_port: int = _int(os.environ.get("MQTT_PORT"), 1883)
    mqtt_username: str | None = os.environ.get("MQTT_USERNAME", "homeassistant")
    mqtt_password: str | None = os.environ.get("MQTT_PASSWORD")
    mqtt_client_id: str = os.environ.get("MQTT_CLIENT_ID", "flirc-bridge")
    mqtt_base_topic: str = os.environ.get("MQTT_BASE_TOPIC", "flirc_bridge")
    mqtt_discovery_prefix: str = os.environ.get("MQTT_DISCOVERY_PREFIX", "homeassistant")
    mqtt_retain: bool = _bool(os.environ.get("MQTT_RETAIN"), True)
    mqtt_enabled: bool = _bool(os.environ.get("MQTT_ENABLED"), True)

    web_host: str = os.environ.get("WEB_HOST", "0.0.0.0")
    web_port: int = _int(os.environ.get("WEB_PORT"), 8000)
    web_reload: bool = _bool(os.environ.get("WEB_RELOAD"), False)
    web_token: str | None = os.environ.get("WEB_TOKEN")

    ir_listen_timeout: int = _int(os.environ.get("IR_LISTEN_TIMEOUT"), 10)
    auto_store_patterns: bool = _bool(os.environ.get("AUTO_STORE_PATTERNS"), False)


def get_settings() -> Settings:
    """Return singleton settings instance."""
    # Simple memoization using function attribute.
    if not hasattr(get_settings, "_instance"):
        get_settings._instance = Settings()  # type: ignore[attr-defined]
    return get_settings._instance  # type: ignore[attr-defined]
