"""Checks on what a release ships besides the code: metadata, locks, docs and workflows."""

import dataclasses
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from vox.config import Config, load_config

REPO = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((REPO / "pyproject.toml").read_text())
VERSION = PYPROJECT["project"]["version"]

# Every setting config.example.toml must document at its default, as (table, key)
DOCUMENTED = {
    (None, "dictionary"),
    ("hotkey", "key"), ("hotkey", "fallback"), ("hotkey", "double_tap_timeout_ms"),
    ("audio", "sample_rate"), ("audio", "channels"), ("audio", "max_recording_seconds"),
    ("transcription", "mode"), ("transcription", "streaming_model"), ("transcription", "prompt"),
    ("whisper", "model"),
    ("whisper_cpp", "binary"), ("whisper_cpp", "model"),
    ("context", "screen"),
    ("sounds", "enabled"),
    ("attenuation", "enabled"), ("attenuation", "level"),
}


def settings(config: Config) -> dict:
    return {f.name: getattr(config, f.name) for f in dataclasses.fields(config)}


def test_example_config_changes_nothing_as_shipped():
    assert settings(load_config(REPO / "config.example.toml")) == settings(Config())


def test_example_config_shows_the_real_defaults(tmp_path):
    # Uncommenting every "# key = value" line must still give the defaults
    text = (REPO / "config.example.toml").read_text()
    uncommented = re.sub(r"^# (\w+ = )", r"\1", text, flags=re.MULTILINE)
    path = tmp_path / "config.toml"
    path.write_text(uncommented)

    assert settings(load_config(path)) == settings(Config())
    data = tomllib.loads(uncommented)
    found = {(None, k) for k, v in data.items() if not isinstance(v, dict)}
    found |= {(table, k) for table, values in data.items() if isinstance(values, dict) for k in values}
    assert DOCUMENTED <= found


def test_changelog_describes_this_version():
    notes = subprocess.run(
        ["bash", str(REPO / "scripts/changelog-notes.sh"), VERSION], capture_output=True, text=True, check=True,
    ).stdout
    assert notes.strip()
    assert not notes.startswith("\n")
    assert "## [" not in notes  # only this version's section
    assert f"[{VERSION}]: https://github.com/runsonmypc/vox/releases/tag/v{VERSION}" in (REPO / "CHANGELOG.md").read_text()


def test_changelog_notes_fail_for_an_unknown_version():
    result = subprocess.run(
        ["bash", str(REPO / "scripts/changelog-notes.sh"), "0.0.1"], capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "no section for 0.0.1" in result.stderr


def locked_requirements() -> list[str]:
    text = (REPO / "requirements.lock").read_text().replace("\\\n", " ")
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def test_requirements_lock_pins_every_package_with_hashes():
    requirements = locked_requirements()
    names = set()
    for requirement in requirements:
        assert re.match(r"^[A-Za-z0-9_.-]+==[^ ;]+", requirement), requirement
        assert "--hash=sha256:" in requirement, requirement
        names.add(requirement.split("==")[0].lower())
    # evdev ships no wheels, and Vox never uses the pynput backend that imports it
    assert "evdev" not in names
    # The build backend is installed from the lock too, so building Vox fetches nothing unpinned
    assert "setuptools" in names
    assert {"openai", "pynput", "sounddevice", "keyring"} <= names


def test_pyproject_metadata():
    project = PYPROJECT["project"]
    assert project["license"] == "GPL-3.0-only"
    assert project["license-files"] == ["LICENSE"]
    assert (REPO / "LICENSE").read_text().lstrip().startswith("GNU GENERAL PUBLIC LICENSE\n                       Version 3")
    # Some dependencies publish wheels only for these Pythons; install.sh checks the same range
    assert project["requires-python"] == ">=3.12,<3.14"
    assert "Python 3.12 or 3.13" in (REPO / "install.sh").read_text()
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION)


def shell_scripts() -> list[Path]:
    return [REPO / "install.sh", *sorted((REPO / "scripts").glob("*.sh")), *sorted((REPO / "packaging").rglob("*.sh"))]


@pytest.mark.parametrize("script", shell_scripts(), ids=lambda p: str(p.relative_to(REPO)))
def test_shell_scripts_parse_and_are_executable(script):
    subprocess.run(["bash", "-n", str(script)], check=True)
    assert script.stat().st_mode & 0o111


@pytest.mark.parametrize("script", ["postinst", "prerm", "postrm"])
def test_maintainer_scripts_are_posix_sh(script):
    path = REPO / "packaging" / "deb" / script
    assert path.read_text().startswith("#!/bin/sh\n")
    subprocess.run(["sh", "-n", str(path)], check=True)
    assert path.stat().st_mode & 0o111


def test_workflow_actions_are_pinned_to_commits():
    uses = []
    for workflow in (REPO / ".github" / "workflows").glob("*.yml"):
        uses += re.findall(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$", workflow.read_text(), flags=re.MULTILINE)
    assert uses
    for action, comment in uses:
        if action.startswith("./"):
            continue
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", action), action
        assert re.fullmatch(r"\s*# v\d+(\.\d+)*", comment), f"{action} needs its version as a comment"
