#!/usr/bin/env bash
# Build vox_VERSION_ARCH.deb for Ubuntu 24.04 and derivatives.
#
#   packaging/deb/build-deb.sh VERSION ARCH [WHEEL]
#
# Runs on the target distribution and architecture (CI: ubuntu-24.04 and ubuntu-24.04-arm) with
# uv, python3-venv and passwordless sudo. A virtualenv can't be moved once built, so this builds
# it where the package installs it, /opt/vox/venv, copies it into the package, and removes it
# again. WHEEL is Vox's wheel from `uv build`; without it, the wheel is built here.
set -euo pipefail
umask 022  # the package's files are world-readable, as dpkg expects

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

if [ $# -lt 2 ] || [ $# -gt 3 ]; then die "usage: build-deb.sh VERSION ARCH [WHEEL]"; fi
version=$1 arch=$2 wheel=${3:-}
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
here="$repo/packaging/deb"
out="$repo/dist"
prefix=/opt/vox
venv="$prefix/venv"
python=/usr/bin/python3

[ "$(dpkg --print-architecture)" = "$arch" ] || die "build the $arch package on an $arch machine"
"$python" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))' ||
    die "the package targets Python 3.12 (Ubuntu 24.04); this system has $("$python" -V 2>&1)"
[ ! -e "$prefix" ] || die "$prefix already exists; build on a clean machine"

work=$(mktemp -d)
cleanup() {
    sudo rm -rf "$prefix"
    rm -rf "$work"
}
trap cleanup EXIT

if [ -z "$wheel" ]; then
    uv build --wheel --out-dir "$work/wheel" --build-constraints "$repo/requirements.lock" --require-hashes "$repo"
    wheel=$(echo "$work"/wheel/vox-*.whl)
fi
[ -f "$wheel" ] || die "no wheel at $wheel"
case "$(basename "$wheel")" in
"vox-$version-"*) ;;
*) die "$(basename "$wheel") is not Vox Transfer $version" ;;
esac

# The virtualenv, at the path it will run from
sudo install -d -o "$(id -u)" -g "$(id -g)" "$prefix"
"$python" -m venv --without-pip "$venv"
uv pip install --quiet --python "$venv/bin/python" --compile-bytecode \
    --require-hashes --no-deps -r "$repo/requirements.lock"
uv pip install --quiet --python "$venv/bin/python" --compile-bytecode --no-deps "$wheel"
# setuptools only builds Vox; the package doesn't need it
uv pip uninstall --quiet --python "$venv/bin/python" setuptools
# The tray and windows use the system's PyGObject (python3-gi), which the package depends on
ln -s /usr/lib/python3/dist-packages/gi \
    "$("$venv/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')/gi"
ln -s /usr/lib/python3/dist-packages/cairo \
    "$("$venv/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')/cairo"

# The package tree
root="$work/root"
mkdir -p "$root/opt" "$root/usr/bin" "$root/usr/lib/systemd/user" "$root/usr/share/applications" \
    "$root/etc/xdg/autostart" "$root/usr/share/icons/hicolor/256x256/apps" "$root/usr/share/doc/vox" \
    "$root/DEBIAN"
cp -a "$prefix" "$root/opt/"
ln -s "$venv/bin/vox" "$root/usr/bin/vox"
sed 's|^ExecStart=.*|ExecStart=/usr/bin/vox|' "$repo/packaging/linux/vox.service" >"$root/usr/lib/systemd/user/vox.service"
grep -qx 'ExecStart=/usr/bin/vox' "$root/usr/lib/systemd/user/vox.service" || die "vox.service has no ExecStart to replace"
install -m 644 "$here/vox.desktop" "$root/usr/share/applications/vox.desktop"
install -m 644 "$here/vox-autostart.desktop" "$root/etc/xdg/autostart/vox.desktop"
"$venv/bin/python" -P -c 'import sys; from vox.ui.icons import make_app_icon; make_app_icon(256).save(sys.argv[1])' \
    "$root/usr/share/icons/hicolor/256x256/apps/vox.png"
install -m 644 "$here/copyright" "$root/usr/share/doc/vox/copyright"
for script in postinst prerm postrm; do
    install -m 755 "$here/$script" "$root/DEBIAN/$script"
done
# Files under /etc are conffiles, so an upgrade keeps an administrator's edits (such as Hidden=true
# in the autostart entry) and only a purge deletes them
(cd "$root" && find etc -type f | sed 's|^|/|' | sort) >"$root/DEBIAN/conffiles"
cat >"$root/DEBIAN/control" <<EOF
Package: vox
Version: $version
Architecture: $arch
Maintainer: Alex <45095641+runsonmypc@users.noreply.github.com>
Installed-Size: $(du -sk --exclude=DEBIAN "$root" | cut -f1)
Depends: python3 (>= 3.12), python3 (<< 3.13), python3-gi, python3-gi-cairo, gir1.2-gtk-3.0, gir1.2-gtk-4.0, gir1.2-adw-1, gir1.2-ayatanaappindicator3-0.1, libportaudio2, xdotool, xclip, x11-utils
Suggests: tesseract-ocr, maim, gir1.2-atspi-2.0
Section: sound
Priority: optional
Homepage: https://github.com/runsonmypc/vox
Description: Vox Transfer, voice dictation for macOS and Linux
 Tap a key, speak, and Vox Transfer types what you said into the focused app.
 It transcribes with OpenAI or locally with whisper.cpp, and lives in the
 system tray. Vox Transfer starts at login for every user.
EOF

mkdir -p "$out"
deb="$out/vox_${version}_${arch}.deb"
dpkg-deb --root-owner-group -Zxz --build "$root" "$deb"
echo "$deb"
