"""install.sh and the service files it installs.

Each test sources install.sh in bash (which then defines its functions and runs nothing) with a
scratch HOME, and replaces every command that could touch the real system (systemctl, launchctl,
curl, id, uname) with a shell function, since functions take precedence over programs on PATH.
"""

import configparser
import os
import plistlib
import shlex
import subprocess
import sys
import tarfile
from hashlib import sha256
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "install.sh"


def bash(home: Path, script: str, **env: str) -> subprocess.CompletedProcess:
    """Source install.sh against a scratch HOME, then run `script`."""
    environ = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "TMPDIR": str(home),
        "PYTHONPATH": str(REPO),
        "LC_ALL": "C",
        **env,
    }
    return subprocess.run(
        ["bash", "-c", f"source {shlex.quote(str(INSTALL))}\n{script}"],
        env=environ, capture_output=True, text=True, timeout=120,
    )


@pytest.fixture
def home(tmp_path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


def fake_venv(home: Path) -> Path:
    """A virtualenv whose python runs the tests' interpreter, which can import vox and its dependencies."""
    venv = home / ".local/share/vox/venv"
    (venv / "bin").mkdir(parents=True)
    python = venv / "bin/python"
    # A wrapper, not a symlink: the interpreter finds its virtualenv from the path it was started as
    python.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
    python.chmod(0o755)
    return venv


# --- Options ---------------------------------------------------------------------------------

NOTHING_RUNS = """
install_vox() { touch "$HOME/ran"; }
uninstall_vox() { touch "$HOME/ran"; }
bootstrap() { touch "$HOME/ran"; }
"""


def test_refuses_to_run_as_root(home):
    result = bash(home, NOTHING_RUNS + "id() { echo 0; }\nmain --no-service")
    assert result.returncode == 1
    assert "not as root" in result.stderr
    assert not (home / "ran").exists()


@pytest.mark.parametrize("args", ["--bogus", "--no-service --uninstall"])
def test_rejects_unknown_options(home, args):
    result = bash(home, NOTHING_RUNS + f"main {args}")
    assert result.returncode == 2
    assert "--uninstall" in result.stderr
    assert not (home / "ran").exists()


def test_help_lists_the_options(home):
    result = bash(home, NOTHING_RUNS + "main --help")
    assert result.returncode == 0
    assert "--no-service" in result.stdout and "--uninstall" in result.stdout


def test_runs_from_its_source_tree_and_bootstraps_elsewhere(home, tmp_path):
    assert bash(home, "source_tree").stdout.strip() == str(REPO)
    alone = tmp_path / "download"
    alone.mkdir()
    (alone / "install.sh").write_text(INSTALL.read_text())
    result = subprocess.run(
        ["bash", "-c", f"source {shlex.quote(str(alone / 'install.sh'))}; source_tree"],
        capture_output=True, text=True, env={**os.environ, "HOME": str(home)},
    )
    assert result.returncode != 0 and result.stdout == ""


# --- Updating the virtualenv -----------------------------------------------------------------

def make_old_venv(home: Path) -> Path:
    venv = home / ".local/share/vox/venv"
    venv.mkdir(parents=True)
    (venv / "marker").write_text("old")
    return venv


BUILD = """
build_ok() { mkdir -p "$VENV"; echo new >"$VENV/marker"; }
build_fails() { mkdir -p "$VENV"; echo half >"$VENV/marker"; false; }
smoke_test() { :; }
"""


def test_failed_build_keeps_the_previous_venv(home):
    venv = make_old_venv(home)
    result = bash(home, BUILD + "replace_venv build_fails")
    assert result.returncode != 0
    assert (venv / "marker").read_text() == "old"
    assert not Path(f"{venv}.old").exists()
    assert "previous Vox Transfer stays installed" in result.stderr


def test_failed_smoke_test_keeps_the_previous_venv(home):
    venv = make_old_venv(home)
    result = bash(home, BUILD + "smoke_test() { die 'the new Vox Transfer environment does not work'; }\nreplace_venv build_ok")
    assert result.returncode == 1
    assert (venv / "marker").read_text() == "old"


def test_successful_build_replaces_the_venv(home):
    venv = make_old_venv(home)
    result = bash(home, BUILD + "replace_venv build_ok")
    assert result.returncode == 0, result.stderr
    assert (venv / "marker").read_text().strip() == "new"
    assert not Path(f"{venv}.old").exists()


def test_an_interrupted_update_restores_the_last_working_venv(home):
    venv = home / ".local/share/vox/venv"
    old = Path(f"{venv}.old")
    old.mkdir(parents=True)
    (old / "marker").write_text("old")
    venv.mkdir()
    (venv / "marker").write_text("half")  # a run killed while building
    result = bash(home, BUILD + "replace_venv build_fails")
    assert result.returncode != 0
    assert (venv / "marker").read_text() == "old"


def test_smoke_test_imports_vox_without_a_display(home):
    fake_venv(home)
    bin_vox = home / ".local/share/vox/venv/bin/vox"
    bin_vox.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -m vox \"$@\"\n")
    bin_vox.chmod(0o755)
    # vox.daemon needs an X display on Linux, so only macOS imports it here
    result = bash(home, "smoke_test", DISPLAY="", WAYLAND_DISPLAY="")
    if sys.platform == "linux" and "No module named 'gi'" in result.stderr:
        pytest.skip("python3-gi is not reachable from this venv")
    assert result.returncode == 0, result.stderr


