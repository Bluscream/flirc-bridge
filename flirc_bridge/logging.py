from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


def configure_logging(log_path: Optional[str]) -> None:
    """Configure root logging so messages go to both console and optional log file."""
    root_logger = logging.getLogger()
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Ensure console handler is present exactly once.
    has_console_handler = False
    for handler in root_logger.handlers:
        if isinstance(handler, logging.StreamHandler) and getattr(handler, "_flirc_console", False):
            has_console_handler = True
            if handler.formatter is None:
                handler.setFormatter(formatter)
            break
    if not has_console_handler:
        console_handler = logging.StreamHandler()
        console_handler._flirc_console = True  # type: ignore[attr-defined]
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    if log_path:
        abs_path = Path(log_path).expanduser().resolve()
        abs_path.parent.mkdir(parents=True, exist_ok=True)

        has_file_handler = False
        for handler in root_logger.handlers:
            if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == abs_path:
                has_file_handler = True
                if handler.formatter is None:
                    handler.setFormatter(formatter)
                break
        if not has_file_handler:
            file_handler = logging.FileHandler(abs_path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
            logger.info("Now logging to %s", abs_path)

    if root_logger.level == logging.WARNING:
        root_logger.setLevel(logging.INFO)

    # Ensure uvicorn loggers reuse the root handlers so messages land in both destinations.
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(logger_name)
        if uvicorn_logger is root_logger:
            continue
        # Remove direct handlers so records propagate to the root logger.
        for handler in list(uvicorn_logger.handlers):
            uvicorn_logger.removeHandler(handler)
        uvicorn_logger.propagate = True
        if uvicorn_logger.level == logging.NOTSET or uvicorn_logger.level > root_logger.level:
            uvicorn_logger.setLevel(root_logger.level)


__all__ = ["configure_logging"]
