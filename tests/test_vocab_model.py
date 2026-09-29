"""The Vocabulary and Snippets pages' model; the pages themselves are tested in test_settings_window.py."""

import tomllib

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

# Parses, but fails validation: the window shows no snippets, while the file still has one
INVALID = """\
[audio]
max_recording_seconds = 0

[snippets]
"my address" = "221B Baker Street, London"
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


def test_invalid_config_refuses_every_write(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(INVALID)
    model = loaded(path)
    assert model.load_error and model.snippets == {}
    assert model.conflict("my address") is None  # so the window could not warn before replacing it
    for write in (
        lambda: model.save_snippet("my address", "typo"),
        lambda: model.remove_snippet("my address"),
        lambda: model.add_words("Vox"),
        lambda: model.remove_words(["Vox"]),
    ):
        with pytest.raises(ConfigError, match="max_recording_seconds"):
            write()
    assert path.read_text() == INVALID

    path.write_text(INVALID.replace("= 0", "= 60"))
    model.reload()
    model.save_snippet("sign off", "Best")
    assert load_config(path).snippets == {"my address": "221B Baker Street, London", "sign off": "Best"}


def test_missing_config_file_is_created(tmp_path):
    path = tmp_path / "vox" / "config.toml"
    model = loaded(path)
    assert model.words == [] and model.snippets == {}
    model.add_words("Vox")
    assert load_config(path).dictionary == ["Vox"]


def test_shown_path_abbreviates_home():
    from pathlib import Path

    assert VocabModel(Path.home() / ".config" / "vox" / "config.toml").shown_path == "~/.config/vox/config.toml"
