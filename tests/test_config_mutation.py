"""Tests for writing dictionary words and snippets back to config.toml."""

import asyncio
import logging
import os
import stat
import tomllib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vox.config import (
    RECORDING_LIMIT_CHOICES,
    load_config,
    update_dictionary,
    update_hotkey,
    update_max_recording_seconds,
    update_snippet,
    update_transcription_mode,
    write_atomically,
)
from vox.errors import ConfigError

EXAMPLE = """\
# Vox configuration
[api]
# openai_api_key = "sk-..."

[audio]
sample_rate = 16000  # keep this comment

# Snippets - exact match on raw transcript -> expansion
[snippets]
"my email" = "alex@example.com"  # personal
"""


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(EXAMPLE)
    return path


def test_add_words_creates_top_level_dictionary(cfg):
    assert update_dictionary(cfg, add=["FastAPI", "PostgreSQL"]) == ["FastAPI", "PostgreSQL"]
    text = cfg.read_text()
    assert text.index("dictionary") < text.index("[api]")  # top-level keys must precede tables
    assert load_config(cfg).dictionary == ["FastAPI", "PostgreSQL"]


def test_comments_and_other_settings_survive(cfg):
    update_dictionary(cfg, add=["Vox"])
    update_snippet(cfg, "my address", "123 Main St")
    text = cfg.read_text()
    for line in ("# Vox configuration", '# openai_api_key = "sk-..."', "sample_rate = 16000  # keep this comment",
                 "# Snippets - exact match on raw transcript -> expansion", '"my email" = "alex@example.com"  # personal'):
        assert line in text
    config = load_config(cfg)
    assert config.sample_rate == 16000
    assert config.snippets == {"my email": "alex@example.com", "my address": "123 Main St"}


def test_transcription_mode_update_preserves_config(cfg):
    update_transcription_mode(cfg, "whisper_cpp")
    assert load_config(cfg).mode == "whisper_cpp"
    assert 'sample_rate = 16000  # keep this comment' in cfg.read_text()
    update_transcription_mode(cfg, "streaming")
    assert load_config(cfg).mode == "streaming"
    assert cfg.read_text().count('mode = "streaming"') == 1
    before = cfg.read_text()
    with pytest.raises(ValueError):
        update_transcription_mode(cfg, "invalid")
    assert cfg.read_text() == before


@pytest.mark.parametrize("old_mode, section, key", [
    ("batch", "whisper", "model"),
    ("streaming", "transcription", "streaming_model"),
    ("whisper_cpp", "whisper_cpp", "model"),
])
def test_mode_switch_preserves_legacy_model_for_old_provider(tmp_path, old_mode, section, key):
    path = tmp_path / "config.toml"
    path.write_text(f'[transcription]\nmode = "{old_mode}"\nmodel = "old-model"\n')
    new_mode = "batch" if old_mode != "batch" else "whisper_cpp"
    update_transcription_mode(path, new_mode)
    data = tomllib.loads(path.read_text())
    assert data["transcription"]["mode"] == new_mode
    assert "model" not in data["transcription"]
    assert data[section][key] == "old-model"


def test_mode_switch_keeps_the_effective_model_over_a_legacy_provider_model(tmp_path):
    """[transcription] model wins over [whisper] model, so switching modes must not bring the shadowed one back."""
    path = tmp_path / "config.toml"
    path.write_text('[whisper]\nmodel = "whisper-1"\n\n[transcription]\nmode = "batch"\nmodel = "gpt-4o-transcribe"\n')
    assert load_config(path).whisper_model == "gpt-4o-transcribe"
    update_transcription_mode(path, "streaming")
    assert "gpt-4o-transcribe" in path.read_text()
    update_transcription_mode(path, "batch")
    assert load_config(path).whisper_model == "gpt-4o-transcribe"


