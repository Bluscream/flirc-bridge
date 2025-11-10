from __future__ import annotations

import logging

import uvicorn

from flirc_bridge.application import BridgeRuntime
from flirc_bridge.config import get_settings


def main() -> None:
    settings = get_settings()
    runtime = BridgeRuntime(settings)
    runtime.start()

    if not settings.web_enabled:
        logging.info("Web interface disabled; running without FastAPI server")
        runtime.wait_forever()
        return

    try:
        from flirc_bridge.web import create_app

        app = create_app(runtime=runtime)
    except Exception as exc:
        logging.exception("Failed to initialize web interface: %s", exc)
        runtime.wait_forever()
        return

    uvicorn.run(
        app,
        host=settings.web_host,
        port=settings.web_port,
        reload=settings.web_reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
