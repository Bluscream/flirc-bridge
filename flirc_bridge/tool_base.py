from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Iterable, List, Optional


class ToolError(RuntimeError):
    """Base exception for tool wrappers."""


class BaseTool:
    executable_name: str
    logger: logging.Logger

    def __init__(self, executable: Optional[str] = None) -> None:
        self.executable = executable or self.executable_name
        self.logger = logging.getLogger(f"flirc_bridge.tool_base.{self.__class__.__name__}")
        if not self.executable:
            raise ToolError("Executable name must be provided")

    def _resolve_executable(self) -> str:
        resolved = shutil.which(self.executable)
        if not resolved:
            raise ToolError(f"Unable to locate executable in PATH: {self.executable}")
        return resolved

    def run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        executable = self._resolve_executable()
        try:
            self.logger.info("Running %s %s", executable, " ".join(arguments))
            result = subprocess.run(
                [executable, *arguments],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            output = exc.stderr or exc.stdout or ""
            self.logger.error(
                "Command failed (%s %s): %s",
                executable,
                " ".join(arguments),
                output.strip(),
            )
            raise ToolError(output or str(exc)) from exc
        except FileNotFoundError as exc:
            self.logger.error("Executable not found: %s", exc)
            raise ToolError(str(exc)) from exc
        return result

    def run_text(self, *arguments: str) -> str:
        result = self.run(*arguments)
        output = (result.stdout or "").strip()
        if not output and result.stderr:
            output = result.stderr.strip()
        return output


class FlircTool(BaseTool):
    """Common helpers for CLI tools that support Flirc-style commands."""

    def send_command(
        self,
        fmt: str,
        data: Iterable[str],
        carrier: Optional[int] = None,
        repeat: Optional[int] = None,
    ) -> str:
        def _quote(value: str) -> str:
            value = value or ""
            if value.startswith('"') and value.endswith('"'):
                return value
            return f'"{value}"'

        args: List[str] = ["sendir"]
        normalized_format = fmt.strip().lower()
        if normalized_format in {"raw", "raw_array"}:
            payload = "".join(str(item) for item in data)
            args.extend(["--raw", _quote(payload)])
        elif normalized_format == "pronto":
            joined = ",".join(str(item) for item in data)
            args.extend(["--pronto", _quote(joined)])
        elif normalized_format in {"csv", "array"}:
            joined = ",".join(str(item) for item in data)
            args.extend(["--csv", _quote(joined)])
        else:
            joined = ",".join(str(item) for item in data)
            args.extend(["--raw", _quote(joined)])
        if carrier is not None:
            args.extend(["--ik", str(carrier)])
        if repeat is not None:
            args.extend(["--repeat", str(repeat)])
        result = self.run(*args)
        return (result.stdout or result.stderr or "").strip()


__all__ = ["BaseTool", "FlircTool", "ToolError"]
