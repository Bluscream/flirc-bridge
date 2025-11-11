from __future__ import annotations

import logging
import shutil
import subprocess
import os
from enum import Enum
from typing import Iterable, List, Optional


class ProtocolFormat(str, Enum):
    RAW = "raw"  # +8248 -1291 +212 ...
    CSV = "csv"  # 8248,1291,212,...or 0,8248...or 8248, 1291..
    PRONTO = "pronto"  # 0EDD AEBB


class ToolError(RuntimeError):
    """Base exception for tool wrappers."""


class BaseTool:
    executable_name: str
    logger: logging.Logger
    path: Optional[str]
    filesize: Optional[int]
    cache: dict
    stdout: List[str]
    stderr: List[str]

    def __init__(self, executable: Optional[str] = None) -> None:
        self.executable = executable or self.executable_name
        self.logger = logging.getLogger(f"flirc_bridge.tool.{self.__class__.__name__}")
        if not self.executable:
            raise ToolError("Executable name must be provided")
        self.path: Optional[str] = None
        self.filesize: Optional[int] = None
        self.cache: dict = {}
        self.stdout = []
        self.stderr = []

    def _resolve_executable(self) -> str:
        if self.path:
            return self.path

        resolved = shutil.which(self.executable)
        if not resolved:
            raise ToolError(f"Unable to locate executable in PATH: {self.executable}")

        directory, filename = os.path.split(resolved)
        if directory:
            try:
                entries = os.listdir(directory)
            except OSError:
                entries = []
            for entry in entries:
                if entry.lower() == filename.lower():
                    resolved = os.path.join(directory, entry)
                    break

        self.path = resolved
        try:
            self.filesize = os.path.getsize(resolved)
        except OSError:
            self.filesize = None

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
            if result.stdout:
                self.stdout.append(result.stdout)
            if result.stderr:
                self.stderr.append(result.stderr)
        except subprocess.CalledProcessError as exc:
            output = exc.stderr or exc.stdout or ""
            self.logger.error(
                "Command failed (%s %s): %s",
                executable,
                " ".join(arguments),
                output.strip(),
            )
            if exc.stdout:
                self.stdout.append(exc.stdout)
            if exc.stderr:
                self.stderr.append(exc.stderr)
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

    def send_ir(
        self,
        fmt: ProtocolFormat,
        data: Iterable[str],
        carrier: Optional[int] = None,
        repeat: Optional[int] = 1,
    ) -> str:
        args: List[str] = ["sendir"]
        normalized_format = fmt.value.lower()
        if normalized_format == ProtocolFormat.PRONTO.value:
            joined = ",".join(str(item) for item in data)
            args.append(f'--pronto="{joined}"') # -p, --pronto         send a pronto pattern
        elif normalized_format in {ProtocolFormat.CSV.value, "array"}:
            joined = ",".join(str(item) for item in data)
            args.append(f'--csv="{joined}"') # -c, --csv            8248,1291,212,...or 0,8248...or 8248, 1291..
        else:
            joined = "".join(str(item) for item in data)
            args.append(f'--raw="{joined}"') # -x, --raw            +8248 -1291 +212 ...
        if carrier is not None:
            args.append(f'--ik={carrier}') # -i, --ik             set the interkey delay between rep. frames
        if repeat is not None:
            args.append(f'--repeat={repeat}') # -r, --repeat         number of times to repeat pattern
        result = self.run(*args)
        return (result.stdout or result.stderr or "").strip()

    def stop_ir(self) -> None:
        self.run("sendir", "--kill")


__all__ = ["BaseTool", "FlircTool", "ToolError", "ProtocolFormat"]
