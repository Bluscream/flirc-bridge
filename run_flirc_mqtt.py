from __future__ import annotations

import uvicorn

from flirc_mqtt_app.config import get_settings
from flirc_mqtt_app.web import create_app


def main() -> None:
    settings = get_settings()
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.web_host,
        port=settings.web_port,
        reload=settings.web_reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