def test_smoke_test_never_imports_the_source_tree(home):
    """Run from the unpacked release, python -c would import its vox/ and pass a venv that lacks modules."""
    venv = fake_venv(home)
    calls = home / "calls"
    (venv / "bin/python").write_text(f'#!/bin/sh\necho "$PWD $*" >> {shlex.quote(str(calls))}\n')
    (venv / "bin/vox").write_text("#!/bin/sh\n")
    (venv / "bin/vox").chmod(0o755)
    result = bash(home, f"cd {shlex.quote(str(REPO))}\nsmoke_test")
    assert result.returncode == 0, result.stderr
    cwd, args = calls.read_text().splitlines()[0].split(" ", 1)
    assert cwd == "/"
    assert args.startswith("-P -c import vox.")


# --- Linux -----------------------------------------------------------------------------------

def test_linux_launchers_add_an_autostart_entry(home):
    fake_venv(home)
    result = bash(home, "linux_launchers")
    assert result.returncode == 0, result.stderr
    for entry in (home / ".config/autostart/vox.desktop", home / ".local/share/applications/vox.desktop"):
        desktop = configparser.ConfigParser(interpolation=None)
        desktop.optionxform = str
        desktop.read_string(entry.read_text())
        assert desktop["Desktop Entry"]["Name"] == "Vox Transfer"
        assert desktop["Desktop Entry"]["Exec"] == "systemctl --user start vox.service"
        assert desktop["Desktop Entry"]["Icon"] == str(home / ".local/share/vox/vox.png")
    assert (home / ".local/share/vox/vox.png").read_bytes().startswith(b"\x89PNG")


def test_non_debian_systems_get_a_readable_package_list(home):
    script = """
        command() { [ "$2" != apt-get ] && builtin command "$@"; }
        linux_deps /nonexistent/python3
    """
    result = bash(home, script)
    assert result.returncode == 1
    assert "PortAudio (Debian/Ubuntu: libportaudio2)" in result.stderr
    assert "PyGObject (Debian/Ubuntu: python3-gi)" in result.stderr
    assert "sudo" not in result.stderr


@pytest.mark.parametrize("has_xprop", [True, False])
def test_linux_deps_install_xprop(home, has_xprop):
    """Without xprop every terminal looks like an ordinary window and gets Ctrl+V, which does not paste there."""
    hide = "xprop() { :; }" if has_xprop else 'command() { [ "$2" != xprop ] && builtin command "$@"; }'
    script = f"""
        xdotool() {{ :; }}; xclip() {{ :; }}
        {hide}
        apt-get() {{ :; }}
        sudo() {{ echo "$*" >>"$HOME/calls"; }}
        linux_deps /nonexistent/python3
    """
    result = bash(home, script)
    assert result.returncode == 0, result.stderr
    installed = (home / "calls").read_text().split()
    assert ("x11-utils" in installed) is not has_xprop
    assert "xdotool" not in installed and "libportaudio2" in installed


