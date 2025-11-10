from __future__ import annotations

import json
import os
import re
import shutil
from typing import Iterable, List, Optional

from .config import get_settings
from .tool_base import FlircTool, ToolError


class IRToolsError(ToolError):
    """Raised when invoking irtools fails."""


def _parse_version_output(tool: str, output: str, executable: Optional[str] = None) -> dict:
    info = {
        "tool": tool,
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


class IRTools(FlircTool):
    executable_name = "irtools"
    windows_paths = [
        r"C:\\Program Files (x86)\\Flirc",
        r"C:\\Program Files\\Flirc",
        r"C:\\Program Files\\Flirc Software",
    ]
    unix_paths = [
        "/usr/local/bin",
        "/usr/bin",
        "/opt/flirc/bin",
    ]

    def __init__(
        self,
        executable: Optional[str] = None,
    ) -> None:
        settings = get_settings()
        candidate = executable or settings.irtools_path
        resolved = shutil.which(candidate)
        if not resolved:
            fallback = settings.flirc_util_path
            fallback_resolved = shutil.which(fallback)
            if fallback_resolved:
                candidate = fallback_resolved
            else:
                candidate = fallback
        else:
            candidate = resolved
        super().__init__(candidate)

    def help(self, command: Optional[str] = None) -> str:
        args: List[str] = ["help"]
        if command:
            args.append(command)
        result = self.run(*args)
        return (result.stdout or result.stderr or "").strip()

    def script(self, script_path: str) -> str:
        result = self.run("script", script_path)
        return (result.stdout or result.stderr or "").strip()

    def decode(self, fmt: str, data: Iterable[str]) -> str:
        payload = ",".join(str(item) for item in data)
        result = self.run("decode", "--format", fmt, "--data", payload)
        return (result.stdout or result.stderr or "").strip()

    def send(
        self,
        fmt: str,
        data: Iterable[str],
        carrier: Optional[int] = None,
        repeat: Optional[int] = None,
    ) -> str:
        """
        Transmit an IR pattern using irtools.
        """
        return self.send_command(fmt, data, carrier=carrier, repeat=repeat)

    def listen(
        self,
        fmt: str,
        timeout: int,
    ) -> tuple[List[str], str]:
        """
        Listen for incoming IR patterns via irtools.
        Returns the parsed data and the raw CLI output.
        """
        result = self.run("listen", "--format", fmt, "--timeout", str(timeout))

        output = result.stdout.strip()
        data: List[str]
        try:
            parsed = json.loads(output)
            if isinstance(parsed, dict) and "data" in parsed:
                raw_values = parsed["data"]
            else:
                raw_values = parsed
            if isinstance(raw_values, list):
                data = [str(item) for item in raw_values]
            else:
                data = [str(raw_values)]
        except json.JSONDecodeError:
            data = [segment for segment in output.replace("\r", "\n").splitlines() if segment]
        return data, output

    def version(self) -> str:
        """
        Return the raw output of `irtools version`, falling back to `--version`
        when the subcommand is unavailable.
        """
        primary_error: Optional[ToolError] = None
        try:
            result = self.run("version")
        except ToolError as exc:  # pragma: no cover - defensive
            primary_error = exc
            try:
                result = self.run("--version")
            except ToolError:
                raise primary_error
        output = (result.stdout or "").strip()
        if not output and result.stderr:
            output = result.stderr.strip()
        return output

    def version_info(self) -> dict:
        """
        Return structured information about the irtools binary.
        """
        output = self.version()
        executable = os.path.exists(self.executable) or self.executable
        return _parse_version_output("irtools", output, executable)


__all__ = ["IRTools", "IRToolsError"]
