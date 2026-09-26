"""Tests for the vocabulary and snippet editor, driven through a withdrawn Tk root."""

import tomllib

import pytest

tk = pytest.importorskip("tkinter")

from vox.config import load_config
from vox.ui.tkutil import create_root
from vox.ui.vocab_window import VocabWindow

CONFIG = """\
# my settings
dictionary = ["FastAPI"]

[snippets]
"my email" = "alex@example.com"
"""


@pytest.fixture
def root():
    try:
        r = create_root()
    except tk.TclError as e:
        pytest.skip(f"Tk unavailable: {e}")
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(CONFIG)
    return path


def words(win):
    return list(win.words.get(0, tk.END))


def snippet_rows(win):
    return [tuple(win.snippets.item(iid, "values")) for iid in win.snippets.get_children()]


def test_lists_current_vocabulary_and_snippets(root, cfg):
    win = VocabWindow(root, cfg)
    assert words(win) == ["FastAPI"]
    assert snippet_rows(win) == [("my email", "alex@example.com")]


def test_add_word_persists_and_clears_entry(root, cfg):
    win = VocabWindow(root, cfg)
    win.word.set("  Kubernetes ")
    win.add_word()
    assert words(win) == ["FastAPI", "Kubernetes"]
    assert win.word.get() == ""
    assert load_config(cfg).dictionary == ["FastAPI", "Kubernetes"]
    assert cfg.read_text().startswith("# my settings")
    assert "Added" in win.status.get()


def test_blank_word_is_ignored(root, cfg):
    win = VocabWindow(root, cfg)
    before = cfg.read_text()
    win.word.set("   ")
    win.add_word()
    assert cfg.read_text() == before


def test_remove_selected_words(root, cfg):
    win = VocabWindow(root, cfg)
    for w in ("Kubernetes", "PostgreSQL"):
        win.word.set(w)
        win.add_word()
    win.words.selection_set(0)
    win.words.selection_set(2)
    win.remove_selected_words()
    assert words(win) == ["Kubernetes"]
    assert load_config(cfg).dictionary == ["Kubernetes"]


def test_add_multiline_snippet(root, cfg):
    win = VocabWindow(root, cfg)
    win.trigger.set("sign off")
    win.expansion.insert("1.0", "Best,\nAlex")
    win.save_snippet()
    assert load_config(cfg).snippets == {"my email": "alex@example.com", "sign off": "Best,\nAlex"}
    assert ("sign off", "Best, Alex") in snippet_rows(win)
    assert win.trigger.get() == "" and win.expansion.get("1.0", "end-1c") == ""


def test_selecting_snippet_loads_it_for_editing(root, cfg):
    win = VocabWindow(root, cfg)
    win.snippets.selection_set("my email")
    win._edit_selected_snippet()
    assert win.trigger.get() == "my email"
    assert win.expansion.get("1.0", "end-1c") == "alex@example.com"

    win.expansion.delete("1.0", tk.END)
    win.expansion.insert("1.0", "new@example.com")
    win.save_snippet()
    assert load_config(cfg).snippets == {"my email": "new@example.com"}


def test_remove_selected_snippets(root, cfg):
    win = VocabWindow(root, cfg)
    win.trigger.set('say "hi"')
    win.expansion.insert("1.0", "Hello!")
    win.save_snippet()
    win.snippets.selection_set(("my email", 'say "hi"'))
    win.remove_selected_snippets()
    assert snippet_rows(win) == []
    assert tomllib.loads(cfg.read_text())["snippets"] == {}


def test_invalid_snippet_shows_error_and_keeps_form(root, cfg):
    win = VocabWindow(root, cfg)
    before = cfg.read_text()
    win.trigger.set("empty")
    win.save_snippet()
    assert win.status.get().startswith("Not saved:")
    assert win.trigger.get() == "empty"
    assert cfg.read_text() == before


def test_broken_config_is_reported_not_overwritten(root, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[snippets\n")
    win = VocabWindow(root, path)
    assert win.status.get().startswith("Could not read config")
    win.word.set("Vox")
    win.add_word()
    assert win.status.get().startswith("Not saved:")
    assert path.read_text() == "[snippets\n"


def test_missing_config_file_is_created(root, tmp_path):
    path = tmp_path / "vox" / "config.toml"
    win = VocabWindow(root, path)
    assert words(win) == [] and snippet_rows(win) == []
    win.word.set("Vox")
    win.add_word()
    assert load_config(path).dictionary == ["Vox"]
