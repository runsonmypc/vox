#!/usr/bin/env bash
# Run install.sh from a source tree the way a user would on a fresh Ubuntu: install, update over
# the first install, then uninstall.
#
#   scripts/test-install-linux.sh
#
# CI runs this as root in a clean ubuntu:24.04 container. install.sh refuses root, so this adds a
# user with sudo, and it checks that the machine has no C compiler, which installing must never need.
# The login service is not tested: a container has no systemd user session.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run this as root in a throwaway container or VM"
src=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
user=vox-tester
home=/home/$user
venv=$home/.local/share/vox/venv

apt-get update -q
# What every Ubuntu desktop has, minus a compiler
apt-get install -y -q --no-install-recommends python3 sudo ca-certificates
if command -v cc >/dev/null || command -v gcc >/dev/null; then
    die "this test needs a machine without a C compiler"
fi
# install.sh must cope with package lists that are missing or stale
rm -rf /var/lib/apt/lists
mkdir -p /var/lib/apt/lists/partial

useradd --create-home --shell /bin/bash "$user"
printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$user" >"/etc/sudoers.d/$user"
chmod 440 "/etc/sudoers.d/$user"
# pip builds Vox inside the tree, so the user gets a writable copy
cp -r "$src" "$home/vox"
chown -R "$user:" "$home/vox"
as_user() { sudo -u "$user" -H "$@"; }

as_user bash "$home/vox/install.sh" --no-service
[ "$(readlink "$home/.local/bin/vox")" = "$venv/bin/vox" ] || die "install.sh did not link ~/.local/bin/vox"
for tool in xdotool xclip xprop; do
    command -v "$tool" >/dev/null || die "install.sh did not install $tool"
done
as_user "$home/.local/bin/vox" --help >/dev/null
as_user "$venv/bin/python" -c 'import gi, cairo, vox.ui.tray; gi.require_foreign("cairo")'

# An update replaces the virtualenv and keeps nothing of the old one
as_user bash "$home/vox/install.sh" --no-service
[ ! -e "$venv.old" ] || die "the update left $venv.old behind"
as_user "$home/.local/bin/vox" --help >/dev/null

as_user mkdir -p "$home/.config/vox"
as_user touch "$home/.config/vox/config.toml"
as_user bash "$home/vox/install.sh" --uninstall
[ ! -e "$venv" ] || die "--uninstall left $venv behind"
if [ -e "$home/.local/bin/vox" ] || [ -L "$home/.local/bin/vox" ]; then die "--uninstall left ~/.local/bin/vox behind"; fi
[ -f "$home/.config/vox/config.toml" ] || die "--uninstall removed the settings"
echo "install.sh: install, update and uninstall passed"
