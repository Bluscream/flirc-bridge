from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from typing import Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


class FlircError(RuntimeError):
    """Base exception for flirc tooling."""


class IRToolsError(FlircError):
    """Raised when invoking irtools fails."""


class FlircUtilError(FlircError):
    """Raised when invoking flirc_util fails."""


def _resolve(executable: str) -> str:
    resolved = shutil.which(executable)
    if not resolved:
        raise FlircError(f"Executable '{executable}' not found in PATH")
    return resolved


def _run(
    executable: str,
    args: Sequence[str],
    error_cls: type[FlircError],
) -> subprocess.CompletedProcess[str]:
    resolved = _resolve(executable)
    try:
        logger.info("Running %s %s", resolved, " ".join(args))
        return subprocess.run(
            [resolved, *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        output = exc.stderr or exc.stdout or ""
        logger.error(
            "Command failed (%s %s): %s",
            resolved,
            " ".join(args),
            output.strip(),
        )
        raise error_cls(output or str(exc)) from exc
    except FileNotFoundError as exc:
        logger.error("Executable not found: %s", exc)
        raise error_cls(str(exc)) from exc


def _run_text(
    executable: str,
    args: Sequence[str],
    error_cls: type[FlircError],
) -> str:
    result = _run(executable, args, error_cls)
    output = (result.stdout or "").strip()
    if not output and result.stderr:
        output = result.stderr.strip()
    return output


def _quote(value: str) -> str:
    value = value or ""
    if value.startswith('"') and value.endswith('"'):
        return value
    return f'"{value}"'


def _parse_version_output(
    tool: str,
    output: str,
    executable: Optional[str] = None,
) -> dict:
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


# --------------------------------------------------------------------------- irtools
def irtools_send(
    fmt: str,
    data: Iterable[str],
    carrier: Optional[int] = None,
    repeat: Optional[int] = None,
) -> str:
    normalized_format = (fmt or "").strip().lower()
    args: List[str] = ["sendir"]

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

    result = _run("irtools", args, IRToolsError)
    output = (result.stdout or result.stderr or "").strip()
    if "must specify a pattern" in output.lower():
        raise IRToolsError(output)
    return output


def irtools_listen(fmt: str, timeout: int) -> Tuple[List[str], str]:
    args = ["listen", "--format", fmt, "--timeout", str(timeout)]
    result = _run("irtools", args, IRToolsError)
    output = (result.stdout or "").strip()
    if not output and result.stderr:
        output = result.stderr.strip()

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


def irtools_version() -> str:
    try:
        return _run_text("irtools", ["version"], IRToolsError)
    except IRToolsError:
        return _run_text("irtools", ["--version"], IRToolsError)


def irtools_version_info() -> dict:
    output = irtools_version()
    executable = shutil.which("irtools")
    return _parse_version_output("irtools", output, executable)


# ------------------------------------------------------------------------ flirc_util
def _flatten_pattern(pattern: Iterable[str]) -> List[str]:
    flattened: List[str] = []
    for item in pattern:
        if isinstance(item, (list, tuple)):
            flattened.extend(str(elem) for elem in item)
        else:
            flattened.append(str(item))
    return flattened


def flirc_send_ir(pattern: Iterable[str]) -> str:
    entries = _flatten_pattern(pattern)
    payload = "".join(entries)
    result = _run("flirc_util", ["sendir", "--raw", _quote(payload)], FlircUtilError)
    return (result.stdout or result.stderr or "").strip()


def flirc_device_log() -> str:
    return _run_text("flirc_util", ["device_log"], FlircUtilError)


def flirc_unit_test() -> subprocess.CompletedProcess[str]:
    return _run("flirc_util", ["unit_test"], FlircUtilError)


def flirc_version() -> str:
    return _run_text("flirc_util", ["version"], FlircUtilError)


def flirc_version_info() -> dict:
    output = flirc_version()
    executable = shutil.which("flirc_util")
    return _parse_version_output("flirc_util", output, executable)


def flirc_settings() -> str:
    return _run_text("flirc_util", ["settings"], FlircUtilError)


def _normalize(key: str) -> str:
    return key.strip().lower().replace(" ", "_").replace("-", "_")


def flirc_settings_info() -> dict:
    raw = flirc_settings()
    info = {
        "raw": raw,
        "details": {},
        "settings": {},
        "recorded_keys": [],
    }
    lines = [line.rstrip() for line in raw.splitlines()]
    idx = 0

    if idx < len(lines) and lines[idx].strip() and ":" not in lines[idx]:
        info["version"] = lines[idx].strip()
        idx += 1

    while idx < len(lines) and lines[idx].strip():
        line = lines[idx]
        if ":" in line:
            key, value = line.split(":", 1)
            info["details"][_normalize(key)] = value.strip()
        idx += 1

    while idx < len(lines) and not lines[idx].strip():
        idx += 1

    if idx < len(lines) and lines[idx].lower().startswith("settings"):
        idx += 1
        while idx < len(lines) and lines[idx].strip():
            line = lines[idx]
            if ":" in line:
                key, value = line.split(":", 1)
                info["settings"][_normalize(key)] = value.strip()
            idx += 1

    while idx < len(lines) and not lines[idx].strip():
        idx += 1

    if idx < len(lines) and lines[idx].lower().startswith("recorded keys"):
        idx += 1
        while idx < len(lines) and lines[idx].strip() and (
            "index" in lines[idx].lower() or set(lines[idx].strip()) == {"-"}
        ):
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


__all__ = [
    "FlircError",
    "IRToolsError",
    "FlircUtilError",
    "irtools_send",
    "irtools_listen",
    "irtools_version",
    "irtools_version_info",
    "flirc_send_ir",
    "flirc_device_log",
    "flirc_unit_test",
    "flirc_version",
    "flirc_version_info",
    "flirc_settings",
    "flirc_settings_info",
]
