"""The API key's model; the key entry in Settings is tested in test_settings_window.py.

Every test runs on conftest's in-memory keyring with dummy keys, and never calls OpenAI.
"""

from unittest.mock import MagicMock, patch

import keyring
import openai
import pytest
from keyring.backends import fail

from vox import keystore
from vox.ui.key_model import BAD_CHARACTER, CheckResult, KeyModel, Outcome, check_key

KEY = "sk-test-dummy-0001"
OTHER = "sk-test-dummy-0002"


def stored(backend):
    return backend.items.get((keystore.SERVICE, keystore.USERNAME))


def loaded():
    model = KeyModel()
    model.reload()
    return model


# -- Model ----------------------------------------------------------------------


def test_no_saved_key():
    model = loaded()
    assert not model.has_saved_key
    assert model.saved_text == "No key saved yet."
    assert model.override_text is None


def test_saved_key_shows_only_its_last_four_characters(memory_keyring):
    keystore.set_api_key(KEY)
    model = loaded()
    assert model.saved_text == "A key ending in 0001 is saved."
    assert KEY[:-4] not in model.saved_text


def test_save_trims_and_replaces(memory_keyring):
    model = loaded()
    model.save(f"  {KEY}\n")
    assert stored(memory_keyring) == KEY
    model.save(OTHER)
    assert stored(memory_keyring) == OTHER
    assert model.saved_suffix == "0002"


def test_remove(memory_keyring):
    keystore.set_api_key(KEY)
    model = loaded()
    model.remove()
    assert stored(memory_keyring) is None
    assert not model.has_saved_key


def test_problems_with_a_typed_key():
    assert KeyModel.problem("  ") == "Paste your OpenAI API key."
    assert "spaces" in KeyModel.problem("sk-abc def")
    assert "spaces" in KeyModel.problem("sk-abc\ndef")
    assert KeyModel.problem(f" {KEY} ") is None


@pytest.mark.parametrize("key", [
    "sk-test-dummy\u200b0001",  # zero-width space, as copied from a web page or chat
    "\ufeffsk-test-dummy-0001",  # byte-order mark
    "sk-test\u2013dummy-0001",  # en dash
    "\u201csk-test-dummy-0001\u201d",  # curly quotes
    "sk-test-dummy\x7f0001",  # a control character
])
def test_key_with_a_character_outside_printable_ascii_is_refused(key):
    assert KeyModel.problem(key) == BAD_CHARACTER


def test_storage_text_names_the_keychain_or_warns():
    model = loaded()
    assert model.encrypted
    assert model.storage_text == "Vox Transfer keeps your key in the system keyring, which encrypts it."
    keyring.set_keyring(fail.Keyring())
    assert not model.encrypted
    assert "without encryption" in model.storage_text
    assert keystore.FALLBACK_PATH.name in model.storage_text


def test_environment_override_is_explained(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    assert "OPENAI_API_KEY" in loaded().override_text


def test_unreadable_keychain_is_reported(memory_keyring, monkeypatch):
    def refuse(*_args):
        raise RuntimeError("Failed to unlock the collection!")

    monkeypatch.setattr(memory_keyring, "get_password", refuse)
    model = loaded()
    assert model.saved_text == "Couldn’t read the saved key: Failed to unlock the collection!"
    assert not model.has_saved_key


# -- Checking with OpenAI -----------------------------------------------------------


def _error(cls, **attrs):
    """An OpenAI exception without its HTTP request and response."""
    error = cls.__new__(cls)
    Exception.__init__(error, "dummy")
    for name, value in attrs.items():
        setattr(error, name, value)
    return error


@pytest.mark.parametrize(("error", "outcome", "words"), [
    (None, Outcome.ACCEPTED, ""),
    (_error(openai.AuthenticationError, status_code=401), Outcome.REJECTED, "didn’t accept"),
    (_error(openai.PermissionDeniedError, status_code=403), Outcome.UNCHECKED, "isn’t allowed to list models"),
    (_error(openai.APITimeoutError), Outcome.UNCHECKED, "couldn’t reach OpenAI"),
    (_error(openai.InternalServerError, status_code=503), Outcome.UNCHECKED, "error 503"),
    (UnicodeEncodeError("ascii", "Bearer sk-\u200b", 10, 11, "ordinal not in range(128)"), Outcome.REJECTED,
     "isn’t part of an OpenAI key"),
])
def test_check_key(error, outcome, words):
    client = MagicMock()
    if error is not None:
        client.models.list.side_effect = error
    with patch("openai.OpenAI", return_value=client) as make:
        result = check_key(f" {KEY} ")
    make.assert_called_once_with(api_key=KEY, max_retries=0, timeout=15.0)
    client.models.list.assert_called_once_with()
    assert result.outcome is outcome
    assert words in result.message
    assert KEY not in result.message


def test_check_rejects_a_key_the_client_cannot_send(monkeypatch):
    """The real client fails to put the key in a header before connecting (to a closed local port anyway)."""
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    result = check_key("sk-test-dummy\u200b0001")
    assert result.outcome is Outcome.REJECTED
    assert result.message == BAD_CHARACTER


def test_unchecked_result_asks_to_save_anyway():
    result = CheckResult(Outcome.UNCHECKED, "Vox Transfer couldn’t reach OpenAI to check the key.")
    assert result.save_anyway_question == "Vox Transfer couldn’t reach OpenAI to check the key. Save it anyway?"