def test_mode_switch_keeps_the_effective_streaming_model(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[transcription]\nmode = "streaming"\nmodel = "live-new"\nstreaming_model = "live-old"\n')
    assert load_config(path).streaming_model == "live-new"
    update_transcription_mode(path, "batch")
    update_transcription_mode(path, "streaming")
    assert load_config(path).streaming_model == "live-new"


def test_mode_switch_keeps_the_effective_whisper_cpp_model(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[whisper_cpp]\nmodel = "old.bin"\n\n[transcription]\nmode = "whisper_cpp"\nmodel = "new.bin"\n')
    update_transcription_mode(path, "batch")
    update_transcription_mode(path, "whisper_cpp")
    assert load_config(path).whisper_cpp_model == "new.bin"


def test_add_skips_duplicates_and_blanks(cfg):
    update_dictionary(cfg, add=["FastAPI"])
    assert update_dictionary(cfg, add=["fastapi", "  ", "", " Kubernetes "]) == ["FastAPI", "Kubernetes"]


def test_remove_words(cfg):
    update_dictionary(cfg, add=["A", "B", "C"])
    assert update_dictionary(cfg, remove=["B", "missing"]) == ["A", "C"]
    assert load_config(cfg).dictionary == ["A", "C"]


@pytest.mark.parametrize("section", ["transcription", "whisper"])
def test_edits_dictionary_where_load_config_reads_it(tmp_path, section):
    path = tmp_path / "config.toml"
    path.write_text(f'[{section}]\ndictionary = [\n    "Vox",  # the app\n    "OpenAI",\n]\n')
    assert update_dictionary(path, add=["Kubernetes"], remove=["OpenAI"]) == ["Vox", "Kubernetes"]
    data = tomllib.loads(path.read_text())
    assert "dictionary" not in data  # no shadowing top-level key
    assert data[section]["dictionary"] == ["Vox", "Kubernetes"]
    assert "# the app" in path.read_text()
    assert load_config(path).dictionary == ["Vox", "Kubernetes"]


def test_edits_the_dictionary_load_config_reads_when_several_sections_have_one(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[attenuation]\ndictionary = ["A"]\n\n[whisper]\ndictionary = ["W"]\n')
    update_dictionary(path, add=["x"])
    assert load_config(path).dictionary == ["A", "x"]


def test_add_snippet_creates_table(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[audio]\nsample_rate = 16000\n")
    assert update_snippet(path, "sign off", "Best,\nAlex") == {"sign off": "Best,\nAlex"}
    assert load_config(path).snippets == {"sign off": "Best,\nAlex"}


def test_snippet_replaces_equivalent_trigger(cfg):
    result = update_snippet(cfg, "My Email.", "new@example.com")
    assert result == {"My Email.": "new@example.com"}


def test_remove_snippet(cfg):
    assert update_snippet(cfg, "my email", None) == {}
    assert load_config(cfg).snippets == {}


@pytest.mark.parametrize("trigger, expansion", [("", "x"), ("   ", "x"), ("hi", ""), ("hi", "  ")])
def test_snippet_validation(cfg, trigger, expansion):
    before = cfg.read_text()
    with pytest.raises(ValueError):
        update_snippet(cfg, trigger, expansion)
    assert cfg.read_text() == before


def test_quotes_and_unicode_round_trip(cfg):
    update_snippet(cfg, 'say "hi"', "Héllo — ✓ \\ 'quoted'")
    update_dictionary(cfg, add=['Zoë "Z" O\'Neil'])
    config = load_config(cfg)
    assert config.snippets['say "hi"'] == "Héllo — ✓ \\ 'quoted'"
    assert config.dictionary == ['Zoë "Z" O\'Neil']


def test_creates_missing_file_and_dirs_with_private_mode(tmp_path):
    path = tmp_path / "new" / "vox" / "config.toml"
    update_dictionary(path, add=["Vox"])
    assert load_config(path).dictionary == ["Vox"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_new_config_directory_is_owner_only(tmp_path):
    path = tmp_path / "vox" / "config.toml"
    update_snippet(path, "x", "y")
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_recording_limit_is_saved_in_the_audio_section(cfg):
    update_max_recording_seconds(cfg, 1800)
    assert load_config(cfg).max_recording_seconds == 1800
    assert "sample_rate = 16000  # keep this comment" in cfg.read_text()
    update_max_recording_seconds(cfg, RECORDING_LIMIT_CHOICES[0])
    assert load_config(cfg).max_recording_seconds == RECORDING_LIMIT_CHOICES[0]


def test_recording_limit_creates_a_private_file(tmp_path):
    path = tmp_path / "new" / "config.toml"
    update_max_recording_seconds(path, 600)
    assert load_config(path).max_recording_seconds == 600
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("seconds", [0, -60, True, 90.0, "600"])
def test_recording_limit_rejects_invalid_values(cfg, seconds):
    before = cfg.read_text()
    with pytest.raises(ValueError):
        update_max_recording_seconds(cfg, seconds)
    assert cfg.read_text() == before


def test_hotkey_is_saved_in_the_hotkey_section(cfg):
    update_hotkey(cfg, "cmd_r", "ctrl+space")
    config = load_config(cfg)
    assert (config.hotkey, config.hotkey_fallback) == ("cmd_r", "ctrl+space")
    assert "sample_rate = 16000  # keep this comment" in cfg.read_text()
    before = cfg.read_text()
    with pytest.raises(ValueError):
        update_hotkey(cfg, " ", "")
    assert cfg.read_text() == before


def test_clearing_the_combination_removes_fallback(cfg):
    update_hotkey(cfg, "cmd_r", "ctrl+space")
    update_hotkey(cfg, "cmd_r", "")
    assert tomllib.loads(cfg.read_text())["hotkey"] == {"key": "cmd_r"}
    update_hotkey(cfg, "f13", "")  # nothing to remove
    assert load_config(cfg).hotkey_fallback == ""


def test_hotkey_update_creates_a_private_file(tmp_path):
    path = tmp_path / "new" / "config.toml"
    update_hotkey(path, "f13", "")
    assert tomllib.loads(path.read_text()) == {"hotkey": {"key": "f13"}}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_preserves_file_permissions(cfg):
    os.chmod(cfg, 0o640)
    update_dictionary(cfg, add=["Vox"])
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o640


def test_follows_symlink_instead_of_replacing_it(tmp_path):
    real = tmp_path / "dotfiles" / "vox.toml"
    real.parent.mkdir()
    real.write_text(EXAMPLE)
    link = tmp_path / "config.toml"
    link.symlink_to(real)
    update_dictionary(link, add=["Vox"])
    assert link.is_symlink()
    assert tomllib.loads(real.read_text())["dictionary"] == ["Vox"]


def test_write_is_atomic_on_failure(cfg):
    before = cfg.read_text()
    with patch("vox.config.os.replace", side_effect=OSError("disk full")), pytest.raises(OSError):
        update_dictionary(cfg, add=["Vox"])
    assert cfg.read_text() == before
    assert [p.name for p in cfg.parent.iterdir()] == ["config.toml"]  # temp file cleaned up


def test_refuses_to_overwrite_unparseable_config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[audio\nbroken = \n")
    with pytest.raises(ConfigError):
        update_dictionary(path, add=["Vox"])
    assert path.read_text() == "[audio\nbroken = \n"


@pytest.mark.parametrize("text, write", [
    ("audio = 5\n", lambda path: update_max_recording_seconds(path, 600)),
    ('hotkey = "right_ctrl"\n', lambda path: update_hotkey(path, "f13", "")),
    ('transcription = "batch"\n', lambda path: update_transcription_mode(path, "streaming")),
    ('whisper = 1\n[transcription]\nmodel = "m"\n', lambda path: update_transcription_mode(path, "streaming")),
    ('[transcription]\nmode = "batch"\nmodel = "m"\n[whisper]\n', None),
])
def test_a_section_that_is_not_a_table_is_a_config_error_not_a_crash(tmp_path, text, write):
    """The daemon reports a ConfigError with the error sound; a TypeError would be an unexpected error."""
    path = tmp_path / "config.toml"
    path.write_text(text)
    if write is None:  # a real [whisper] table is fine
        update_transcription_mode(path, "streaming")
        assert tomllib.loads(path.read_text())["whisper"] == {"model": "m"}
        return
    with pytest.raises(ConfigError, match="must be a table"):
        write(path)
    assert path.read_text() == text


def test_write_atomically_can_make_an_existing_file_owner_only(tmp_path):
    path = tmp_path / "vox.env"
    path.write_text("OLD=1\n")
    os.chmod(path, 0o644)
    write_atomically(path, "NEW=1\n", keep_mode=False)
    assert path.read_text() == "NEW=1\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_bumps_mtime_for_reloader(cfg):
    os.utime(cfg, (1_000_000, 1_000_000))
    update_snippet(cfg, "x", "y")
    assert cfg.stat().st_mtime > 1_000_000


# -- Hot reload into a running daemon ---------------------------------------


@asynccontextmanager
async def running_reloader(config, recorder, tray=None):
    """Run the daemon's real _config_reloader with its 2s poll shortened."""
    from vox.daemon import _config_reloader

    async def wait_until(predicate, timeout=3.0):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            assert loop.time() < deadline, "reloader did not pick up the change"
            await asyncio.sleep(0.01)

    with patch("vox.daemon._CONFIG_POLL_SECONDS", 0.01):
        task = asyncio.create_task(_config_reloader(config, recorder, tray))
        await asyncio.sleep(0.03)  # let it note the file as it is
        try:
            yield wait_until
        finally:
            task.cancel()


@pytest.mark.anyio
async def test_ui_edits_hot_reload_into_running_daemon(cfg):
    from vox.daemon import _process
    from vox.transcribe import Transcriber
    from vox.window import AppContext, AppType

    config = load_config(cfg)
    config.openai_api_key = "test"
    transcriber = Transcriber(config)  # created before the edit, like the running daemon's
    recorder = MagicMock()

    async with running_reloader(config, recorder) as wait_until:
        update_dictionary(cfg, add=["Kubernetes"])
        update_snippet(cfg, "sign off", "Best,\nAlex")
        await wait_until(lambda: "sign off" in config.snippets and config.dictionary == ["Kubernetes"])

    assert "Kubernetes" in transcriber._request(None)[0]["keywords"]

    batch = MagicMock()
    batch.transcribe = AsyncMock(return_value="Sign off.")
    # Pin the focused window: on a Linux desktop the paste would otherwise look up the real one
    with patch("vox.daemon.has_speech", return_value=True), patch("vox.daemon.paste") as paste, \
         patch("vox.daemon.detect_active_window", return_value=AppContext("mail", "Mail", AppType.EMAIL)):
        await _process(
            wav_data=b"", config=config, batch_transcriber=batch, streaming_transcriber=None,
            stream_task=None, sounds=MagicMock(), queue=asyncio.Queue(),
            context=AppContext("mail", "Mail", AppType.EMAIL), screen_capture_future=None, mode="batch",
        )
    paste.assert_called_once_with("Best,\nAlex", AppType.EMAIL)
    recorder.reconfigure.assert_not_called()


@pytest.mark.anyio
async def test_menu_device_choice_survives_vocab_edits_but_file_changes_apply(cfg):
    import tomlkit

    config = load_config(cfg)
    recorder = MagicMock()

    async with running_reloader(config, recorder) as wait_until:
        config.audio_device = 3  # picked from the menu bar at runtime
        update_dictionary(cfg, add=["Vox"])
        await wait_until(lambda: config.dictionary == ["Vox"])
        assert config.audio_device == 3
        recorder.reconfigure.assert_not_called()

        doc = tomlkit.parse(cfg.read_text())
        doc["audio"]["device"] = "USB"
        cfg.write_text(tomlkit.dumps(doc))
        await wait_until(lambda: config.audio_device == "USB")
    recorder.reconfigure.assert_called_once_with(config)  # the live object, so later menu picks reach the recorder


@pytest.mark.anyio
async def test_invalid_edit_keeps_the_running_value(tmp_path, caplog):
    path = tmp_path / "config.toml"
    path.write_text("[attenuation]\nlevel = 0.3\n")
    config = load_config(path)

    with caplog.at_level(logging.WARNING, logger="vox.daemon"):
        async with running_reloader(config, MagicMock()) as wait_until:
            path.write_text('[attenuation]\nlevel = "0.8"\n')
            await wait_until(lambda: "Config reload failed" in caplog.text)
    assert config.attenuation_level == 0.3
    assert "[attenuation] level" in caplog.text
