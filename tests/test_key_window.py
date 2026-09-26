"""Tests for the API key window: the shared model, then the macOS and Linux views built on it.

Every test runs on conftest's in-memory keyring with dummy keys, and never calls OpenAI.
"""

from unittest.mock import MagicMock, patch

import keyring
import openai
import pytest
from keyring.backends import fail

from vox import keystore
from vox.ui import key_model
from vox.ui.key_model import CheckResult, KeyModel, Outcome, check_key

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


def test_storage_text_names_the_keychain_or_warns():
    model = loaded()
    assert model.encrypted
    assert model.storage_text == "Vox keeps your key in the system keyring, which encrypts it."
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


# -- macOS --------------------------------------------------------------------------


@pytest.fixture
def mac_window(appkit):
    """Build (never show) the macOS window: checks run inline, and confirmations are answered yes."""
    from vox.ui.mac.key import KeyController

    made = []

    def build():
        controller = KeyController.alloc().initWithModel_(loaded())
        controller.background = lambda work, done: done(work())
        controller.confirmed = []

        def confirm(window, title, message, button, destructive, then):
            controller.confirmed.append(title)
            then()

        controller.confirm = confirm
        made.append(controller)
        return controller

    yield build
    for controller in made:
        controller.window.close()


def checking(appkit, controller, on):
    controller.check_box.setState_(appkit.NSControlStateValueOn if on else appkit.NSControlStateValueOff)


def test_mac_checks_with_openai_by_default(appkit, memory_keyring, mac_window):
    assert mac_window().check_box.state() == appkit.NSControlStateValueOn


def test_mac_saves_the_key_and_closes(appkit, memory_keyring, mac_window):
    window = mac_window()
    checking(appkit, window, False)
    window.secure_field.setStringValue_(f" {KEY} ")
    window.save_(None)
    assert stored(memory_keyring) == KEY
    assert window.closed


def test_mac_blank_key_is_refused(appkit, memory_keyring, mac_window):
    window = mac_window()
    window.save_(None)
    assert window.status.stringValue() == "Paste your OpenAI API key."
    assert stored(memory_keyring) is None
    assert not window.closed


def test_mac_accepted_key_is_saved(appkit, memory_keyring, mac_window):
    window = mac_window()
    checking(appkit, window, True)
    window.secure_field.setStringValue_(KEY)
    with patch("vox.ui.mac.key.check_key", return_value=CheckResult(Outcome.ACCEPTED)) as check:
        window.save_(None)
    check.assert_called_once_with(KEY)
    assert stored(memory_keyring) == KEY
    assert window.closed


def test_mac_rejected_key_is_not_saved(appkit, memory_keyring, mac_window):
    window = mac_window()
    checking(appkit, window, True)
    window.secure_field.setStringValue_(KEY)
    with patch("vox.ui.mac.key.check_key", return_value=CheckResult(Outcome.REJECTED, "OpenAI didn’t accept this key.")):
        window.save_(None)
    assert stored(memory_keyring) is None
    assert window.status.stringValue() == "OpenAI didn’t accept this key."
    assert window.save_button.isEnabled()
    assert not window.closed


def test_mac_unchecked_key_can_be_saved_anyway(appkit, memory_keyring, mac_window):
    window = mac_window()
    checking(appkit, window, True)
    window.secure_field.setStringValue_(KEY)
    with patch("vox.ui.mac.key.check_key", return_value=CheckResult(Outcome.UNCHECKED, "Vox couldn’t reach OpenAI.")):
        window.save_(None)
    assert window.confirmed == ["Couldn’t Check the Key"]
    assert stored(memory_keyring) == KEY


def test_mac_show_swaps_in_a_plain_field(appkit, memory_keyring, mac_window):
    window = mac_window()
    checking(appkit, window, False)
    window.secure_field.setStringValue_(KEY)
    window.show_box.setState_(appkit.NSControlStateValueOn)
    window.toggleShow_(None)
    assert window.secure_field.isHidden() and not window.plain_field.isHidden()
    assert window.plain_field.stringValue() == KEY
    window.plain_field.setStringValue_(OTHER)
    window.save_(None)
    assert stored(memory_keyring) == OTHER


def test_mac_remove_is_offered_only_for_a_saved_key(appkit, memory_keyring, mac_window):
    assert mac_window().remove_button.isHidden()
    keystore.set_api_key(KEY)
    window = mac_window()
    assert not window.remove_button.isHidden()
    assert window.saved.stringValue() == "A key ending in 0001 is saved."
    window.remove_(None)
    assert window.confirmed == ["Remove the Saved Key?"]
    assert stored(memory_keyring) is None
    assert window.closed