def test_other_distributions_are_told_to_install_xprop(home):
    script = """
        command() { [ "$2" != apt-get ] && [ "$2" != xprop ] && builtin command "$@"; }
        linux_deps /nonexistent/python3
    """
    result = bash(home, script)
    assert result.returncode == 1
    assert "xprop (X11 utilities) (Debian/Ubuntu: x11-utils)" in result.stderr


def test_apt_refreshes_its_package_lists_when_install_fails(home):
    script = """
        apt-get() { :; }
        sudo() { echo "sudo $*" >>"$HOME/calls"; [ "$2" = update ] && touch "$HOME/updated"; [ "$2" != install ] || [ -e "$HOME/updated" ]; }
        linux_deps /nonexistent/python3
    """
    result = bash(home, script)
    assert result.returncode == 0, result.stderr
    calls = (home / "calls").read_text().splitlines()
    assert [c.split()[2] for c in calls] == ["install", "update", "install"]


def test_linux_uninstall_removes_vox_and_keeps_settings_and_history(home):
    venv = fake_venv(home)
    (venv / "bin/vox").touch()
    (home / ".local/bin").mkdir(parents=True)
    (home / ".local/bin/vox").symlink_to(venv / "bin/vox")
    installed = [
        home / ".config/systemd/user/vox.service",
        home / ".config/autostart/vox.desktop",
        home / ".local/share/applications/vox.desktop",
        home / ".local/share/vox/vox.png",
    ]
    kept = [home / ".config/vox/config.toml", home / ".local/share/vox/history.db"]
    for path in installed + kept:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    script = """
        uname() { echo Linux; }
        systemctl() { echo "systemctl $*" >>"$HOME/calls"; }
        dpkg-query() { return 1; }
        uninstall_vox
    """
    result = bash(home, script)
    assert result.returncode == 0, result.stderr
    assert not any(p.exists() for p in installed)
    assert not venv.exists() and not (home / ".local/bin/vox").is_symlink()
    assert all(p.exists() for p in kept)
    assert "systemctl --user disable --now vox.service" in (home / "calls").read_text()
    assert "~/.config/vox" in result.stdout


def install_script(repo: Path) -> str:
    return f"""
        uname() {{ echo Linux; }}
        linux_install() {{ :; }}
        install_vox {shlex.quote(str(repo))} 0
    """


def test_install_ends_with_the_full_command_path(home, tmp_path):
    """~/.local/bin is not on macOS's default PATH, so a bare `vox` is often "command not found"."""
    result = bash(home, install_script(tmp_path))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == f"==> Done. Run '{home}/.local/bin/vox --help' for options."
    assert f"{home}/.local/bin is not on your PATH" in result.stderr


def test_install_says_nothing_about_path_when_the_command_is_on_it(home, tmp_path):
    result = bash(home, install_script(tmp_path), PATH=f"{home}/.local/bin:{os.environ['PATH']}")
    assert result.returncode == 0, result.stderr
    assert "PATH" not in result.stderr


def test_vox_service_restarts_on_crashes_but_not_on_config_errors():
    unit = configparser.ConfigParser(interpolation=None, strict=True)
    unit.optionxform = str
    unit.read_string((REPO / "packaging/linux/vox.service").read_text())
    assert unit["Service"]["ExecStart"] == "%h/.local/bin/vox"
    assert unit["Service"]["Restart"] == "on-failure"
    assert unit["Service"]["RestartPreventExitStatus"] == "78"
    assert unit["Service"]["UMask"] == "0077"
    assert unit["Install"]["WantedBy"] == "graphical-session.target"


# --- macOS -----------------------------------------------------------------------------------

