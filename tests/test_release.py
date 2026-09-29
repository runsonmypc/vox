"""Checks on what a release ships besides the code: metadata, locks, docs and workflows."""

import dataclasses
import os
import re
import shlex
import subprocess
import textwrap
import tomllib
from pathlib import Path

import pytest

from vox.config import Config, load_config

REPO = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((REPO / "pyproject.toml").read_text())
VERSION = PYPROJECT["project"]["version"]
WORKFLOWS = REPO / ".github" / "workflows"
# Standard GitHub-hosted runners, free on public repositories; larger runners are billed
FREE_RUNNERS = {"ubuntu-24.04", "ubuntu-24.04-arm", "macos-latest"}

# Every setting config.example.toml must document at its default, as (table, key)
DOCUMENTED = {
    (None, "dictionary"),
    ("hotkey", "key"), ("hotkey", "fallback"), ("hotkey", "double_tap_timeout_ms"),
    ("audio", "sample_rate"), ("audio", "channels"), ("audio", "max_recording_seconds"),
    ("transcription", "mode"), ("transcription", "streaming_model"), ("transcription", "prompt"),
    ("whisper", "model"),
    ("whisper_cpp", "binary"), ("whisper_cpp", "model"), ("whisper_cpp", "cpu_fallback"),
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


def documented_settings() -> dict[tuple[str | None, str], str]:
    """The settings table in docs/settings.md as {(table, key): default}, without tables of pairs such as [snippets]."""
    page = (REPO / "docs" / "settings.md").read_text()
    rows = re.findall(r"^\| `(?:\[(\w+)\] )?(\w+)`(?: \(top level\))? \| ([^|]+) \|", page, flags=re.MULTILINE)
    return {(table or None, key): default.strip() for table, key, default in rows}


def test_the_settings_page_documents_every_setting_at_its_default(tmp_path):
    documented = documented_settings()
    unset = {("audio", "device"), ("transcription", "language")}
    assert set(documented) == DOCUMENTED | unset
    assert Config().audio_device is None and Config().whisper_language is None

    # The defaults the table gives as TOML values must load as Config's own
    top, tables = [], {}
    for (table, key), default in documented.items():
        value = re.fullmatch(r"`([^`]+)`(?: \(none\))?", default)
        if value is None:
            assert (table, key) in unset and default.startswith("unset"), (table, key, default)
            continue
        line = f"{key} = {value.group(1)}"
        if table is None:
            top.append(line)
        else:
            tables.setdefault(table, []).append(line)
    path = tmp_path / "config.toml"
    path.write_text("\n".join(top + [f"[{table}]\n" + "\n".join(lines) for table, lines in tables.items()]))
    assert settings(load_config(path)) == settings(Config())


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


def workflow_jobs(name: str) -> dict[str, str]:
    """Each job's lines in a workflow, by job id (jobs sit two spaces in, under `jobs:`)."""
    body = (WORKFLOWS / name).read_text().split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([\w-]+):\n", body, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_every_workflow_job_has_a_timeout(workflow):
    jobs = workflow_jobs(workflow)
    assert jobs
    for job, lines in jobs.items():
        if re.search(r"^    uses: ", lines, flags=re.MULTILINE):
            continue  # a call to another workflow, whose jobs set their own
        assert re.search(r"^    timeout-minutes: \d+$", lines, flags=re.MULTILINE), f"{workflow}: {job} has no timeout-minutes"


def test_workflows_run_only_on_free_standard_runners():
    for workflow in WORKFLOWS.glob("*.yml"):
        text = workflow.read_text()
        labels = re.findall(r"^\s+runs-on: (.+)$", text, flags=re.MULTILINE)
        assert labels, workflow.name
        for label in labels:
            assert label in FREE_RUNNERS or label == "${{ matrix.runner }}", f"{workflow.name}: {label}"
        matrix = re.findall(r"^\s+(?:- )?runner: (\S+)$", text, flags=re.MULTILINE)
        for listed in re.findall(r"^\s+runner: \[(.+)\]$", text, flags=re.MULTILINE):
            matrix += [label.strip() for label in listed.split(",")]
        assert set(matrix) <= FREE_RUNNERS, f"{workflow.name}: {matrix}"


def test_workflows_pin_the_same_uv():
    pins = {w.name: re.findall(r'^  UV_VERSION: "([^"]+)"', w.read_text(), flags=re.MULTILINE) for w in WORKFLOWS.glob("*.yml")}
    assert len(pins["ci.yml"]) == 1 and pins["release.yml"] == pins["ci.yml"], pins


def test_a_manual_release_run_publishes_nothing():
    assert re.search(r"^  workflow_dispatch:", (WORKFLOWS / "release.yml").read_text(), flags=re.MULTILINE)
    jobs = workflow_jobs("release.yml")
    # Only the publish job can write, only a pushed tag runs it, and no other job touches releases
    assert [job for job, lines in jobs.items() if "contents: write" in lines] == ["publish"]
    assert re.search(r"^    if: github.event_name == 'push'$", jobs["publish"], flags=re.MULTILINE)
    assert "gh release create" in jobs["publish"]
    assert not any("gh release" in lines for job, lines in jobs.items() if job != "publish")


def deb_depends() -> set[str]:
    control = re.search(r"^Depends: (.+)$", (REPO / "packaging/deb/build-deb.sh").read_text(), flags=re.MULTILINE)
    return {dependency.split()[0] for dependency in control.group(1).split(",")}


def install_sh_packages() -> set[str]:
    """The Debian packages install.sh's linux_deps checks for."""
    body = (REPO / "install.sh").read_text().split("\nlinux_deps() {\n", 1)[1].split("\n}\n", 1)[0]
    return {package for group in re.findall(r"missing\+=\(([^)]+)\)", body) for package in group.split()}


def test_deb_and_install_sh_need_the_same_system_packages():
    # xprop tells terminals, which paste with Ctrl+Shift+V, from other windows
    assert "x11-utils" in deb_depends() and "x11-utils" in install_sh_packages()
    # The .deb ships its virtualenv, so only install.sh needs python3-venv
    assert install_sh_packages() - {"python3-venv"} <= deb_depends()


def test_users_see_the_name_vox_transfer_while_the_package_and_service_stay_vox():
    for entry in ("vox.desktop", "vox-autostart.desktop"):
        assert "\nName=Vox Transfer\n" in (REPO / "packaging/deb" / entry).read_text()
    assert "\nDescription=Vox Transfer voice dictation\n" in (REPO / "packaging/linux/vox.service").read_text()
    control = (REPO / "packaging/deb/build-deb.sh").read_text()
    assert "\nPackage: vox\n" in control and "\nDescription: Vox Transfer, voice dictation" in control
    assert '--title "Vox Transfer $VERSION"' in workflow_jobs("release.yml")["publish"]


def test_deb_smoke_test_imports_the_packaged_vox_not_the_checkout():
    """CI runs the smoke test from the checkout, where python -c would import its vox/ instead of /opt/vox."""
    script = (REPO / "packaging/deb/smoke-test.sh").read_text()
    lines = script.splitlines()
    cd = lines.index("cd /")
    assert lines.index('deb=$(realpath "$1")') < cd
    runs = [i for i, line in enumerate(lines) if '"$py"' in line]
    assert runs and all(i > cd and '"$py" -P -c' in lines[i] for i in runs)
    assert 'assert vox.__file__.startswith("/opt/vox/venv/")' in script


def relock_commands() -> str:
    """The shell block in RELEASING.md's relock step."""
    step = (REPO / "RELEASING.md").read_text().split('<a id="relock"></a>', 1)[1]
    return textwrap.dedent(re.search(r"```sh\n(.*?\n)\s*```", step, flags=re.DOTALL).group(1))


def test_relock_runs_the_pinned_uv_from_a_private_directory(tmp_path):
    """Another account could create a fixed /tmp directory first and swap the uv in it."""
    commands = relock_commands()
    assert "mktemp -d" in commands and "/tmp" not in commands
    uv_version = re.search(r'^  UV_VERSION: "([^"]+)"', (WORKFLOWS / "ci.yml").read_text(), flags=re.MULTILINE).group(1)
    bin_dir, checkout = tmp_path / "bin", tmp_path / "checkout"
    bin_dir.mkdir()
    (checkout / "scripts").mkdir(parents=True)
    log = tmp_path / "log"
    fakes = {
        bin_dir / "uvx": f'#!/bin/sh\necho "uvx $*" >>{shlex.quote(str(log))}\n',
        # Logs the directory's mode and path, then calls uv as the real lock.sh does
        checkout / "scripts/lock.sh": (
            '#!/bin/sh\nshim=$(command -v uv)\n'
            f'echo "$(ls -ld "${{shim%/uv}}" | cut -c1-10) $shim" >>{shlex.quote(str(log))}\nuv export "$@"\n'
        ),
    }
    for path, text in fakes.items():
        path.write_text(text)
        path.chmod(0o755)
    shims = set()
    for _ in range(2):
        log.unlink(missing_ok=True)
        subprocess.run(
            ["bash", "-euc", commands], cwd=checkout, check=True,
            env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "TMPDIR": str(tmp_path), "HOME": str(tmp_path)},
        )
        calls = log.read_text().splitlines()
        pinned = f"uvx uv@{uv_version}"
        assert calls[0::2] == [f"{pinned} lock", f"{pinned} export", f"{pinned} export --check"]
        for call in calls[1::2]:
            mode, shim = call.split(" ", 1)
            assert mode == "drwx------", call
            assert not Path(shim).parent.exists()  # removed afterwards
            shims.add(shim)
    assert len(shims) == 2  # a new directory each time, not a fixed path


def test_troubleshooting_gives_the_full_command_path():
    """~/.local/bin is not on macOS's default PATH, so a bare `vox` is often "command not found"."""
    troubleshooting = (REPO / "docs" / "troubleshooting.md").read_text()
    assert not re.search(r"`vox[ `]", troubleshooting)
    assert "`~/.local/bin/vox -v`" in troubleshooting and "`/usr/bin/vox`" in troubleshooting
