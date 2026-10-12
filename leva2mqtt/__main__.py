"""Entry point: python -m leva2mqtt [--config PATH] [--healthcheck]"""
import argparse
import asyncio
import logging
import os
import sys
import time

from . import __version__
from .bridge import HEALTH_FILE, Bridge
from .config import ConfigError, load


def healthcheck() -> int:
    try:
        age = time.time() - os.path.getmtime(HEALTH_FILE)
    except OSError:
        return 1
    return 0 if age < 30 else 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="leva2mqtt", description="ito / leva! bridge")
    ap.add_argument("--config", help="path to config.yaml (default: $LEVA2MQTT_CONFIG or /data/config.yaml)")
    ap.add_argument("--healthcheck", action="store_true", help="exit 0 when the bridge is healthy")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args()
    if a.healthcheck:
        return healthcheck()
    try:
        cfg = load(a.config)
    except ConfigError as e:
        print(f"configuration error: {e}", file=sys.stderr)
        return 2
    logging.basicConfig(level=getattr(logging, cfg.log_level, logging.INFO),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("leva2mqtt").info("leva2mqtt %s", __version__)
    asyncio.run(Bridge(cfg).run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