def test_launch_agent_has_absolute_paths_and_private_logs(home):
    fake_venv(home)
    out = home / "agent.plist"
    template = REPO / "packaging/macos/com.runsonmypc.vox.plist"
    result = bash(home, f'render_plist {shlex.quote(str(template))} {shlex.quote(str(out))} "$MAC_LOGS/vox.log"')
    assert result.returncode == 0, result.stderr
    job = plistlib.loads(out.read_bytes())
    log = str(home / "Library/Logs/Vox/vox.log")
    assert job["Label"] == "com.runsonmypc.vox"
    # so Login Items in System Settings shows the agent as the launcher, Vox Transfer
    assert job["AssociatedBundleIdentifiers"] == ["com.runsonmypc.vox.launcher"]
    assert job["ProgramArguments"] == [str(home / ".local/bin/vox")]
    assert job["StandardOutPath"] == job["StandardErrorPath"] == log
    assert job["Umask"] == 0o077
    assert job["KeepAlive"] == {"SuccessfulExit": False}
    path = job["EnvironmentVariables"]["PATH"].split(":")
    assert "/opt/homebrew/bin" in path and "/usr/local/bin" in path and "/usr/bin" in path
    text = out.read_text()
    assert "@" not in text and "/tmp/vox" not in text and "Developer" not in text


def test_mac_logs_are_private_and_old_tmp_logs_are_removed(home):
    legacy = [home / "tmp/vox.stdout.log", home / "tmp/vox.stderr.log"]
    for path in legacy:
        path.parent.mkdir(exist_ok=True)
        path.write_text("old transcript")
    result = bash(home, f"LEGACY_LOGS=({' '.join(shlex.quote(str(p)) for p in legacy)})\nmac_logs")
    assert result.returncode == 0, result.stderr
    logs = home / "Library/Logs/Vox"
    assert logs.stat().st_mode & 0o777 == 0o700
    assert (logs / "vox.log").stat().st_mode & 0o777 == 0o600
    assert not any(p.exists() for p in legacy)


def test_mac_uninstall_removes_only_the_vox_launchers(home):
    venv = fake_venv(home)
    ours = home / "SystemApplications/Vox Transfer.app/Contents"
    old = home / "Applications/Vox.app/Contents"  # added by an install from before the rename
    other = home / "Applications/Vox Transfer.app/Contents"  # someone else's app of the same name
    for path in (ours, old, other):
        path.mkdir(parents=True)
    (ours / "Info.plist").write_text("<string>com.runsonmypc.vox.launcher</string>")
    (old / "Info.plist").write_text("<string>com.runsonmypc.vox.launcher</string>")
    (other / "Info.plist").write_text("<string>com.example.vox</string>")
    agent = home / "Library/LaunchAgents/com.runsonmypc.vox.plist"
    agent.parent.mkdir(parents=True)
    agent.touch()
    logs = home / "Library/Logs/Vox/vox.log"
    logs.parent.mkdir(parents=True)
    logs.touch()
    script = """
        APPLICATIONS="$HOME/SystemApplications"
        uname() { echo Darwin; }
        id() { echo 501; }
        launchctl() { echo "launchctl $*" >>"$HOME/calls"; }
        uninstall_vox
    """
    result = bash(home, script)
    assert result.returncode == 0, result.stderr
    assert not ours.exists() and not old.exists() and other.exists()
    assert not agent.exists() and not venv.exists()
    assert logs.exists()
    assert "launchctl bootout gui/501/com.runsonmypc.vox" in (home / "calls").read_text()


MAC = """
    APPLICATIONS="$HOME/SystemApplications"
    uname() { echo Darwin; }
    id() { echo 501; }
    launchctl() { :; }
    app_icon() { echo icon >"$1"; }
"""


def other_app(path: Path) -> dict[str, bytes]:
    """Another vendor's app bundle at `path`, as {relative path: contents} to compare later."""
    (path / "Contents/MacOS").mkdir(parents=True)
    (path / "Contents/Frameworks").mkdir()
    (path / "Contents/Info.plist").write_text("<string>com.example.vox-player</string>")
    (path / "Contents/MacOS/VOX").write_text("the player")
    return bundle(path)


