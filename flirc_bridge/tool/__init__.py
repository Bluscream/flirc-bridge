from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Iterable, Optional, Union

from .base import BaseTool, FlircTool, ToolError, ProtocolFormat
FORMAT_ALIASES = {
    "raw": ProtocolFormat.RAW,
    "raw_array": ProtocolFormat.RAW,
    "csv": ProtocolFormat.CSV,
    "array": ProtocolFormat.CSV,
    "pronto": ProtocolFormat.PRONTO,
}
from .flirc_util import FlircUtil, FlircUtilError
from .irtools import IRTools, IRToolsError

_IRTOOLS: Optional[IRTools] = None
_FLIRC_UTIL: Optional[FlircUtil] = None
_TOOL_CACHE: dict = {}
_TOOL_LOCK = threading.Lock()
_TOOLS_INITIALIZED = False


def initialize_tools(refresh: bool = False) -> dict:
    """Initialize tooling and cache metadata."""
    global _TOOLS_INITIALIZED, _TOOL_CACHE, _IRTOOLS, _FLIRC_UTIL
    with _TOOL_LOCK:
        if not refresh and _TOOLS_INITIALIZED:
            return _TOOL_CACHE

        irtools = _IRTOOLS or IRTools()
        flirc_util = _FLIRC_UTIL or FlircUtil()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                executor.submit(irtools.refresh_cache): "irtools",
                executor.submit(flirc_util.refresh_cache): "flirc_util",
            }
            cache: dict = {}
            for future, key in futures.items():
                cache[key] = future.result()

        cache["generated_at"] = datetime.utcnow().isoformat()
        _IRTOOLS = irtools
        _FLIRC_UTIL = flirc_util
        _TOOL_CACHE = cache
        _TOOLS_INITIALIZED = True
        return _TOOL_CACHE


def get_tool_cache(refresh: bool = False) -> dict:
    """Return cached tool metadata, optionally refreshing it."""
    return initialize_tools(refresh=refresh)


def get_irtools() -> IRTools:
    initialize_tools()
    return _IRTOOLS


def get_flirc_util() -> FlircUtil:
    initialize_tools()
    return _FLIRC_UTIL


def send_ir_pattern(
    fmt: Union[ProtocolFormat, str],
    data: Iterable[str],
    *,
    carrier: Optional[int] = None,
    repeat: Optional[int] = None,
    irtools: Optional[IRTools] = None,
    flirc_util: Optional[FlircUtil] = None,
):
    """Attempt to send an IR pattern via irtools, falling back to flirc_util."""

    last_error: Optional[Exception] = None
    if isinstance(fmt, str):
        fmt_enum = FORMAT_ALIASES.get(fmt.lower(), ProtocolFormat.RAW)
    else:
        fmt_enum = fmt

    ir = irtools or get_irtools()
    try:
        ir.stop_ir()
        ir_output = ir.send_ir(fmt_enum, data, carrier=carrier, repeat=repeat)
        return {"tool": "irtools", "output": ir_output, "fallback": False}
    except Exception as exc:
        last_error = exc

    fu = flirc_util or get_flirc_util()
    try:
        fu.stop_ir()
        flirc_output = fu.send_ir(
            data,
            fmt=fmt_enum,
            carrier=carrier,
            repeat=repeat,
        )
        return {"tool": "flirc_util", "output": flirc_output, "fallback": True, "irtools_error": str(last_error)}
    except Exception as flirc_exc:
        raise ToolError(f"Both irtools and flirc_util failed: irtools={last_error}; flirc_util={flirc_exc}") from flirc_exc

__all__ = [
    "BaseTool",
    "FlircTool",
    "ToolError",
    "FlircUtil",
    "FlircUtilError",
    "IRTools",
    "IRToolsError",
    "ProtocolFormat",
    "initialize_tools",
    "get_tool_cache",
    "get_irtools",
    "get_flirc_util",
    "send_ir_pattern",
]
