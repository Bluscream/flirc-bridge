from __future__ import annotations

import json
import threading
from typing import Callable, Dict, Optional

import paho.mqtt.client as mqtt

from .config import Settings, get_settings
from .database import Action, Device, Pattern
from .irtools import IRTools, IRToolsError


def _slugify(*parts: str) -> str:
    return "_".join(part.strip().lower().replace(" ", "_") for part in parts if part)


class MQTTManager:
    """Handles MQTT connectivity, discovery, and command dispatch."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        irtools: Optional[IRTools] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.irtools = irtools or IRTools()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, self.settings.mqtt_client_id)
        if self.settings.mqtt_username and self.settings.mqtt_password:
            self.client.username_pw_set(self.settings.mqtt_username, self.settings.mqtt_password)

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

        self._pattern_lookup: Dict[str, Callable[[], None]] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        self.client.connect_async(self.settings.mqtt_broker, self.settings.mqtt_port, keepalive=60)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    # ------------------------------------------------------------------ events
    def _on_connect(self, client: mqtt.Client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            print(f"[MQTT] Failed to connect (code={reason_code})")
            return
        print("[MQTT] Connected")
        base_topic = self._commands_topic()
        client.subscribe(f"{base_topic}/#")
        client.subscribe(f"{self.settings.mqtt_base_topic}/send")

    def _on_disconnect(self, client: mqtt.Client, userdata, flags, reason_code, properties) -> None:
        print(f"[MQTT] Disconnected (code={reason_code})")

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
            print(f"[MQTT] No handler registered for topic {topic}")

    # ---------------------------------------------------------------- utilities
    def _commands_topic(self) -> str:
        return f"{self.settings.mqtt_base_topic}/commands"

    def publish_discovery(self, device: Device, action: Action, pattern: Pattern) -> None:
        object_id = _slugify(device.name, action.name, pattern.format)
        topic = (
            f"{self.settings.mqtt_discovery_prefix}/button/{self.settings.mqtt_client_id}_{object_id}/config"
        )
        command_topic = f"{self._commands_topic()}/{object_id}"
        payload = {
            "name": f"{device.name} {action.name} ({pattern.format})",
            "command_topic": command_topic,
            "payload_press": "PRESS",
            "unique_id": f"{self.settings.mqtt_client_id}_{object_id}",
            "device": {
                "identifiers": [self.settings.mqtt_client_id],
                "manufacturer": "Flirc MQTT",
                "name": "Flirc MQTT Bridge",
            },
        }
        self.client.publish(topic, json.dumps(payload), retain=self.settings.mqtt_retain)

        command_topic = f"{self._commands_topic()}/{object_id}"

        pattern_format = pattern.format
        pattern_data = pattern.data
        device_name = device.name
        action_name = action.name

        def handler() -> None:
            try:
                self.irtools.send(pattern_format, json.loads(pattern_data))
                print(f"[MQTT] Sent pattern {device_name}/{action_name}/{pattern_format}")
            except (IRToolsError, json.JSONDecodeError) as exc:
                print(f"[MQTT] Failed to send pattern: {exc}")

        with self._lock:
            self._pattern_lookup[command_topic] = handler

    def clear_discovery(self, device: Device, action: Action) -> None:
        for pattern in action.patterns:
            object_id = _slugify(device.name, action.name, pattern.format)
            topic = (
                f"{self.settings.mqtt_discovery_prefix}/button/{self.settings.mqtt_client_id}_{object_id}/config"
            )
            self.client.publish(topic, "", retain=self.settings.mqtt_retain)
            command_topic = f"{self._commands_topic()}/{object_id}"
            with self._lock:
                self._pattern_lookup.pop(command_topic, None)

    def publish_all(self, records: Dict[str, Dict[str, Dict[str, str]]]) -> None:
        for device_name, actions in records.items():
            for action_name, formats in actions.items():
                for format_name, json_payload in formats.items():
                    dummy_device = Device(name=device_name)
                    dummy_action = Action(name=action_name, device=dummy_device)
                    dummy_pattern = Pattern(format=format_name, data=json_payload, action=dummy_action)
                    self.publish_discovery(dummy_device, dummy_action, dummy_pattern)

    def _handle_custom_payload(self, payload: str) -> None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            print("[MQTT] Custom payload must be valid JSON")
            return
        fmt = data.get("format")
        values = data.get("data")
        carrier = data.get("carrier")
        repeat = data.get("repeat")
        if not fmt or not values:
            print("[MQTT] Custom payload missing 'format' or 'data'")
            return
        try:
            items = [str(item) for item in values] if isinstance(values, list) else [str(values)]
            self.irtools.send(fmt, items, carrier=carrier, repeat=repeat)
            print("[MQTT] Sent custom pattern via MQTT")
        except IRToolsError as exc:
            print(f"[MQTT] Failed to send custom pattern: {exc}")


__all__ = ["MQTTManager"]
