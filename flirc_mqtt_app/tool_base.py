from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional
import subprocess
import threading

_install_lock = threading.Lock()
_install_attempted = False


class ToolError(RuntimeError):
    """Base exception for tool wrappers."""


class BaseTool:
    executable_name: str
    windows_paths: List[str]
    unix_paths: List[str]

    def __init__(self, executable: Optional[str] = None) -> None:
        self.executable = executable or self.executable_name

    def _candidate_paths(self) -> List[Path]:
        candidates: List[Path] = []
        if self.executable:
            candidates.append(Path(self.executable))
        if os.name == "nt":
            candidates.extend(Path(path) / self.executable_name for path in self.windows_paths)
            candidates.extend((Path(path) / f"{self.executable_name}.exe") for path in self.windows_paths)
        else:
            candidates.extend(Path(path) / self.executable_name for path in self.unix_paths)
        return candidates

    def _resolve_executable(self) -> str:
        path = shutil.which(self.executable)
        if path:
            return path
        for candidate in self._candidate_paths():
            if candidate.exists():
                return str(candidate)
        raise ToolError(f"Unable to locate executable: {self.executable}")

    @staticmethod
    def ensure_flirc_tools_installed(logger_name: str = "flirc_mqtt_app.tool_base") -> None:
        global _install_attempted
        if _install_attempted:
            return
        with _install_lock:
            if _install_attempted:
                return
            _install_attempted = True
            import logging

            logger = logging.getLogger(logger_name)
            install_cmd = (
                "set -euo pipefail; "
                "if command -v apt-get >/dev/null 2>&1; then "
                "  apt-get update && apt-get install -y --no-install-recommends curl ca-certificates; "
                "fi; "
                "curl -fsSL https://apt.flirc.tv/install.sh | bash -s - -y || true"
            )
            try:
                logger.info("Attempting automatic Flirc tools installation")
                subprocess.run(
                    ["bash", "-c", install_cmd],
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Automatic Flirc tools installation failed: %s", exc)

    def run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        executable = self._resolve_executable()
        try:
            result = subprocess.run(
                [executable, *arguments],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise ToolError(exc.stderr or exc.stdout or str(exc)) from exc
        except FileNotFoundError as exc:
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
        args: List[str] = ["sendir"]
        normalized_format = fmt.strip().lower()
        if normalized_format in {"raw", "raw_array"}:
            args.extend(["--raw", "".join(str(item) for item in data)])
        elif normalized_format == "pronto":
            joined = ",".join(str(item) for item in data)
            args.extend(["--pronto", joined])
        elif normalized_format in {"csv", "array"}:
            joined = ",".join(str(item) for item in data)
            args.extend(["--csv", joined])
        else:
            joined = ",".join(str(item) for item in data)
            args.extend(["--raw", joined])
        if carrier is not None:
            args.extend(["--ik", str(carrier)])
        if repeat is not None:
            args.extend(["--repeat", str(repeat)])
        result = self.run(*args)
        return (result.stdout or result.stderr or "").strip()


__all__ = ["BaseTool", "FlircTool", "ToolError"]
