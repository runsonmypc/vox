"""Tests for the vocabulary window: the shared model, then the macOS and Linux views built on it."""

import sys
import tomllib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vox.config import load_config
from vox.errors import ConfigError
from vox.ui.vocab_model import VocabModel

CONFIG = """\
# my settings
dictionary = ["FastAPI"]

[snippets]
"my email" = "alex@example.com"
"""


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(CONFIG)
    return path


def loaded(path):
    model = VocabModel(path)
    model.reload()
    return model


# -- Model ----------------------------------------------------------------------


def test_reads_vocabulary_and_snippets(cfg):
    model = loaded(cfg)
    assert model.words == ["FastAPI"]
    assert model.snippets == {"my email": "alex@example.com"}
    assert model.load_error is None


def test_add_words_accepts_a_comma_separated_list_and_skips_known_ones(cfg):
    model = loaded(cfg)
    assert model.add_words(" Kubernetes, fastapi ,PostgreSQL\nKubernetes ") == ["Kubernetes", "PostgreSQL"]
    assert model.words == ["FastAPI", "Kubernetes", "PostgreSQL"]
    assert load_config(cfg).dictionary == model.words
    assert cfg.read_text().startswith("# my settings")


def test_blank_input_writes_nothing(cfg):
    model = loaded(cfg)
    before = cfg.read_text()
    assert model.add_words(" , \n") == []
    assert cfg.read_text() == before


def test_remove_words(cfg):
    model = loaded(cfg)
    model.add_words("Kubernetes, PostgreSQL")
    model.remove_words(["FastAPI", "PostgreSQL"])
    assert model.words == ["Kubernetes"]
    assert load_config(cfg).dictionary == ["Kubernetes"]


def test_save_snippet_adds_and_keeps_new_lines(cfg):
    model = loaded(cfg)
    model.save_snippet("sign off", "Best,\nAlex")
    assert load_config(cfg).snippets == {"my email": "alex@example.com", "sign off": "Best,\nAlex"}


def test_editing_a_trigger_renames_the_snippet(cfg):
    model = loaded(cfg)
    model.save_snippet("work email", "alex@work.example", original="my email")
    assert model.snippets == {"work email": "alex@work.example"}

    model.save_snippet("Work Email", "alex@work.example", original="work email")  # same trigger to the daemon
    assert model.snippets == {"Work Email": "alex@work.example"}


def test_conflict_names_the_snippet_a_save_would_replace(cfg):
    model = loaded(cfg)
    model.save_snippet("sign off", "Best")
    assert model.conflict("My Email!") == "my email"
    assert model.conflict("my email", original="my email") is None
    assert model.conflict("my email", original="sign off") == "my email"
    assert model.conflict("something new") is None
    assert model.conflict("  ") is None


def test_invalid_snippet_raises_and_leaves_the_file_alone(cfg):
    model = loaded(cfg)
    before = cfg.read_text()
    with pytest.raises(ValueError):
        model.save_snippet("empty", "   ")
    assert cfg.read_text() == before


def test_remove_snippet(cfg):
    model = loaded(cfg)
    model.remove_snippet("my email")
    assert model.snippets == {}
    assert tomllib.loads(cfg.read_text())["snippets"] == {}