def bundle(path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(path)): p.read_bytes() if p.is_file() else b"" for p in path.rglob("*")}


def is_launcher(path: Path) -> bool:
    return "com.runsonmypc.vox.launcher" in (path / "Contents/Info.plist").read_text()


def own_launcher(path: Path) -> None:
    """A launcher that an earlier run of this user's install.sh added at `path`."""
    (path / "Contents/MacOS").mkdir(parents=True)
    (path / "Contents/Info.plist").write_text("<string>com.runsonmypc.vox.launcher</string>")
    (path / "Contents/MacOS/Vox").write_text("an older launcher")


def test_mac_launcher_never_writes_into_another_app_named_vox_transfer(home):
    fake_venv(home)
    other = home / "SystemApplications/Vox Transfer.app"
    before = other_app(other)
    result = bash(home, MAC + "mac_launcher")
    assert result.returncode == 0, result.stderr
    assert bundle(other) == before
    assert is_launcher(home / "Applications/Vox Transfer.app")
    assert (home / "Applications/Vox Transfer.app/Contents/MacOS/Vox").read_text().startswith("#!/bin/bash")

    # and uninstalling removes only the launcher it added
    result = bash(home, MAC + "uninstall_vox")
    assert result.returncode == 0, result.stderr
    assert bundle(other) == before
    assert not (home / "Applications/Vox Transfer.app").exists()


def test_mac_launcher_never_removes_another_app_named_vox(home):
    """The default APFS volume ignores case, so another vendor's VOX.app is the same path as the old Vox.app."""
    fake_venv(home)
    other = home / "SystemApplications/Vox.app"
    before = other_app(other)
    result = bash(home, MAC + "mac_launcher")
    assert result.returncode == 0, result.stderr
    assert bundle(other) == before
    assert is_launcher(home / "SystemApplications/Vox Transfer.app")

    result = bash(home, MAC + "uninstall_vox")
    assert result.returncode == 0, result.stderr
    assert bundle(other) == before
    assert not (home / "SystemApplications/Vox Transfer.app").exists()


@pytest.mark.parametrize("folder", ["SystemApplications", "Applications"])
def test_mac_launcher_replaces_the_vox_launcher_from_before_the_rename(home, folder):
    fake_venv(home)
    (home / "SystemApplications").mkdir()
    old = home / folder / "Vox.app"
    own_launcher(old)
    result = bash(home, MAC + "mac_launcher")
    assert result.returncode == 0, result.stderr
    assert is_launcher(home / "SystemApplications/Vox Transfer.app")
    assert not old.exists()


def test_mac_launcher_is_skipped_when_both_places_have_another_app_named_vox_transfer(home):
    fake_venv(home)
    apps = [home / "SystemApplications/Vox Transfer.app", home / "Applications/Vox Transfer.app"]
    before = [other_app(app) for app in apps]
    result = bash(home, MAC + "mac_launcher")
    assert result.returncode == 0, result.stderr
    assert [bundle(app) for app in apps] == before
    assert "no Vox Transfer launcher was added" in result.stderr
    assert "launchctl kickstart gui/501/com.runsonmypc.vox" in result.stderr


