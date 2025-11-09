from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional

from .config import get_settings


class IRToolsError(RuntimeError):
    """Raised when invoking irtools fails."""


class IRTools:
    """Wrapper around the irtools command line utility."""

    def __init__(self, executable: Optional[str] = None) -> None:
        settings = get_settings()
        self.executable = executable or settings.irtools_path

    def _resolve_executable(self) -> str:
        path = shutil.which(self.executable)
        if path:
            return path
        candidate = Path(self.executable)
        if candidate.exists():
            return str(candidate)
        raise IRToolsError(f"Unable to locate irtools executable: {self.executable}")

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
        executable = self._resolve_executable()
        args = [executable, "send", "--format", fmt]
        if carrier is not None:
            args.extend(["--carrier", str(carrier)])
        if repeat is not None:
            args.extend(["--repeat", str(repeat)])
        # Many irtools commands accept comma separated payloads.
        payload = ",".join(data)
        args.extend(["--data", payload])
        try:
            result = subprocess.run(
                args,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise IRToolsError(exc.stderr or exc.stdout or str(exc)) from exc
        return result.stdout.strip()

    def listen(
        self,
        fmt: str,
        timeout: int,
    ) -> tuple[List[str], str]:
        """
        Listen for incoming IR patterns via irtools.
        Returns the parsed data and the raw CLI output.
        """
        executable = self._resolve_executable()
        args = [
            executable,
            "listen",
            "--format",
            fmt,
            "--timeout",
            str(timeout),
        ]
        try:
            result = subprocess.run(
                args,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise IRToolsError(exc.stderr or exc.stdout or str(exc)) from exc

        output = result.stdout.strip()
        data: List[str]
        try:
            # Attempt JSON parse first – irtools can output JSON when supported.
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
            # Fallback to splitting by whitespace/newlines.
            data = [segment for segment in output.replace("\r", "\n").splitlines() if segment]
        return data, output


__all__ = ["IRTools", "IRToolsError"]
