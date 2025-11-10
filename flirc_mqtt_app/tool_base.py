from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional


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
