from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .config import Settings, get_settings
from .logging import configure_logging
from .database import Action, Device, get_session, init_db
from .flirc_util import FlircUtil
from .irtools import IRTools
from .mqtt import MQTTManager, clear_bridge_topics

logger = logging.getLogger(__name__)


def scrub_settings(settings_obj: Settings) -> dict:
    """Return a sanitized copy of settings for logging/display purposes."""
    data = asdict(settings_obj)
    for key in ("mqtt_password", "web_token"):
        if key in data and data[key]:
            data[key] = "***"
    return data


class BridgeRuntime:
    """Coordinates core services (database, MQTT, tooling) independent of the web UI."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._irtools: Optional[IRTools] = None
        self._flirc_util: Optional[FlircUtil] = None
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
        if self._irtools is None:
            raise RuntimeError("BridgeRuntime has not been started; IRTools unavailable")
        return self._irtools

    @property
    def flirc_util(self) -> FlircUtil:
        if self._flirc_util is None:
            raise RuntimeError("BridgeRuntime has not been started; FlircUtil unavailable")
        return self._flirc_util

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
                json.dumps(scrub_settings(self._settings), sort_keys=True),
            )

            self._irtools = IRTools()
            self._flirc_util = FlircUtil()

            self._start_mqtt_if_enabled()

            self._started = True
            self._shutdown.clear()

    def _start_mqtt_if_enabled(self) -> None:
        if not (self._settings.mqtt_enabled and self._settings.mqtt_broker):
            logger.info("MQTT disabled via configuration or missing broker settings")
            return

        cleared_topics = []
        if self._settings.mqtt_cleanup_enabled:
            try:
                cleared_topics = clear_bridge_topics(
                    self._settings,
                    collect_seconds=self._settings.mqtt_cleanup_collect_seconds,
                    retain_only=self._settings.mqtt_cleanup_retain_only,
                    match_substring=self._settings.mqtt_cleanup_match,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Failed to clear MQTT topics matching '%s': %s",
                    self._settings.mqtt_cleanup_match,
                    exc,
                )
            else:
                if cleared_topics:
                    logger.info(
                        "Cleared %s MQTT topic(s) matching '%s'",
                        len(cleared_topics),
                        self._settings.mqtt_cleanup_match,
                    )
                else:
                    logger.info(
                        "No MQTT topics matched cleanup filter '%s'",
                        self._settings.mqtt_cleanup_match,
                    )

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
                    with get_session() as session:
                        devices = session.query(Device).all()
                        for device in devices:
                            for action in device.actions:
                                self._mqtt_manager.clear_discovery(device, action)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("Failed to clear MQTT discovery topics: %s", exc)

                try:
                    self._mqtt_manager.stop()
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("Exception while stopping MQTT manager: %s", exc)
                finally:
                    self._mqtt_manager = None

            self._irtools = None
            self._flirc_util = None
            self._started = False

    def wait_forever(self) -> None:
        """Block the current thread until shutdown is requested."""
        try:
            while not self._shutdown.wait(timeout=1):
                pass
        except KeyboardInterrupt:
            logger.info("Interrupt received; shutting down runtime")
        finally:
            self.stop()


__all__ = ["BridgeRuntime", "configure_logging", "scrub_settings"]
