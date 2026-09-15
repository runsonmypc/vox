"""CLI entry point for vox."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="vox",
        description="Voice-to-text daemon for Linux",
    )
    parser.add_argument(
        "--config", "-c",
        type=Path,
        default=None,
        help="Path to config file (default: ~/.config/vox/config.toml)",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available audio input devices and exit",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Silence noisy third-party loggers even in verbose mode
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    log = logging.getLogger("vox")

    if args.list_devices:
        try:
            import sounddevice as sd
            print(sd.query_devices())
        except Exception as e:
            log.error("Failed to query audio devices: %s", e)
            sys.exit(1)
        return

    from .config import load_config
    from .errors import ConfigError, DependencyError

    try:
        config = load_config(args.config)
    except ConfigError as e:
        log.error("%s", e)
        sys.exit(1)

    if not config.openai_api_key:
        log.error("OPENAI_API_KEY not set. Set it in config file or environment.")
        sys.exit(1)

    # Check system dependencies
    from .injector import check_dependencies
    try:
        check_dependencies()
    except DependencyError as e:
        log.error("%s", e)
        sys.exit(1)

    # Prevent multiple instances via advisory file lock
    import fcntl
    _lock_path = Path("/tmp/vox-daemon.lock")
    try:
        _lock_file = open(_lock_path, "a")
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        log.error("Another vox instance is already running.")
        sys.exit(1)

    log.info("Starting vox daemon...")

    from .daemon import run
    try:
        run(config)
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    main()
