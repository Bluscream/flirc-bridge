from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

import paho.mqtt.client as mqtt

from ..config import Settings, get_settings
from ..database import Action, Device, Pattern
from ..tool import IRTools, ToolError, send_ir_pattern
from ..utils import format_reason, slugify

logger = logging.getLogger(__name__)


class MQTTManager:
    """Handles MQTT connectivity, discovery, and command dispatch."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        irtools: Optional[IRTools] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.irtools = irtools or IRTools()
        self._prefix = self.settings.mqtt_prefix
        self._safe_prefix = self._prefix.replace("-", "_")
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, self._safe_prefix)
        self.client.enable_logger(logger.getChild("client"))
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        if self.settings.mqtt_username and self.settings.mqtt_password:
            self.client.username_pw_set(self.settings.mqtt_username, self.settings.mqtt_password)

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        self.client.on_log = self._on_log

        self._pattern_lookup: Dict[str, Callable[[], None]] = {}
        self._published_object_ids: Set[str] = set()
        self._lock = threading.Lock()
        self._connected = threading.Event()

    def start(self) -> None:
        logger.info(
            "Connecting to MQTT broker %s:%s as %s",
            self.settings.mqtt_broker,
            self.settings.mqtt_port,
            self._prefix,
        )
        try:
            self.client.connect(
                self.settings.mqtt_broker,
                self.settings.mqtt_port,
                keepalive=60,
            )
        except Exception as exc:
            logger.error("Failed to initiate MQTT connection: %s", exc)
            raise
        self.client.loop_start()
        if not self._connected.wait(timeout=10):
            logger.warning(
                "Timed out waiting for MQTT connection acknowledgement (broker=%s)",
                self.settings.mqtt_broker,
            )

    def stop(self) -> None:
        try: self.unpublish_all()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to unpublish MQTT discovery topics on stop: %s", exc)
        self._connected.clear()
        self.client.loop_stop()
        self.client.disconnect()
    def __del__(self) -> None:  # pragma: no cover - destructor is best-effort
        try:
            self.unpublish_all()
        except Exception:
            pass

    # ------------------------------------------------------------------ events
    def _on_connect(self, client: mqtt.Client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            logger.error(
                "MQTT connection refused: reason=%s, properties=%s",
                format_reason(reason_code),
                properties,
            )
            return
        self._connected.set()
        session_present = getattr(flags, "session_present", getattr(flags, "sessionPresent", None))
        logger.info("MQTT connected (session_present=%s)", session_present)
        client.subscribe(f"{self._button_prefix()}/#")
        client.subscribe(f"{self.settings.mqtt_base_topic}/send")

    def _on_disconnect(self, client: mqtt.Client, userdata, flags, reason_code, properties) -> None:
        self._connected.clear()
        logger.warning(
            "MQTT disconnected: reason=%s, properties=%s",
            format_reason(reason_code),
            properties,
        )

    def _on_log(self, client: mqtt.Client, userdata, level, buf) -> None:
        logger.debug("MQTT client log (level=%s): %s", level, buf)

    def _on_message(self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
        payload = msg.payload.decode("utf-8").strip()
        topic = msg.topic
        if topic == f"{self.settings.mqtt_base_topic}/send":
            self._handle_custom_payload(payload)
            return

        with self._lock:
            handler = self._pattern_lookup.get(topic)
        if handler:
            handler()
        else:
            if topic.startswith(self._command_prefix()) or topic.startswith(f"{self._button_prefix()}/"):
                return
            logger.debug("No MQTT handler registered for topic %s", topic)

    # ---------------------------------------------------------------- utilities
    def _button_prefix(self) -> str:
        return f"{self.settings.mqtt_discovery_prefix}/button"

    def _command_prefix(self) -> str:
        return f"{self._button_prefix()}/{self._safe_prefix}_"

    def _command_topic(self, object_id: str) -> str:
        return f"{self._button_prefix()}/{self._safe_prefix}_{object_id}"

    def _attributes_topic(self, object_id: str) -> str:
        return f"{self._command_topic(object_id)}/attributes"

    def _config_topic_for_object_id(self, object_id: str) -> str:
        return f"{self.settings.mqtt_discovery_prefix}/button/{self._safe_prefix}_{object_id}/config"

    def publish_discovery(self, device: Device, action: Action, patterns: List[Pattern]) -> None:
        if not patterns:
            return
        primary = patterns[0]
        object_id = self._object_suffix(action)
        topic = self._config_topic_for_object_id(object_id)
        command_topic = self._command_topic(object_id)
        payload = {
            "name": f"{device.name} {action.name}",
            "command_topic": command_topic,
            "payload_press": "PRESS",
            "unique_id": f"{self._safe_prefix}_{object_id}",
            "device": {
                "identifiers": [self._safe_prefix],
                "manufacturer": "Flirc",
                "name": self.settings.instance_name,
            },
            "json_attributes_topic": self._attributes_topic(object_id),
            "json_attributes_template": "{{ value_json | tojson }}",
        }
        self.client.publish(topic, json.dumps(payload), retain=self.settings.mqtt_retain)

        attributes_payload = {
            "device": {
                "id": device.id,
                "name": device.name,
            },
            "action": {
                "id": action.id,
                "name": action.name,
            },
            "primary_pattern": {
                "id": primary.id,
                "format": primary.format,
                "repeat": primary.repeat or 1,
                "ik": primary.ik or 23000,
                "hash": primary.hash,
                "updated_at": (primary.updated_at or primary.created_at or datetime.utcnow()).isoformat(),
            },
            "patterns": [
                {
                    "id": pattern.id,
                    "format": pattern.format,
                    "repeat": pattern.repeat or 1,
                    "ik": pattern.ik or 23000,
                    "hash": pattern.hash,
                }
                for pattern in patterns
            ],
        }
        self.client.publish(
            self._attributes_topic(object_id),
            json.dumps(attributes_payload),
            retain=self.settings.mqtt_retain,
        )

        def handler() -> None:
            for pattern in patterns:
                try:
                    payload_values = json.loads(pattern.data or "[]")
                    repeat_value = pattern.repeat or 1
                    ik_value = pattern.ik or 23000
                    send_ir_pattern(
                        pattern.format,
                        payload_values,
                        ik=ik_value,
                        repeat=repeat_value,
                        irtools=self.irtools,
                    )
                    logger.info(
                        "Sent pattern %s/%s (%s, id=%s)",
                        device.name,
                        action.name,
                        pattern.format,
                        pattern.id,
                    )
                except (ToolError, json.JSONDecodeError) as exc:
                    logger.error(
                        "Failed to send pattern %s/%s (%s, id=%s): %s",
                        device.name,
                        action.name,
                        pattern.format,
                        pattern.id,
                        exc,
                    )

        with self._lock:
            self._pattern_lookup[command_topic] = handler
            self._published_object_ids.add(object_id)

    def refresh_action(self, device: Device, action: Action) -> None:
        patterns = self._sorted_patterns(action)
        if not patterns:
            self.clear_discovery(device, action)
            return
        self.clear_discovery(device, action)
        self.publish_discovery(device, action, patterns)

    def clear_discovery(self, device: Device, action: Action) -> None:
        object_id = self._object_suffix(action)
        config_topic = self._config_topic_for_object_id(object_id)
        self.client.publish(config_topic, "", retain=True)
        self.client.publish(
            self._attributes_topic(object_id),
            "",
            retain=True,
        )
        command_topic = self._command_topic(object_id)
        with self._lock:
            self._pattern_lookup.pop(command_topic, None)
            self._published_object_ids.discard(object_id)

    def unpublish_all(self) -> None:
        """Remove retained discovery topics for all published actions."""
        with self._lock:
            object_ids = list(self._published_object_ids)
            self._pattern_lookup.clear()
            self._published_object_ids.clear()

        if not object_ids:
            return

        for object_id in object_ids:
            config_topic = self._config_topic_for_object_id(object_id)
            attributes_topic = self._attributes_topic(object_id)
            try:
                self.client.publish(config_topic, "", retain=True)
                self.client.publish(attributes_topic, "", retain=True)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Failed to unpublish discovery topic %s: %s", config_topic, exc)

    def publish_all(self, devices: List[Dict[str, Any]]) -> None:
        for device_payload in devices:
            device_name = device_payload.get("name", "Unknown Device")
            device_id = device_payload.get("id")
            dummy_device = Device(id=device_id, name=device_name)
            for action_payload in device_payload.get("actions", []):
                action_name = action_payload.get("name", "Unknown Action")
                action_id = action_payload.get("id") or str(uuid.uuid4())
                dummy_action = Action(id=action_id, name=action_name, device=dummy_device)
                for pattern_payload in action_payload.get("patterns", []):
                    pattern_id = pattern_payload.get("id") or str(uuid.uuid4())
                    data_values = pattern_payload.get("data") or []
                    data_string = json.dumps(data_values)
                    dummy_pattern = Pattern(
                        id=pattern_id,
                        format=pattern_payload.get("format", "raw"),
                        data=data_string,
                        action=dummy_action,
                        hash=pattern_payload.get("hash"),
                        repeat=pattern_payload.get("repeat") or 1,
                        ik=pattern_payload.get("ik") or 23000,
                    )
                    dummy_action.patterns.append(dummy_pattern)
                self.refresh_action(dummy_device, dummy_action)

    def _handle_custom_payload(self, payload: str) -> None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("Custom MQTT payload must be valid JSON")
            return
        fmt = data.get("format")
        values = data.get("data")
        carrier = data.get("carrier")
        ik = data.get("ik")
        repeat = data.get("repeat")
        if not fmt or not values:
            logger.warning("Custom MQTT payload missing 'format' or 'data'")
            return
        try:
            items = [str(item) for item in values] if isinstance(values, list) else [str(values)]
            ik_value = ik if ik is not None else carrier
            repeat_value = repeat if repeat is not None else 1
            send_ir_pattern(fmt, items, ik=ik_value, repeat=repeat_value, irtools=self.irtools)
            logger.info("Sent custom MQTT payload (%s)", fmt)
        except ToolError as exc:
            logger.error("Failed to send custom MQTT payload (%s): %s", fmt, exc)

    def _sorted_patterns(self, action: Action) -> List[Pattern]:
        return sorted(
            action.patterns,
            key=lambda pattern: (
                pattern.created_at or datetime.min,
                pattern.updated_at or datetime.min,
                pattern.id or "",
            ),
        )

    def _object_suffix(self, action: Action) -> str:
        if action.id:
            return action.id.replace("-", "_")
        return slugify(action.device.name if action.device else "device", action.name, str(uuid.uuid4())[:8])

    def _config_topic(self, device: Device, action: Action) -> str:
        object_id = self._object_suffix(action)
        return self._config_topic_for_object_id(object_id)


__all__ = ["MQTTManager"]
