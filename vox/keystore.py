"""The OpenAI API key, kept in the operating system's keychain.

The macOS Keychain, or the Secret Service (GNOME Keyring, KWallet) on Linux,
encrypts the key at rest and unlocks it at login. Only when Linux has no
keyring at all does the key go to an owner-only plain-text file instead. An
``OPENAI_API_KEY`` environment variable overrides whatever is stored.

The key never goes into ``os.environ``, so processes Vox starts don't inherit it.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from pathlib import Path

import keyring
from keyring.backends import fail
from keyring.errors import PasswordDeleteError

from .config import DEFAULT_CONFIG_PATH, read_api_key_setting, remove_api_key_setting

log = logging.getLogger(__name__)

SERVICE = "vox"
USERNAME = "openai_api_key"
ENV_VAR = "OPENAI_API_KEY"
FALLBACK_PATH = DEFAULT_CONFIG_PATH.parent / ".env"


class KeystoreError(Exception):
    """A keychain exists but could not be read or written."""


def env_override() -> str:
    """The key set in Vox's environment, which wins over the stored one, or ""."""
    return os.environ.get(ENV_VAR, "").strip()


def has_keychain() -> bool:
    """Whether an encrypted keychain is available. keyring falls back to its fail backend only when none is."""
    return not isinstance(keyring.get_keyring(), fail.Keyring)


def storage_name() -> str | None:
    """Where a saved key is kept, in words; None when it would go to the unencrypted file."""
    backend = keyring.get_keyring()
    if isinstance(backend, fail.Keyring):
        return None
    for candidate in getattr(backend, "backends", None) or [backend]:  # a chainer tries its backends in order
        module = type(candidate).__module__
        if module.endswith(".macOS"):
            return "the macOS Keychain"
        if module.endswith(".SecretService"):
            return "your login keyring"
        if module.endswith(".kwallet"):
            return "KWallet"
    return "the system keyring"


def get_api_key() -> str:
    """The key Vox should use: the environment variable, else the stored key. Raises KeystoreError."""
    return env_override() or get_stored_key()


def get_stored_key() -> str:
    """The saved key, or "" if none. Raises KeystoreError when the keychain can't be read."""
    if not has_keychain():
        return _read_env_file(FALLBACK_PATH)
    try:
        return (keyring.get_password(SERVICE, USERNAME) or "").strip()
    except Exception as e:
        raise KeystoreError(_reason(e)) from e


def set_api_key(key: str) -> None:
    """Save the key. A keychain error is raised, never answered by writing plain text."""
    if not has_keychain():
        _write_env_key(FALLBACK_PATH, key)
        return
    try:
        keyring.set_password(SERVICE, USERNAME, key)
    except Exception as e:
        raise KeystoreError(_reason(e)) from e


def delete_api_key() -> None:
    if not has_keychain():
        _remove_env_key(FALLBACK_PATH)
        return
    try:
        keyring.delete_password(SERVICE, USERNAME)
    except PasswordDeleteError:
        pass  # nothing saved
    except Exception as e:
        raise KeystoreError(_reason(e)) from e


def migrate_plaintext(config_path: Path | None = None) -> None:
    """Move plain-text keys into the keychain, deleting each copy only once the keychain holds that key.

    Looks at config.toml's ``[api]`` first (it used to win), then the ``.env``
    file. A key that differs from the one already saved is left where it is,
    with a warning. Never raises.
    """
    if not has_keychain():
        return
    config_path = config_path or DEFAULT_CONFIG_PATH
    sources = (
        (config_path, read_api_key_setting, remove_api_key_setting),
        (FALLBACK_PATH, _read_env_file, _remove_env_key),
    )
    try:
        stored = (keyring.get_password(SERVICE, USERNAME) or "").strip()
        for path, read, remove in sources:
            key = read(path)
            if not key:
                continue
            if not stored:
                keyring.set_password(SERVICE, USERNAME, key)
                if (keyring.get_password(SERVICE, USERNAME) or "").strip() != key:
                    log.error("The keychain didn't return the key Vox saved; leaving %s as it is", _shown(path))
                    return
                stored = key
                log.info("Moved the OpenAI API key from %s into %s", _shown(path), storage_name())
            if key == stored:
                remove(path)
                log.info("Deleted the plain-text OpenAI API key from %s", _shown(path))
            else:
                log.warning(
                    "%s holds a different OpenAI API key from the one in %s. Vox uses the saved one; "
                    "delete the file's copy, or save it from Set API Key…",
                    _shown(path), storage_name(),
                )
    except Exception as e:
        log.warning("Couldn't move the plain-text OpenAI API key into the keychain: %s", _reason(e))


# -- The plain-text file ---------------------------------------------------------


def _is_key_line(line: str) -> bool:
    name, sep, _ = line.partition("=")
    return bool(sep) and name.strip() == ENV_VAR


def _read_env_file(path: Path) -> str:
    """The last ``OPENAI_API_KEY=`` value in a .env file, or ""."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return ""
    values = [line.partition("=")[2].strip().strip("\"'") for line in lines if _is_key_line(line)]
    return next((v for v in reversed(values) if v), "")


def _write_env_key(path: Path, key: str) -> None:
    lines = _other_lines(path)
    _write_private(path, [*lines, f"{ENV_VAR}={key}"])


def _remove_env_key(path: Path) -> None:
    """Drop the key's lines, deleting the file if nothing but blank lines would be left."""
    if not path.exists():
        return
    lines = _other_lines(path)
    if any(line.strip() for line in lines):
        _write_private(path, lines)
    else:
        path.unlink()


def _other_lines(path: Path) -> list[str]:
    try:
        return [line for line in path.read_text(encoding="utf-8").splitlines() if not _is_key_line(line)]
    except FileNotFoundError:
        return []


def _write_private(path: Path, lines: list[str]) -> None:
    """Replace ``path`` atomically with an owner-only (0600) file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")  # created 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def _shown(path: Path) -> str:
    return str(path).replace(str(Path.home()), "~", 1)


def _reason(error: Exception) -> str:
    return str(error) or type(error).__name__
