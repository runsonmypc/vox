#!/usr/bin/env bash
# Install or update Vox for the current user on macOS or Linux.
#
#   ./install.sh               install, run Vox at login, and add a Vox launcher to start it after Quit
#   ./install.sh --no-service  install only
#
# Vox gets its own virtualenv in ~/.local/share/vox/venv and a ~/.local/bin/vox
# link, so this checkout can be deleted afterwards. Re-run to update.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/vox/venv"
BIN="$HOME/.local/bin/vox"
SERVICE=1
case "${1:-}" in
"") ;;
--no-service) SERVICE=0 ;;
*) sed -n '2,8s/^# \{0,1\}//p' "$0"; exit 2 ;;
esac

say() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Debian/Ubuntu packages Vox needs, probed so nothing is reinstalled
linux_deps() {
    local py=$1 missing=()
    command -v xdotool >/dev/null || missing+=(xdotool)
    command -v xclip >/dev/null || missing+=(xclip)
    "$py" -c 'import ctypes.util, sys; sys.exit(not ctypes.util.find_library("portaudio"))' || missing+=(libportaudio2)
    "$py" -c 'import ensurepip' 2>/dev/null || missing+=(python3-venv)
    "$py" -c 'import gi' 2>/dev/null || missing+=(python3-gi)
    # The history and vocabulary windows need GTK 4 and libadwaita 1.5+ (Ubuntu 24.04 ships 1.5)
    "$py" -c '
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")' 2>/dev/null || missing+=(gir1.2-gtk-4.0 gir1.2-adw-1)
    "$py" -c '
import gi
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
except ValueError:
    gi.require_version("AppIndicator3", "0.1")' 2>/dev/null || missing+=(gir1.2-ayatanaappindicator3-0.1)
    [ ${#missing[@]} -eq 0 ] && return
    command -v apt-get >/dev/null || die "install these packages (Debian names), then re-run: ${missing[*]}"
    say "Installing system packages: ${missing[*]}"
    sudo apt-get install -y "${missing[@]}"
}

has_tray_host() {
    local owned
    owned=$(gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
        --method org.freedesktop.DBus.NameHasOwner org.kde.StatusNotifierWatcher 2>/dev/null) || return 1
    [[ $owned == *true* ]]
}

# GNOME only shows tray icons through the AppIndicator extension; other desktops have a tray host built in
linux_tray_host() {
    local ext=appindicatorsupport@rgcjonas.gmail.com installed
    has_tray_host && return
    installed=$(gnome-extensions list 2>/dev/null) || return 0  # not GNOME
    if grep -qx "$ext" <<<"$installed"; then
        say "Enabling the GNOME AppIndicator extension"
        gnome-extensions enable "$ext" || true
    else
        say "Installing the GNOME AppIndicator extension: click Install in the dialog on screen"
        gdbus call --session --dest org.gnome.Shell.Extensions --object-path /org/gnome/Shell/Extensions \
            --method org.gnome.Shell.Extensions.InstallRemoteExtension "$ext" >/dev/null 2>&1 || true
    fi
    # GNOME's D-Bus helper can drop the reply while its dialog waits, so watch for the tray host instead
    for _ in $(seq 120); do has_tray_host && return; sleep 1; done
    say "No tray icon until 'AppIndicator and KStatusNotifierItem Support' is enabled (extensions.gnome.org); Vox still works"
}

app_icon() {
    "$VENV/bin/python" -c 'import sys; from vox.ui.icons import make_app_icon; make_app_icon().save(sys.argv[1])' "$1"
}

# Launchers start the login service rather than vox itself: opening one while Vox runs does nothing,
# and on macOS Vox keeps the one identity its Microphone and Accessibility grants belong to
linux_launcher() {
    local icon="$HOME/.local/share/vox/vox.png" entry="$HOME/.local/share/applications/vox.desktop"
    mkdir -p "$(dirname "$icon")" "$(dirname "$entry")"
    app_icon "$icon"
    cat >"$entry" <<EOF
[Desktop Entry]
Type=Application
Name=Vox
Comment=Start voice dictation
Exec=systemctl --user start vox.service
Icon=$icon
Terminal=false
StartupNotify=false
Categories=Utility;
EOF
}

mac_launcher() {
    local dir="$HOME/Applications"
    [ -w /Applications ] && dir=/Applications  # where Finder, Launchpad and Spotlight show apps; admins need no sudo
    local app="$dir/Vox.app"
    mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
    cat >"$app/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>Vox</string>
    <key>CFBundleIconFile</key>
    <string>Vox</string>
    <key>CFBundleIdentifier</key>
    <string>com.runsonmypc.vox.launcher</string>
    <key>CFBundleName</key>
    <string>Vox</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSUIElement</key>
    <true/>
</dict>
</plist>
EOF
    cat >"$app/Contents/MacOS/Vox" <<'EOF'
#!/bin/bash
# Start Vox's login service; does nothing while Vox is running
domain="gui/$(id -u)"
label=com.runsonmypc.vox
launchctl print "$domain/$label" >/dev/null 2>&1 ||
    launchctl bootstrap "$domain" "$HOME/Library/LaunchAgents/$label.plist"
exec launchctl kickstart "$domain/$label"
EOF
    chmod +x "$app/Contents/MacOS/Vox"
    app_icon "$app/Contents/Resources/Vox.icns"
    touch "$app"  # so Finder shows a changed icon
}

start_service() {
    case "$(uname -s)" in
    Linux)
        mkdir -p "$HOME/.config/systemd/user"
        cp "$REPO/vox.service" "$HOME/.config/systemd/user/vox.service"
        systemctl --user daemon-reload
        systemctl --user enable --quiet vox.service
        systemctl --user restart vox.service
        linux_launcher
        say "Vox is running (logs: journalctl --user -u vox -f)"
        say "After Quit, start it again from Vox in your applications"
        ;;
    Darwin)
        local plist="$HOME/Library/LaunchAgents/com.runsonmypc.vox.plist" started=0
        mkdir -p "$(dirname "$plist")"
        cp "$REPO/com.runsonmypc.vox.plist" "$plist"
        launchctl bootout "gui/$(id -u)/com.runsonmypc.vox" 2>/dev/null || true
        # launchd may still be retiring the old job immediately after bootout.
        for _ in 1 2 3 4 5; do
            if launchctl bootstrap "gui/$(id -u)" "$plist"; then started=1; break; fi
            sleep 1
        done
        [ "$started" -eq 1 ] || die "could not start the Vox LaunchAgent"
        mac_launcher
        say "Vox is running (logs: /tmp/vox.stderr.log)"
        say "After Quit, start it again from Vox in Applications or Spotlight"
        say "macOS asks once for Microphone, Accessibility and Input Monitoring access"
        ;;
    esac
}

