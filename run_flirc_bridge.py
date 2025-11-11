from __future__ import annotations

import logging
from contextlib import suppress

import uvicorn

from flirc_bridge.application import BridgeRuntime
from flirc_bridge.config import get_settings


def _block_until_shutdown(runtime: BridgeRuntime) -> None:
    """Mimic the legacy wait_forever behaviour using await_shutdown."""
    with suppress(KeyboardInterrupt):
        while not runtime.await_shutdown(timeout=1.0):
            continue


def main() -> None:
    settings = get_settings()
    runtime = BridgeRuntime(settings)
    runtime.start()

    if not settings.web_enabled:
        logging.info("Web interface disabled; running without FastAPI server")
        _block_until_shutdown(runtime)
        return

    try:
        from flirc_bridge.web import create_app

        app = create_app(runtime=runtime)
    except Exception as exc:
        logging.exception("Failed to initialize web interface: %s", exc)
        _block_until_shutdown(runtime)
        return

    try:
        uvicorn.run(
            app,
            host=settings.web_host,
            port=settings.web_port,
            reload=settings.web_reload,
            log_level="info",
            log_config=None,
        )
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()
