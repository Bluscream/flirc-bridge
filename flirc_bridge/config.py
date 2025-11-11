from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load .env file if present in the project root (one level above this module).
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)

from .utils import coerce_bool, coerce_float, coerce_int


@dataclass(slots=True)
class Settings:
    """Application configuration loaded from environment variables."""

    database_uri: str = os.environ.get("DB_URI") or os.environ.get("DB_STRING") or "sqlite:///patterns.db"
    database_path: Optional[str] = None
    log_file: Optional[str] = os.environ.get("LOG_FILE")
    irtools_path: str = os.environ.get("IRTOOLS_PATH", "irtools")
    flirc_util_path: str = os.environ.get("FLIRC_UTIL_PATH", "flirc_util")
    ir_listen_timeout: int = coerce_int(os.environ.get("IR_LISTEN_TIMEOUT"), 10)
    auto_store_patterns: bool = coerce_bool(os.environ.get("AUTO_STORE_PATTERNS"), False)

    mqtt_enabled: bool = coerce_bool(os.environ.get("MQTT_ENABLED"), True)
    mqtt_broker: str = os.environ.get("MQTT_BROKER", "localhost")
    mqtt_port: int = coerce_int(os.environ.get("MQTT_PORT"), 1883)
    mqtt_username: str | None = os.environ.get("MQTT_USERNAME", "homeassistant")
    mqtt_password: str | None = os.environ.get("MQTT_PASSWORD")
    mqtt_prefix: str = os.environ.get("MQTT_PREFIX") or os.environ.get("MQTT_CLIENT_ID", "flirc_bridge")
    # Used as MQTT device name and website title
    instance_name: str = os.environ.get("INSTANCE_NAME") or os.environ.get("MQTT_DEVICE_NAME", "Flirc MQTT Bridge")
    mqtt_base_topic: str = os.environ.get("MQTT_BASE_TOPIC", "flirc_bridge")
    mqtt_discovery_prefix: str = os.environ.get("MQTT_DISCOVERY_PREFIX", "homeassistant")
    mqtt_retain: bool = coerce_bool(os.environ.get("MQTT_RETAIN"), True)
    mqtt_cleanup_enabled: bool = coerce_bool(os.environ.get("MQTT_CLEANUP_ENABLED"), True)
    mqtt_cleanup_collect_seconds: float = coerce_float(os.environ.get("MQTT_CLEANUP_COLLECT_SECONDS"), 3.0)
    mqtt_cleanup_retain_only: bool = coerce_bool(os.environ.get("MQTT_CLEANUP_RETAIN_ONLY"), False)
    mqtt_cleanup_match: str = os.environ.get("MQTT_CLEANUP_MATCH", "flirc")

    web_enabled: bool = coerce_bool(os.environ.get("WEB_ENABLED"), True)
    web_host: str = os.environ.get("WEB_HOST", "0.0.0.0")
    web_port: int = coerce_int(os.environ.get("WEB_PORT"), 8000)
    web_reload: bool = coerce_bool(os.environ.get("WEB_RELOAD"), False)
    web_token: str | None = os.environ.get("WEB_TOKEN")

    def __post_init__(self) -> None:
        if self.database_uri.startswith("sqlite:///"):
            raw_path = self.database_uri.split("sqlite:///")[-1]
            self.database_path = raw_path if raw_path else None
        else:
            self.database_path = None

    @property
    def mqtt_client_id(self) -> str:
        """Backwards compatible alias for the MQTT prefix."""
        return self.mqtt_prefix

    @property
    def mqtt_device_name(self) -> str:
        return self.instance_name


def get_settings() -> Settings:
    """Return singleton settings instance."""
    # Simple memoization using function attribute.
    if not hasattr(get_settings, "_instance"):
        get_settings._instance = Settings()  # type: ignore[attr-defined]
    return get_settings._instance  # type: ignore[attr-defined]