# setuptools packs whatever is left in build/lib into the wheel, including modules deleted since
rm -rf "$REPO/build"

case "$(uname -s)" in
Linux)
    PY=/usr/bin/python3  # the system Python: python3-gi (the tray's GTK binding) only installs there
    [ -x "$PY" ] || die "python3 not found"
    "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 12))' || die "Vox needs Python 3.12+; the system has $("$PY" -V 2>&1)"
    linux_deps "$PY"
    say "Installing Vox into $VENV"
    rm -rf "$VENV"
    "$PY" -m venv "$VENV"
    # Share only python3-gi with the venv; pip can't build it without a compiler and dev headers
    ln -s "$("$PY" -c 'import gi, os; print(os.path.dirname(gi.__file__))')" \
        "$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')/gi"
    "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check "$REPO"
    linux_tray_host
    ;;
Darwin)
    command -v uv >/dev/null || die "Vox installs with uv on macOS: brew install uv (or see https://docs.astral.sh/uv/)"
    say "Installing Vox into $VENV"
    rm -rf "$VENV"
    uv venv --quiet --managed-python --python 3.12 "$VENV"
    uv pip install --quiet --python "$VENV/bin/python" "$REPO"
    ;;
*)
    die "unsupported system: $(uname -s)"
    ;;
esac
mkdir -p "$(dirname "$BIN")"
ln -sfn "$VENV/bin/vox" "$BIN"

# Vox asks for the OpenAI API key itself and keeps it in the system keychain, so the installer never handles it
if [ "$SERVICE" -eq 1 ]; then start_service; fi
say "Vox asks for your OpenAI API key when it needs one; change it later with Set API Key… in its menu"
say "Done. Run 'vox --help' for options."
