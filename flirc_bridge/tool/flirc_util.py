from __future__ import annotations

import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Iterable, Optional

from .base import FlircTool, ToolError


def _parse_version_output(output: str, executable: Optional[str]) -> dict:
    info = {
        "tool": "flirc_util",
        "executable": executable,
        "raw": output,
        "version": None,
        "details": {},
    }
    lines = [line.strip() for line in output.replace("\r", "\n").split("\n") if line.strip()]
    if not lines:
        return info

    version_match = re.search(r"([0-9]+(?:\.[0-9]+)*)", lines[0])
    if version_match:
        info["version"] = version_match.group(1)

    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = key.strip().lower().replace(" ", "_")
        info["details"][normalized_key] = value.strip()

    return info


def _normalize(key: str) -> str:
    return key.strip().lower().replace(" ", "_").replace("-", "_")


class FlircUtilError(ToolError):
    """Raised when invoking flirc_util fails."""


class FlircUtil(FlircTool):
    """Wrapper around the flirc_util command line tool."""

    executable_name = "flirc_util"

    def __init__(self, executable: Optional[str] = None) -> None:
        super().__init__(executable or self.executable_name)
        self.version_output: Optional[str] = None
        self.settings_output: Optional[str] = None
        self.device_log_output: Optional[str] = None
        self.unit_test_output: Optional[dict] = None

    def help(self, command: Optional[str] = None) -> str:
        args = ["help"]
        if command:
            args.append(command)
        return self.run_text(*args)

    def settings(self) -> str:
        return self.run_text("settings")

    def version(self) -> str:
        return self.run_text("version")

    def version_info(self) -> dict:
        raw = self.version()
        executable = shutil.which(self.executable) or self.executable
        return _parse_version_output(raw, executable)

    def record(self, key: str) -> str:
        return self.run_text("record", key)

    def delete(self, key: Optional[str] = None) -> str:
        if key:
            return self.run_text("delete", key)
        return self.run_text("delete")

    def format(self) -> str:
        return self.run_text("format")

    def load_config(self, path: str) -> str:
        return self.run_text("loadconfig", path)

    def save_config(self, path: str) -> str:
        return self.run_text("saveconfig", path)

    def script(self, script_path: str) -> str:
        return self.run_text("script", script_path)

    def device_log(self) -> str:
        return self.run_text("device_log")

    def unit_test(self) -> subprocess.CompletedProcess[str]:
        return self.run("unit_test")

    @staticmethod
    def _parse_settings_output(raw: str) -> dict:
        info = {
            "raw": raw,
            "details": {},
            "settings": {},
            "recorded_keys": [],
        }
        lines = [line.rstrip() for line in raw.splitlines()]
        idx = 0
        # Version block
        if idx < len(lines) and lines[idx].strip():
            if ":" not in lines[idx]:
                info["version"] = lines[idx].strip()
                idx += 1
        while idx < len(lines) and lines[idx].strip():
            line = lines[idx]
            if ":" in line:
                key, value = line.split(":", 1)
                info["details"][_normalize(key)] = value.strip()
            idx += 1
        # Skip blank lines
        while idx < len(lines) and not lines[idx].strip():
            idx += 1
        # Settings block
        if idx < len(lines) and lines[idx].lower().startswith("settings"):
            idx += 1
            while idx < len(lines) and lines[idx].strip():
                line = lines[idx]
                if ":" in line:
                    key, value = line.split(":", 1)
                    info["settings"][_normalize(key)] = value.strip()
                idx += 1
        # Skip blank lines
        while idx < len(lines) and not lines[idx].strip():
            idx += 1
        # Recorded keys block
        if idx < len(lines) and lines[idx].lower().startswith("recorded keys"):
            idx += 1  # skip header label
            # Skip header rows (e.g., column titles and dashes)
            while idx < len(lines) and lines[idx].strip() and ("index" in lines[idx].lower() or set(lines[idx].strip()) == set("-")):
                idx += 1
            while idx < len(lines) and lines[idx].strip():
                parts = re.split(r"\s+", lines[idx].strip(), maxsplit=4)
                if len(parts) >= 5:
                    index_str, hash_str, ik_str, id_str, key_str = parts[0:5]
                    try:
                        index_val = int(index_str)
                    except ValueError:
                        index_val = index_str
                    try:
                        ik_val = int(ik_str)
                    except ValueError:
                        ik_val = ik_str
                    info["recorded_keys"].append(
                        {
                            "index": index_val,
                            "hash": hash_str,
                            "ik": ik_val,
                            "id": id_str,
                            "key": key_str,
                        }
                    )
                idx += 1
        return info

    def settings_info(self, raw: Optional[str] = None) -> dict:
        output = raw if raw is not None else self.run_text("settings")
        return self._parse_settings_output(output)

    def refresh_cache(self) -> dict:
        self._resolve_executable()

        with ThreadPoolExecutor(max_workers=4) as executor:
            version_future = executor.submit(self.version)
            settings_future = executor.submit(self.settings)
            device_log_future = executor.submit(self.device_log)
            unit_test_future = executor.submit(self.unit_test)

        version_output = version_future.result()
        settings_output = settings_future.result()
        device_log_output = device_log_future.result()

        try:
            unit_result = unit_test_future.result()
            unit_payload = {
                "returncode": unit_result.returncode,
                "stdout": (unit_result.stdout or "").strip(),
                "stderr": (unit_result.stderr or "").strip(),
            }
        except ToolError as exc:  # pragma: no cover - defensive
            unit_payload = {"error": str(exc)}

        settings_info = self.settings_info(raw=settings_output)
        version_info = _parse_version_output(version_output, self.path)

        cache = {
            "path": self.path,
            "filesize": self.filesize,
            "timestamp": datetime.utcnow().isoformat(),
            "version": version_output,
            "version_info": version_info,
            "settings_raw": settings_output,
            "settings_info": settings_info,
            "device_log": device_log_output,
            "unit_test": unit_payload,
        }

        self.version_output = version_output
        self.settings_output = settings_output
        self.device_log_output = device_log_output
        self.unit_test_output = unit_payload
        self.cache = cache
        return cache


__all__ = ["FlircUtil", "FlircUtilError"]
