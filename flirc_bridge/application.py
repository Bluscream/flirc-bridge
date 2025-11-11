from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict
from typing import Optional

from .config import Settings, get_settings
from .logging import configure_logging
from .database import Action, Device, Pattern, get_session, init_db
from .tool import (
    FlircUtil,
    IRTools,
    get_flirc_util,
    get_irtools,
    initialize_tools,
)
from .mqtt import MQTTManager
from .utils import scrub_dict

logger = logging.getLogger(__name__)


class BridgeRuntime:
    """Coordinates core services (database, MQTT, tooling) independent of the web UI."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._mqtt_manager: Optional[MQTTManager] = None
        self._shutdown = threading.Event()
        self._started = False
        self._lock = threading.Lock()

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def started(self) -> bool:
        return self._started

    @property
    def irtools(self) -> IRTools:
        return get_irtools()

    @property
    def flirc_util(self) -> FlircUtil:
        return get_flirc_util()

    @property
    def mqtt_manager(self) -> Optional[MQTTManager]:
        return self._mqtt_manager

    def start(self) -> None:
        """Initialize core services and start MQTT if configured."""
        with self._lock:
            if self._started:
                return

            init_db()

            configure_logging(self._settings.log_file)

            logger.info(
                "Starting bridge runtime with settings: %s",
                json.dumps(
                    scrub_dict(
                        asdict(self._settings),
                        [self._settings.mqtt_password, self._settings.web_token],
                    ),
                    sort_keys=True,
                ),
            )

            with get_session() as session:
                device_count = session.query(Device).count()
                action_count = session.query(Action).count()
                pattern_count = session.query(Pattern).count()
            logger.info(
                "Database inventory: devices=%s actions=%s patterns=%s",
                device_count,
                action_count,
                pattern_count,
            )

            initialize_tools()

            self._start_mqtt_if_enabled()

            self._started = True
            self._shutdown.clear()

    def _start_mqtt_if_enabled(self) -> None:
        if not (self._settings.mqtt_enabled and self._settings.mqtt_broker):
            logger.info("MQTT disabled via configuration or missing broker settings")
            return

        try:
            manager = MQTTManager(settings=self._settings, irtools=self.irtools)
            manager.start()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("MQTT disabled due to startup error: %s", exc)
            self._mqtt_manager = None
            return

        self._mqtt_manager = manager
        published = self._republish_discovery()
        logger.info("Republished MQTT discovery for %s actions", published)

    def _republish_discovery(self) -> int:
        if self._mqtt_manager is None:
            return 0

        published = 0
        try:
            with get_session() as session:
                devices = session.query(Device).all()
                for device in devices:
                    for action in device.actions:
                        self._mqtt_manager.refresh_action(device, action)
                        published += 1
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to republish MQTT discovery: %s", exc)
        return published

    def stop(self) -> None:
        """Stop background services gracefully."""
        with self._lock:
            if not self._started:
                return

            self._shutdown.set()

            if self._mqtt_manager is not None:
                try:
                    self._mqtt_manager.stop()
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("Exception while stopping MQTT manager: %s", exc)
                finally:
                    self._mqtt_manager = None

            self._started = False

    def reset_mqtt_discovery(self) -> int:
        if not self._mqtt_manager:
            raise RuntimeError("MQTT manager is not running")
        cleared = self._mqtt_manager.clear_discovery()
        republished = self._republish_discovery()
        return cleared + republished

    def await_shutdown(self, timeout: Optional[float] = None) -> bool:
        return self._shutdown.wait(timeout)


__all__ = ["BridgeRuntime", "configure_logging", "scrub_dict"]
