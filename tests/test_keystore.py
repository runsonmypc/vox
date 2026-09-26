"""Tests for the API key store. Every test runs on conftest's in-memory keyring and uses dummy keys."""

import logging
import stat

import keyring
import pytest
from keyring.backends import fail
from keyring.errors import KeyringLocked

from vox import keystore
from vox.keystore import KeystoreError

KEY = "sk-test-dummy-0001"
OTHER = "sk-test-dummy-0002"


def _refuse(*_args):
    raise KeyringLocked("Failed to unlock the collection!")


@pytest.fixture
def locked(memory_keyring, monkeypatch):
    monkeypatch.setattr(memory_keyring, "get_password", _refuse)
    monkeypatch.setattr(memory_keyring, "set_password", _refuse)


@pytest.fixture
def forgetful(memory_keyring, monkeypatch):
    """A keychain that accepts a write but never returns it."""
    monkeypatch.setattr(memory_keyring, "set_password", lambda *_args: None)


def stored(backend):
    return backend.items.get((keystore.SERVICE, keystore.USERNAME))


@pytest.fixture
def no_keychain():
    keyring.set_keyring(fail.Keyring())


# -- Isolation ----------------------------------------------------------------------


def test_tests_never_reach_the_real_keychain(memory_keyring):
    assert keyring.get_keyring() is memory_keyring
    assert keystore.FALLBACK_PATH.name == "vox.env"


# -- Reading and writing ----------------------------------------------------------


def test_saved_key_is_kept_in_the_keychain_and_nowhere_else(memory_keyring):
    keystore.set_api_key(KEY)
    assert stored(memory_keyring) == KEY
    assert keystore.get_api_key() == KEY
    assert not keystore.FALLBACK_PATH.exists()


def test_no_key_reads_as_empty():
    assert keystore.get_api_key() == ""


def test_environment_variable_overrides_the_saved_key(monkeypatch):
    keystore.set_api_key(KEY)
    monkeypatch.setenv("OPENAI_API_KEY", f" {OTHER} ")
    assert keystore.get_api_key() == OTHER
    assert keystore.get_stored_key() == KEY


def test_delete_removes_the_saved_key(memory_keyring):
    keystore.set_api_key(KEY)
    keystore.delete_api_key()
    assert stored(memory_keyring) is None
    keystore.delete_api_key()  # deleting nothing is fine


def test_locked_keychain_raises_and_never_writes_plain_text(locked):
    with pytest.raises(KeystoreError, match="unlock"):
        keystore.set_api_key(KEY)
    with pytest.raises(KeystoreError):
        keystore.get_api_key()
    assert not keystore.FALLBACK_PATH.exists()


def test_without_a_keychain_the_key_goes_to_an_owner_only_file(no_keychain):
    keystore.FALLBACK_PATH.write_text("# mine\nOTHER_SETTING=1\nOPENAI_API_KEY=old\n")
    keystore.set_api_key(KEY)
    assert keystore.FALLBACK_PATH.read_text() == f"# mine\nOTHER_SETTING=1\nOPENAI_API_KEY={KEY}\n"
    assert stat.S_IMODE(keystore.FALLBACK_PATH.stat().st_mode) == 0o600
    assert keystore.get_api_key() == KEY

    keystore.delete_api_key()
    assert keystore.FALLBACK_PATH.read_text() == "# mine\nOTHER_SETTING=1\n"
    assert keystore.get_api_key() == ""


def test_deleting_the_only_line_deletes_the_file(no_keychain):
    keystore.set_api_key(KEY)
    keystore.delete_api_key()
    assert not keystore.FALLBACK_PATH.exists()


def test_env_file_reads_quoted_values_and_the_last_assignment(no_keychain):
    keystore.FALLBACK_PATH.write_text(f"OPENAI_API_KEY=first\n OPENAI_API_KEY = '{KEY}'\nOPENAI_API_KEY_OLD=x\n")
    assert keystore.get_stored_key() == KEY


def test_storage_names(memory_keyring):
    assert keystore.storage_name() == "the system keyring"
    keyring.set_keyring(fail.Keyring())
    assert keystore.storage_name() is None
    for module, name in (("keyring.backends.macOS", "the macOS Keychain"),
                         ("keyring.backends.SecretService", "your login keyring"),
                         ("keyring.backends.kwallet", "KWallet")):
        backend = type("Backend", (type(memory_keyring),), {"__module__": module})()
        keyring.set_keyring(backend)
        assert keystore.storage_name() == name