def test_mac_launcher_updates_its_own_launcher_in_place(home):
    fake_venv(home)
    ours = home / "SystemApplications/Vox Transfer.app"
    own_launcher(ours)
    result = bash(home, MAC + "mac_launcher")
    assert result.returncode == 0, result.stderr
    assert (ours / "Contents/MacOS/Vox").read_text().startswith("#!/bin/bash")
    info = plistlib.loads((ours / "Contents/Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == "com.runsonmypc.vox.launcher"
    # the names Finder, Spotlight and Login Items show
    assert info["CFBundleName"] == info["CFBundleDisplayName"] == "Vox Transfer"
    assert not (home / "Applications").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to a read-only folder")
def test_mac_launcher_goes_to_the_users_applications_when_the_shared_folder_is_read_only(home):
    fake_venv(home)
    shared = home / "SystemApplications"
    shared.mkdir()
    shared.chmod(0o555)
    try:
        result = bash(home, MAC + "mac_launcher")
    finally:
        shared.chmod(0o755)
    assert result.returncode == 0, result.stderr
    assert is_launcher(home / "Applications/Vox Transfer.app")
    assert not (shared / "Vox Transfer.app").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root can delete a read-only launcher")
def test_mac_uninstall_carries_on_when_the_launcher_cannot_be_deleted(home):
    venv = fake_venv(home)
    (home / ".local/bin").mkdir(parents=True)
    (home / ".local/bin/vox").symlink_to(venv / "bin/vox")
    launcher = home / "Applications/Vox Transfer.app"
    own_launcher(launcher)
    locked = [launcher / "Contents/MacOS", launcher / "Contents", launcher]
    for path in locked:
        path.chmod(0o555)
    try:
        result = bash(home, MAC + "uninstall_vox")
    finally:
        for path in reversed(locked):
            path.chmod(0o755)
    assert result.returncode == 0, result.stderr
    assert f"could not remove {launcher}" in result.stderr
    assert not venv.exists() and not (home / ".local/bin/vox").is_symlink()
    assert "Vox Transfer is uninstalled" in result.stdout


# --- Bootstrap from a release ----------------------------------------------------------------

def make_release(tmp_path: Path, version: str = "9.9.9", tamper: bool = False) -> Path:
    """A release directory like the GitHub assets: a source tarball and its SHA256SUMS."""
    release = tmp_path / "release"
    tree = tmp_path / "src" / f"vox-{version}"
    tree.mkdir(parents=True)
    (tree / "install.sh").write_text('echo "$@" >"$HOME/installed-with"\n')
    release.mkdir()
    archive = release / f"vox-{version}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(tree, arcname=tree.name)
    digest = sha256(archive.read_bytes()).hexdigest()
    if tamper:
        digest = digest[::-1]
    (release / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n0000  vox_{version}_amd64.deb\n")
    return release


FAKE_CURL = """
curl() {
    local out="" url="" head=0
    while [ $# -gt 0 ]; do
        case "$1" in
        -o) out=$2; shift ;;
        -w) shift ;;
        -fsSLI) head=1 ;;
        -*) ;;
        *) url=$1 ;;
        esac
        shift
    done
    if [ "$head" = 1 ]; then printf '%s' "$LATEST"; return; fi
    echo "$url" >>"$HOME/downloads"
    cp "$RELEASE/${url##*/}" "$out"
}
"""


def test_bootstrap_installs_the_verified_latest_release(home, tmp_path):
    release = make_release(tmp_path)
    result = bash(home, FAKE_CURL + "bootstrap --no-service", RELEASE=str(release),
                  LATEST="https://github.com/runsonmypc/vox/releases/tag/v9.9.9")
    assert result.returncode == 0, result.stderr
    assert (home / "installed-with").read_text().strip() == "--no-service"
    assert (home / "downloads").read_text().split() == [
        "https://github.com/runsonmypc/vox/releases/download/v9.9.9/vox-9.9.9.tar.gz",
        "https://github.com/runsonmypc/vox/releases/download/v9.9.9/SHA256SUMS",
    ]
    assert not list(home.glob("vox-install.*"))  # the download is cleaned up


def test_bootstrap_refuses_a_download_that_fails_its_checksum(home, tmp_path):
    release = make_release(tmp_path, tamper=True)
    result = bash(home, FAKE_CURL + "bootstrap", RELEASE=str(release),
                  LATEST="https://github.com/runsonmypc/vox/releases/tag/v9.9.9")
    assert result.returncode == 1
    assert "does not match its SHA256SUMS entry" in result.stderr
    assert not (home / "installed-with").exists()


def test_bootstrap_needs_a_published_release(home, tmp_path):
    result = bash(home, FAKE_CURL + "bootstrap", RELEASE=str(tmp_path),
                  LATEST="https://github.com/runsonmypc/vox/releases")
    assert result.returncode == 1
    assert "could not find the latest Vox Transfer release" in result.stderr
