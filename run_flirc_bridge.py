from __future__ import annotations

import argparse
from typing import Sequence

import uvicorn

from flirc_mqtt_app.config import get_settings
from flirc_mqtt_app.mqtt_manager import clear_bridge_topics
from flirc_mqtt_app.web import create_app


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Flirc MQTT bridge web app.")
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear matching MQTT topics before starting the web app.",
    )
    parser.add_argument(
        "--exit",
        action="store_true",
        help="Exit after running the requested actions (e.g. --clear).",
    )
    parser.add_argument(
        "--collect-seconds",
        type=float,
        default=2.0,
        help="Seconds to allow retained topics to be delivered before clearing.",
    )
    parser.add_argument(
        "--include-non-retained",
        action="store_true",
        help="Also clear matching non-retained topics observed during collection.",
    )
    args = parser.parse_args(argv)

    settings = get_settings()

    if args.clear:
        cleared = clear_bridge_topics(
            settings,
            collect_seconds=args.collect_seconds,
            retain_only=not args.include_non_retained,
        )
        print(f"[MQTT] Finished clearing {len(cleared)} topic(s).")
        if args.exit:
            return
    elif args.exit:
        return

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