# -- Moving plain-text keys -------------------------------------------------------


@pytest.fixture
def cfg(tmp_path):
    return tmp_path / "config.toml"


def test_env_file_key_moves_into_the_keychain_and_the_file_goes(memory_keyring, cfg, caplog):
    keystore.FALLBACK_PATH.write_text(f"OPENAI_API_KEY={KEY}\n")
    with caplog.at_level(logging.INFO, logger="vox.keystore"):
        keystore.migrate_plaintext(cfg)
    assert stored(memory_keyring) == KEY
    assert not keystore.FALLBACK_PATH.exists()
    assert KEY not in caplog.text


def test_env_file_keeps_its_other_lines(memory_keyring, cfg):
    keystore.FALLBACK_PATH.write_text(f"OTHER_SETTING=1\nOPENAI_API_KEY={KEY}\n")
    keystore.migrate_plaintext(cfg)
    assert keystore.FALLBACK_PATH.read_text() == "OTHER_SETTING=1\n"


def test_config_toml_key_moves_and_the_rest_of_the_file_is_kept(memory_keyring, cfg):
    cfg.write_text(f'# my settings\n[api]\n# my key\nopenai_api_key = "{KEY}"\n\n[hotkey]\nkey = "f8"  # mine\n')
    keystore.migrate_plaintext(cfg)
    assert stored(memory_keyring) == KEY
    text = cfg.read_text()
    assert KEY not in text
    assert text.startswith("# my settings\n[api]\n# my key\n")
    assert 'key = "f8"  # mine' in text


def test_config_toml_drops_an_api_table_left_empty(cfg):
    cfg.write_text(f'[api]\nopenai_api_key = "{KEY}"\n\n[hotkey]\nkey = "f8"\n')
    keystore.migrate_plaintext(cfg)
    assert cfg.read_text().lstrip() == '[hotkey]\nkey = "f8"\n'


def test_different_plain_text_key_is_left_in_place_with_a_warning(memory_keyring, cfg, caplog):
    memory_keyring.set_password(keystore.SERVICE, keystore.USERNAME, KEY)
    keystore.FALLBACK_PATH.write_text(f"OPENAI_API_KEY={OTHER}\n")
    with caplog.at_level(logging.WARNING, logger="vox.keystore"):
        keystore.migrate_plaintext(cfg)
    assert stored(memory_keyring) == KEY
    assert keystore.FALLBACK_PATH.read_text() == f"OPENAI_API_KEY={OTHER}\n"
    assert "different OpenAI API key" in caplog.text
    assert OTHER not in caplog.text


def test_matching_plain_text_key_is_deleted(memory_keyring, cfg):
    memory_keyring.set_password(keystore.SERVICE, keystore.USERNAME, KEY)
    keystore.FALLBACK_PATH.write_text(f"OPENAI_API_KEY={KEY}\n")
    keystore.migrate_plaintext(cfg)
    assert not keystore.FALLBACK_PATH.exists()


def test_config_toml_wins_and_a_different_env_file_key_stays(memory_keyring, cfg):
    cfg.write_text(f'[api]\nopenai_api_key = "{KEY}"\n')
    keystore.FALLBACK_PATH.write_text(f"OPENAI_API_KEY={OTHER}\n")
    keystore.migrate_plaintext(cfg)
    assert stored(memory_keyring) == KEY
    assert keystore.FALLBACK_PATH.exists()


def test_nothing_is_deleted_when_the_keychain_does_not_keep_the_key(forgetful, cfg):
    keystore.FALLBACK_PATH.write_text(f"OPENAI_API_KEY={KEY}\n")
    keystore.migrate_plaintext(cfg)
    assert keystore.FALLBACK_PATH.read_text() == f"OPENAI_API_KEY={KEY}\n"


def test_nothing_is_deleted_and_nothing_raises_when_the_keychain_is_locked(locked, cfg):
    keystore.FALLBACK_PATH.write_text(f"OPENAI_API_KEY={KEY}\n")
    keystore.migrate_plaintext(cfg)
    assert keystore.FALLBACK_PATH.read_text() == f"OPENAI_API_KEY={KEY}\n"


def test_without_a_keychain_the_plain_text_file_is_the_store(no_keychain, cfg):
    keystore.FALLBACK_PATH.write_text(f"OPENAI_API_KEY={KEY}\n")
    keystore.migrate_plaintext(cfg)
    assert keystore.get_api_key() == KEY