def test_broken_config_is_reported_not_overwritten(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[snippets\n")
    model = loaded(path)
    assert model.load_error and "Failed to parse" in model.load_error
    with pytest.raises(ConfigError, match="Failed to parse"):
        model.add_words("Vox")
    assert path.read_text() == "[snippets\n"


def test_missing_config_file_is_created(tmp_path):
    path = tmp_path / "vox" / "config.toml"
    model = loaded(path)
    assert model.words == [] and model.snippets == {}
    model.add_words("Vox")
    assert load_config(path).dictionary == ["Vox"]


def test_shown_path_abbreviates_home():
    from pathlib import Path

    assert VocabModel(Path.home() / ".config" / "vox" / "config.toml").shown_path == "~/.config/vox/config.toml"


# -- macOS --------------------------------------------------------------------------


@pytest.fixture
def mac_window(appkit, cfg):
    from vox.ui.mac.vocab import VocabController

    controller = VocabController.alloc().initWithModel_(VocabModel(cfg))
    yield controller
    controller.close_editor()
    controller.window.close()


def mac_rows(table):
    return table.numberOfRows()


def test_mac_lists_words_and_snippets(appkit, mac_window):
    assert mac_rows(mac_window.words_table) == 1
    assert mac_rows(mac_window.snippets_table) == 1
    assert mac_window.words_footer.stringValue().startswith("Saved to ")
    assert mac_window.word_field.isEnabled()


def test_mac_add_words_saves_selects_and_clears(appkit, cfg, mac_window):
    mac_window.word_field.setStringValue_("Kubernetes, PostgreSQL")
    mac_window.addWord_(None)
    assert load_config(cfg).dictionary == ["FastAPI", "Kubernetes", "PostgreSQL"]
    assert mac_rows(mac_window.words_table) == 3
    assert list(mac_window.words_table.selectedRowIndexes()) == [1, 2]
    assert mac_window.word_field.stringValue() == ""
    assert mac_window.words_footer.stringValue().startswith("Added 2 words.")


def test_mac_remove_word_from_its_row(appkit, cfg, mac_window):
    cell = mac_window.words_table.viewAtColumn_row_makeIfNecessary_(0, 0, True)
    mac_window.removeWord_(cell.accessory)
    assert load_config(cfg).dictionary == []
    assert mac_window.words_table.enclosingScrollView().isHidden()
    assert not mac_window.words_empty[0].isHidden()


def _key(AppKit, window, chars, code, flags=0, repeat=False):
    return AppKit.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
        AppKit.NSEventTypeKeyDown, (0, 0), flags, 0, window.windowNumber(), None, chars, chars, repeat, code
    )


def test_mac_delete_key_removes_the_selection_once(appkit, cfg, mac_window):
    mac_window.model.add_words("a, b, c")
    mac_window.render()
    window, table = mac_window.window, mac_window.words_table
    window.makeFirstResponder_(table)
    table.selectRowIndexes_byExtendingSelection_(appkit.NSIndexSet.indexSetWithIndex_(1), False)  # "a"

    assert mac_window.handle_key(_key(appkit, window, "\x7f", 51)) is None
    assert load_config(cfg).dictionary == ["FastAPI", "b", "c"]
    assert table.selectedRowIndexes().count() == 0  # nothing left selected for a second press to remove

    table.selectRowIndexes_byExtendingSelection_(appkit.NSIndexSet.indexSetWithIndex_(1), False)  # "b"
    assert mac_window.handle_key(_key(appkit, window, "\x7f", 51, repeat=True)) is None
    assert load_config(cfg).dictionary == ["FastAPI", "b", "c"]  # key repeat removes nothing


def test_mac_escape_while_an_input_method_composes_keeps_the_window(appkit, mac_window):
    window = mac_window.window
    window.makeFirstResponder_(mac_window.word_field)
    editor = mac_window.word_field.currentEditor()
    if editor is None:
        pytest.skip("The field needs a field editor")
    editor.setMarkedText_selectedRange_replacementRange_("かな", (2, 0), (0, 0))
    event = _key(appkit, window, "\x1b", 53)
    assert mac_window.handle_key(event) is event  # the input method gets Escape; the window stays open


def test_mac_command_n_opens_a_new_snippet_on_a_cyrillic_layout(appkit, mac_window):
    command = appkit.NSEventModifierFlagCommand
    assert mac_window.handle_key(_key(appkit, mac_window.window, "т", 45, command)) is None  # Cyrillic te on N
    assert mac_window.editor is not None


def test_mac_snippet_editor_adds_a_multiline_snippet(appkit, cfg, mac_window):
    mac_window.open_editor(None)
    editor = mac_window.editor
    assert not editor.save_button.isEnabled()
    editor.trigger.setStringValue_("sign off")
    editor.expansion.setString_("Best,\nAlex")
    editor.validate()
    assert editor.save_button.isEnabled()
    editor.save_(None)
    assert load_config(cfg).snippets["sign off"] == "Best,\nAlex"
    assert mac_window.editor is None
    assert mac_rows(mac_window.snippets_table) == 2


def test_mac_snippet_editor_warns_before_replacing_and_renames(appkit, cfg, mac_window):
    mac_window.open_editor(None)
    editor = mac_window.editor
    editor.trigger.setStringValue_("My Email")
    editor.validate()
    assert editor.warning.stringValue() == "This replaces your “my email” snippet."
    mac_window.close_editor()

    mac_window.open_editor("my email")
    editor = mac_window.editor
    assert editor.expansion.string() == "alex@example.com"
    editor.trigger.setStringValue_("work email")
    editor.validate()
    assert editor.warning.stringValue() == ""
    editor.save_(None)
    assert load_config(cfg).snippets == {"work email": "alex@example.com"}


def test_mac_save_error_keeps_the_editor_open(appkit, cfg, mac_window):
    mac_window.open_editor(None)
    editor = mac_window.editor
    editor.trigger.setStringValue_("x")
    editor.expansion.setString_("y")
    editor.validate()
    with patch.object(VocabModel, "save_snippet", side_effect=OSError("disk full")), \
         patch("vox.ui.mac.kit.alert") as alert:
        editor.save_(None)
    alert.assert_called_once()
    assert alert.call_args.args[1:] == ("Couldn’t Save", "disk full")
    assert mac_window.editor is editor


def test_mac_broken_config_disables_editing(appkit, tmp_path):
    from vox.ui.mac.vocab import VocabController

    path = tmp_path / "config.toml"
    path.write_text("[snippets\n")
    with patch("vox.ui.mac.kit.alert") as alert:
        controller = VocabController.alloc().initWithModel_(VocabModel(path))
    alert.assert_called_once()
    assert not controller.word_field.isEnabled()
    assert controller.words_footer.stringValue().startswith("Couldn’t read")
    controller.window.close()


# -- Linux --------------------------------------------------------------------------


def _drain(gtk):
    context = gtk.GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


@pytest.fixture
def gtk_window(gtk, cfg):
    from vox.ui.gtk.vocab import VocabWindow

    window = VocabWindow(VocabModel(cfg))
    window.shown_toasts = []
    window.toasts.add_toast = window.shown_toasts.append
    yield window
    window.destroy()
    _drain(gtk)


def gtk_titles(listbox):
    titles, i = [], 0
    while (row := listbox.get_row_at_index(i)) is not None:
        titles.append(row.get_title())
        i += 1
    return titles


def test_gtk_lists_words_and_snippets(gtk, gtk_window):
    assert gtk_titles(gtk_window.words_list) == ["FastAPI"]
    assert gtk_titles(gtk_window.snippets_list) == ["my email"]
    assert not gtk_window.banner.get_revealed()


def test_gtk_add_words(gtk, cfg, gtk_window):
    gtk_window.word_entry.set_text("Kubernetes, PostgreSQL")
    gtk_window.add_words()
    assert load_config(cfg).dictionary == ["FastAPI", "Kubernetes", "PostgreSQL"]
    assert gtk_titles(gtk_window.words_list) == ["FastAPI", "Kubernetes", "PostgreSQL"]
    assert gtk_window.word_entry.get_text() == ""
    assert gtk_window.shown_toasts[-1].get_title() == "Added 2 words"


def test_gtk_removing_a_word_can_be_undone(gtk, cfg, gtk_window):
    gtk_window.remove_word("FastAPI")
    assert load_config(cfg).dictionary == []
    toast = gtk_window.shown_toasts[-1]
    assert toast.get_button_label() == "Undo"
    toast.emit("button-clicked")
    assert load_config(cfg).dictionary == ["FastAPI"]


def test_gtk_deleting_a_snippet_can_be_undone(gtk, cfg, gtk_window):
    gtk_window.remove_snippet("my email")
    assert load_config(cfg).snippets == {}
    gtk_window.shown_toasts[-1].emit("button-clicked")
    assert load_config(cfg).snippets == {"my email": "alex@example.com"}


def test_gtk_snippet_editor(gtk, cfg, gtk_window):
    gtk_window.open_editor(None)
    editor = gtk_window.editor
    assert not editor.save_button.get_sensitive()
    editor.trigger.set_text("My Email")
    assert editor.warning.get_visible()
    editor.trigger.set_text("sign off")
    editor.expansion.get_buffer().set_text("Best,\nAlex")
    assert editor.save_button.get_sensitive() and not editor.warning.get_visible()
    editor.save()
    _drain(gtk)
    assert load_config(cfg).snippets["sign off"] == "Best,\nAlex"
    assert gtk_titles(gtk_window.snippets_list) == ["my email", "sign off"]


def test_gtk_broken_config_shows_a_banner_and_disables_editing(gtk, tmp_path):
    from vox.ui.gtk.vocab import VocabWindow

    path = tmp_path / "config.toml"
    path.write_text("[snippets\n")
    with patch("vox.ui.gtk.vocab.error_dialog") as dialog:
        window = VocabWindow(VocabModel(path))
    dialog.assert_called_once()
    assert window.banner.get_revealed()
    assert not window.word_entry.get_sensitive()
    window.destroy()


# -- Launch -------------------------------------------------------------------------


def test_main_runs_the_platform_window(cfg):
    from vox.ui import vocab_window

    module = "vox.ui.mac.vocab" if sys.platform == "darwin" else "vox.ui.gtk.vocab"
    fake = SimpleNamespace(run=MagicMock())
    with patch.dict(sys.modules, {module: fake}):
        vocab_window.main(["--config", str(cfg)])
    (model,), _ = fake.run.call_args
    assert model.path == cfg
