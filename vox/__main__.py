"""CLI entry point for vox."""

from __future__ import annotations

import argparse
import importlib.metadata
import logging
import os
import stat
import sys
from pathlib import Path

# A startup problem only the user can fix, such as a missing system tool (sysexits EX_CONFIG), so restarting
# can't help: vox.service has RestartPreventExitStatus=78. launchd has no such setting and retries every 10 s.
# A broken config.toml is not one of these: Vox starts without recording and picks up the fixed file.
EXIT_CANNOT_START = os.EX_CONFIG

# launchd appends Vox's output to one log file for good; past this size, a new start begins it afresh
_LOG_LIMIT_BYTES = 10 * 1024 * 1024


def _lock_dir() -> Path:
    """Vox's per-user lock directory, where no cleaner deletes old files."""
    if sys.platform != "darwin":
        # The same runtime directory however Vox was started, even without XDG_RUNTIME_DIR set
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
        if runtime.is_absolute() and runtime.is_dir():
            return runtime / "vox"
    return Path.home() / ".local" / "state" / "vox"


def _acquire_instance_lock(directory: Path) -> int | None:
    """Lock ``directory`` for the life of the process; None if another instance holds it. Raises OSError.

    The lock is on the directory rather than on a file in it, so deleting a file can't let a second
    Vox start next to the first, as happened with the old /tmp/vox-daemon.lock.
    """
    import fcntl

    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise
    return fd


def _clear_big_log(fd: int = 2, limit: int = _LOG_LIMIT_BYTES) -> int | None:
    """Empty the log file ``fd`` (stderr) writes to when it has grown past ``limit``; returns the size it had.

    Only a regular file is touched, such as the LaunchAgent's ~/Library/Logs/Vox/vox.log; journald is not.
    """
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= limit:
            return None
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)  # launchd appends anyway; a plain redirection would leave a hole
    except OSError:
        return None
    return info.st_size


def _version() -> str:
    try:
        return importlib.metadata.version("vox")
    except importlib.metadata.PackageNotFoundError:  # run from a checkout that was never installed
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="vox",
        description="Voice-to-text daemon for Linux and macOS",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
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
    lock_dir = _lock_dir()
    try:
        lock = _acquire_instance_lock(lock_dir)
    except OSError as e:
        log.error("Couldn't lock %s to keep Vox to one instance: %s", lock_dir, e)
        sys.exit(EXIT_CANNOT_START)
    if lock is None:
        log.info("Vox is already running.")
        return
    cleared = _clear_big_log()  # only now: a second Vox must not empty the running one's log
    if cleared is not None:
        log.info("Cleared the log file, which had grown to %.0f MiB", cleared / 1024 / 1024)

    from .config import fallback_config, load_config
    from .errors import ConfigError, DependencyError

    try:
        config = load_config(args.config)
    except ConfigError as e:
        # Exiting would only get Vox restarted into the same error; it waits for the fixed file instead
        config = fallback_config(args.config, e)
        log.error("%s. Vox won't record until the file is fixed, and loads it as soon as it is.", e)

    # Without a key Vox still starts: the menu asks for one, and a service exiting here would only be restarted
    from .keystore import KeystoreError, get_api_key, hide_env_override, migrate_plaintext
    hide_env_override()  # before Vox starts any process, so none inherits the key
    migrate_plaintext(config.config_path)
    try:
        config.openai_api_key = get_api_key()
    except KeystoreError as e:
        config.api_key_error = str(e)
        log.warning("Couldn't read the OpenAI API key from the keychain: %s", e)
    no_key = config.uses_openai and not config.openai_api_key and config.api_key_error is None
    if no_key and config.config_error is None:
        log.warning("No OpenAI API key yet. Choose Set API Key… from the Vox menu.")
    if config.mode == "whisper_cpp":
        from .modes import mode_problem

        # Start anyway, like without a key: the menu can switch modes, and exiting would only get Vox restarted
        config.mode_error = mode_problem(config, config.mode)
        if config.mode_error is not None:
            log.error("Local transcription can't run: %s. Choose another mode from the Vox menu.", config.mode_error)

    # Check system dependencies
    from .injector import check_accessibility_permission, check_dependencies
    try:
        check_dependencies()
    except DependencyError as e:
        log.error("%s", e)
        sys.exit(EXIT_CANNOT_START)

    # Check macOS permissions
    if sys.platform == "darwin" and not check_accessibility_permission(prompt=True):
        log.warning(
            "macOS Accessibility permission is not granted to this process.\n"
            "Global hotkeys and text injection require Accessibility permissions.\n"
            "Enable Accessibility in System Settings -> Privacy & Security -> Accessibility."
        )

    log.info("Starting vox %s daemon...", _version())

    try:
        from .daemon import run
        run(config)
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    main()
