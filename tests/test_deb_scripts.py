"""The .deb maintainer scripts decide whether Vox starts at login; run them as dpkg would, in a scratch root."""

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# systemctl --global as the maintainer scripts use it: the login link is the whole state
FAKE_SYSTEMCTL = """#!/bin/sh
link="$ROOT/etc/systemd/user/graphical-session.target.wants/vox.service"
case "$*" in
"--global enable vox.service") mkdir -p "${link%/*}" && ln -sf /usr/lib/systemd/user/vox.service "$link" ;;
"--global disable vox.service") rm -f "$link" ;;
"--global is-enabled --quiet vox.service") [ -L "$link" ] ;;
*) exit 1 ;;
esac
"""

# An absolute path in a script, such as /opt/vox or /var/lib/vox
SYSTEM_PATH = re.compile(r"(?<=[\s=\"'])/(?=(?:etc|opt|run|var)/)")


class Dpkg:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path / "root"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        systemctl = bin_dir / "systemctl"
        systemctl.write_text(FAKE_SYSTEMCTL)
        systemctl.chmod(0o755)
        self.env = {**os.environ, "ROOT": str(self.root), "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
        self.scripts = {}
        for name in ("postinst", "prerm", "postrm"):
            text = SYSTEM_PATH.sub("@ROOT@/", (REPO / "packaging" / "deb" / name).read_text())
            assert not SYSTEM_PATH.search(text), f"{name} would touch the real system"
            self.scripts[name] = tmp_path / name
            self.scripts[name].write_text(text.replace("@ROOT@", str(self.root)))

    def run(self, script: str, *args: str) -> None:
        subprocess.run(["sh", str(self.scripts[script]), *args], env=self.env, check=True)

    def systemctl(self, *args: str) -> None:
        subprocess.run(["systemctl", *args], env=self.env, check=True)

    def install(self, configured_version: str = "") -> None:
        (self.root / "opt" / "vox").mkdir(parents=True, exist_ok=True)
        self.run("postinst", "configure", configured_version)

    def upgrade(self, old: str = "1.0.0", new: str = "1.0.1") -> None:
        self.run("prerm", "upgrade", new)
        self.run("postinst", "configure", old)

    def remove(self) -> None:
        self.run("prerm", "remove")
        self.run("postrm", "remove")

    def purge(self) -> None:
        self.run("postrm", "purge")

    @property
    def starts_at_login(self) -> bool:
        return (self.root / "etc/systemd/user/graphical-session.target.wants/vox.service").is_symlink()


@pytest.fixture
def dpkg(tmp_path) -> Dpkg:
    return Dpkg(tmp_path)


def test_a_first_install_starts_vox_at_login(dpkg):
    dpkg.install()
    assert dpkg.starts_at_login


def test_an_upgrade_keeps_vox_starting_at_login(dpkg):
    dpkg.install()
    dpkg.upgrade()
    assert dpkg.starts_at_login


def test_an_upgrade_keeps_an_administrators_disable(dpkg):
    dpkg.install()
    dpkg.systemctl("--global", "disable", "vox.service")
    dpkg.upgrade()
    assert not dpkg.starts_at_login


def test_removal_stops_login_start_and_reinstalling_restores_it(dpkg):
    dpkg.install()
    dpkg.remove()
    assert not dpkg.starts_at_login
    assert not (dpkg.root / "opt" / "vox").exists()
    # The autostart conffile keeps the package in dpkg's config-files state, so the version comes back
    dpkg.install("1.0.0")
    assert dpkg.starts_at_login
    assert not (dpkg.root / "var" / "lib" / "vox" / "enabled-at-removal").exists()


def test_reinstalling_keeps_an_administrators_disable(dpkg):
    dpkg.install()
    dpkg.systemctl("--global", "disable", "vox.service")
    dpkg.remove()
    dpkg.install("1.0.0")
    assert not dpkg.starts_at_login


def test_purge_forgets_the_login_setting(dpkg):
    dpkg.install()
    dpkg.remove()
    dpkg.purge()
    assert not (dpkg.root / "var" / "lib" / "vox").exists()
    dpkg.install()
    assert dpkg.starts_at_login
