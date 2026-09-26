"""CLI entry point for vox."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import IO

LOCK_PATH = Path("/tmp/vox-daemon.lock")


def _acquire_instance_lock(path: Path = LOCK_PATH) -> IO[str] | None:
    """Hold an advisory lock for the life of the process; None if another instance has it."""
    import fcntl
    try:
        lock = open(path, "a")
    except OSError:
        return None
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return None
    return lock


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="vox",
        description="Voice-to-text daemon for Linux and macOS",
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

    # One instance at a time. Exit cleanly so launchd/systemd don't retry it, and before any permission prompt
    lock = _acquire_instance_lock()
    if lock is None:
        log.info("Vox is already running.")
        return

    from .config import load_config
    from .errors import ConfigError, DependencyError

    try:
        config = load_config(args.config)
    except ConfigError as e:
        log.error("%s", e)
        sys.exit(1)

    # Without a key Vox still starts: the menu asks for one, and a service exiting here would only be restarted
    from .keystore import KeystoreError, get_api_key, hide_env_override, migrate_plaintext
    hide_env_override()  # before Vox starts any process, so none inherits the key
    migrate_plaintext(config.config_path)
    try:
        config.openai_api_key = get_api_key()
    except KeystoreError as e:
        config.api_key_error = str(e)
        log.warning("Couldn't read the OpenAI API key from the keychain: %s", e)
    if config.uses_openai and not config.openai_api_key and config.api_key_error is None:
        log.warning("No OpenAI API key yet. Choose Set API Key… from the Vox menu.")
    if config.mode == "whisper_cpp":
        from .whisper_cpp import WhisperCppTranscriber
        try:
            WhisperCppTranscriber(config)
        except ConfigError as e:
            log.error("%s", e)
            sys.exit(1)

    # Check system dependencies
    from .injector import check_accessibility_permission, check_dependencies
    try:
        check_dependencies()
    except DependencyError as e:
        log.error("%s", e)
        sys.exit(1)

    # Check macOS permissions
    if sys.platform == "darwin" and not check_accessibility_permission(prompt=True):
        log.warning(
            "macOS Accessibility permission is not granted to this process.\n"
            "Global hotkeys and text injection require Accessibility permissions.\n"
            "Enable Accessibility in System Settings -> Privacy & Security -> Accessibility."
        )

    log.info("Starting vox daemon...")

    try:
        from .daemon import run
        run(config)
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    main()
