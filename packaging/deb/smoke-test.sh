#!/usr/bin/env bash
# Install a built Vox .deb on a clean Ubuntu, check that it works, upgrade over it, remove it,
# reinstall it, then purge it.
#
#   packaging/deb/smoke-test.sh DEB
#
# CI runs this as root in a fresh ubuntu:24.04 container, so the package's Depends alone have to
# be enough for Vox to import; a runner image has too much preinstalled to prove that.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ $# -eq 1 ] || die "usage: smoke-test.sh DEB"
[ "$(id -u)" -eq 0 ] || die "run this as root in a throwaway container or VM"
deb=$(realpath "$1")
version=$(dpkg-deb --field "$deb" Version)
unit=/usr/lib/systemd/user/vox.service
login_link=/etc/systemd/user/graphical-session.target.wants/vox.service
autostart=/etc/xdg/autostart/vox.desktop

apt-get update -q
# A desktop has systemd, which the maintainer scripts use; the container image does not
apt-get install -y -q systemd
apt-get install -y -q "$deb"

[ "$(readlink -f /usr/bin/vox)" = /opt/vox/venv/bin/vox ] || die "/usr/bin/vox does not run /opt/vox/venv/bin/vox"
vox --version | grep -qF "$version" || die "vox --version does not report $version"
for file in "$unit" /usr/share/applications/vox.desktop "$autostart" \
    /usr/share/icons/hicolor/256x256/apps/vox.png /usr/share/doc/vox/copyright; do
    [ -f "$file" ] || die "the package did not install $file"
done
grep -qx 'ExecStart=/usr/bin/vox' "$unit" || die "$unit does not run /usr/bin/vox"
[ "$(systemctl --global is-enabled vox.service)" = enabled ] || die "vox.service does not start at login"
[ -L "$login_link" ] || die "postinst did not create $login_link"

# pynput and GTK need a display to import; xvfb is not a dependency, so it comes after the check above
apt-get install -y -q xvfb xauth
py=/opt/vox/venv/bin/python
xvfb-run -a "$py" -c '
import sys
import gi, vox.daemon, vox.ui.tray, pystray
assert "pystray._appindicator" in sys.modules, "pystray fell back from AppIndicator: " + pystray.Icon.__module__
'
# GTK 3 (the tray) and GTK 4 (the windows) cannot share a process
xvfb-run -a "$py" -c '
import importlib, pkgutil
import vox.ui.gtk as windows
for module in pkgutil.iter_modules(windows.__path__, windows.__name__ + "."):
    importlib.import_module(module.name)
'

# Reinstalling runs the upgrade path of the maintainer scripts, which keeps the login setting
apt-get install -y -q --reinstall "$deb"
[ "$(systemctl --global is-enabled vox.service)" = enabled ] || die "vox.service is not enabled after an upgrade"
systemctl --global disable vox.service
apt-get install -y -q --reinstall "$deb"
[ ! -L "$login_link" ] || die "an upgrade enabled vox.service again after an administrator disabled it"
systemctl --global enable vox.service

apt-get remove -y -q vox
[ ! -e /opt/vox ] || die "/opt/vox is left after removal"
if [ -e /usr/bin/vox ] || [ -L /usr/bin/vox ]; then die "/usr/bin/vox is left after removal"; fi
[ ! -e "$unit" ] || die "$unit is left after removal"
[ ! -L "$login_link" ] || die "$login_link is left after removal"
[ -f "$autostart" ] || die "removal deleted $autostart, a conffile that only a purge deletes"

# Installing a removed Vox again starts it at login again
apt-get install -y -q "$deb"
[ "$(systemctl --global is-enabled vox.service)" = enabled ] || die "vox.service is not enabled after reinstalling"

apt-get purge -y -q vox
for path in "$autostart" "$login_link" /var/lib/vox /opt/vox; do
    if [ -e "$path" ] || [ -L "$path" ]; then die "$path is left after purge"; fi
done
echo "vox $version: install, upgrade, removal, reinstall and purge passed"