def test_mac_keychain_error_keeps_the_window_open(appkit, memory_keyring, mac_window):
    window = mac_window()
    checking(appkit, window, False)
    window.secure_field.setStringValue_(KEY)
    with patch.object(KeyModel, "save", side_effect=keystore.KeystoreError("User interaction is not allowed.")), \
         patch("vox.ui.mac.kit.alert") as alert:
        window.save_(None)
    alert.assert_called_once()
    assert alert.call_args.args[1] == "Couldn’t Save the Key"
    assert not window.closed


def test_mac_warns_when_the_key_would_not_be_encrypted(appkit, mac_window):
    keyring.set_keyring(fail.Keyring())
    window = mac_window()
    assert "without encryption" in window.storage.stringValue()


# -- Linux --------------------------------------------------------------------------


def _drain(gtk):
    context = gtk.GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


@pytest.fixture
def gtk_window(gtk):
    """Build the Linux window: checks run inline, and confirmations are answered yes."""
    from vox.ui.gtk.key import KeyWindow

    made = []

    def build():
        window = KeyWindow(loaded())
        window.background = lambda work, done: done(work())
        window.confirmed = []

        def confirm(parent, title, message, button, destructive, then):
            window.confirmed.append(title)
            then()

        window.confirm = confirm
        made.append(window)
        return window

    yield build
    for window in made:
        window.destroy()
    _drain(gtk)


def test_gtk_checks_with_openai_by_default(gtk, memory_keyring, gtk_window):
    assert gtk_window().check_row.get_active()


def test_gtk_saves_the_key_and_closes(gtk, memory_keyring, gtk_window):
    window = gtk_window()
    window.check_row.set_active(False)
    window.entry.set_text(f" {KEY} ")
    window.save()
    assert stored(memory_keyring) == KEY
    assert window.closed


def test_gtk_blank_key_is_refused(gtk, memory_keyring, gtk_window):
    window = gtk_window()
    window.save()
    assert window.status.get_label() == "Paste your OpenAI API key."
    assert window.status.has_css_class("error")
    assert not window.closed


def test_gtk_rejected_key_is_not_saved(gtk, memory_keyring, gtk_window):
    window = gtk_window()
    window.check_row.set_active(True)
    window.entry.set_text(KEY)
    with patch("vox.ui.gtk.key.check_key", return_value=CheckResult(Outcome.REJECTED, "OpenAI didn’t accept this key.")):
        window.save()
    assert stored(memory_keyring) is None
    assert window.status.get_label() == "OpenAI didn’t accept this key."
    assert window.save_button.get_sensitive()
    assert not window.closed


def test_gtk_unchecked_key_can_be_saved_anyway(gtk, memory_keyring, gtk_window):
    window = gtk_window()
    window.check_row.set_active(True)
    window.entry.set_text(KEY)
    with patch("vox.ui.gtk.key.check_key", return_value=CheckResult(Outcome.UNCHECKED, "Vox couldn’t reach OpenAI.")):
        window.save()
    assert window.confirmed == ["Couldn’t Check the Key"]
    assert stored(memory_keyring) == KEY


def test_gtk_remove_is_offered_only_for_a_saved_key(gtk, memory_keyring, gtk_window):
    assert not gtk_window().remove_button.get_visible()
    keystore.set_api_key(KEY)
    window = gtk_window()
    assert window.remove_button.get_visible()
    assert window.saved_row.get_title() == "A key ending in 0001 is saved."
    window.remove()
    assert window.confirmed == ["Remove the Saved Key?"]
    assert stored(memory_keyring) is None
    assert window.closed


def test_gtk_keychain_error_keeps_the_window_open(gtk, memory_keyring, gtk_window):
    window = gtk_window()
    window.check_row.set_active(False)
    window.entry.set_text(KEY)
    with patch.object(KeyModel, "save", side_effect=keystore.KeystoreError("Failed to unlock the collection!")), \
         patch("vox.ui.gtk.key.error_dialog") as error:
        window.save()
    error.assert_called_once()
    assert error.call_args.args[1] == "Couldn’t Save the Key"
    assert not window.closed


def test_gtk_warns_when_the_key_would_not_be_encrypted(gtk, gtk_window):
    assert not gtk_window().banner.get_revealed()
    keyring.set_keyring(fail.Keyring())
    window = gtk_window()
    assert window.banner.get_revealed()
    assert "without encryption" in window.storage.get_label()
