#!/usr/bin/env bash
# Install, update or remove Vox Transfer for the current user on macOS or Linux.
#
#   ./install.sh               install or update, start Vox Transfer at login, and add a Vox Transfer launcher
#   ./install.sh --no-service  install or update only
#   ./install.sh --uninstall   remove Vox Transfer, keeping its settings and dictation history
#
# Run from a source tree, it installs that tree. Run on its own (for example
# `curl -fsSL https://github.com/runsonmypc/vox/releases/latest/download/install.sh | bash`),
# it downloads the latest release, checks it against the release's SHA256SUMS and installs it.
#
# Vox Transfer gets its own virtualenv in ~/.local/share/vox/venv and a ~/.local/bin/vox link, so the
# source tree can be deleted afterwards. Re-run to update.
set -euo pipefail

REPO_URL=https://github.com/runsonmypc/vox
LABEL=com.runsonmypc.vox
VENV="$HOME/.local/share/vox/venv"
VENV_OLD="$VENV.old"
BIN="$HOME/.local/bin/vox"
APPLICATIONS=/Applications
# The macOS launcher, and the name it had before the app became Vox Transfer
LAUNCHER="Vox Transfer.app"
OLD_LAUNCHER=Vox.app
MAC_LOGS="$HOME/Library/Logs/Vox"
# Where launchd wrote Vox's logs before 1.0, readable by every account
LEGACY_LOGS=(/tmp/vox.stdout.log /tmp/vox.stderr.log)
# Directories launchd's minimal PATH lacks: Homebrew's whisper-cli and tmux live there
MAC_PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
BOOTSTRAP_DIR=""

say() { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: install.sh [--no-service | --uninstall]

  (no option)    install or update Vox Transfer, start it at login, and add a Vox Transfer launcher
  --no-service   install or update Vox Transfer only
  --uninstall    remove Vox Transfer, keeping its settings and dictation history
EOF
}

# --- Source or release -------------------------------------------------------------------

# The source tree this script is part of; fails when it runs from a pipe or a lone download
source_tree() {
    local script=${BASH_SOURCE[0]:-} dir
    [ -n "$script" ] && [ -f "$script" ] || return 1
    dir=$(cd "$(dirname "$script")" && pwd)
    [ -f "$dir/pyproject.toml" ] && [ -f "$dir/requirements.lock" ] && [ -d "$dir/vox" ] || return 1
    printf '%s\n' "$dir"
}

sha256() {
    if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1
}

# Check $1/$2 against the hash $1/SHA256SUMS lists for it
verify_checksum() {
    local expected
    expected=$(awk -v f="$2" '$2 == f || $2 == "*" f { print $1; exit }' "$1/SHA256SUMS")
    [ -n "$expected" ] || die "SHA256SUMS does not list $2"
    [ "$(sha256 "$1/$2")" = "$expected" ] || die "$2 does not match its SHA256SUMS entry; the download is damaged"
}

# Download the latest release, verify it, and run its install.sh with the same options
bootstrap() {
    local url tag version archive
    command -v curl >/dev/null || die "install.sh needs curl to download Vox Transfer"
    url=$(curl -fsSLI -o /dev/null -w '%{url_effective}' "$REPO_URL/releases/latest") ||
        die "could not reach $REPO_URL"
    tag=${url##*/}
    case "$tag" in
    v[0-9]*) ;;
    *) die "could not find the latest Vox Transfer release at $REPO_URL/releases" ;;
    esac
    version=${tag#v}
    archive="vox-$version.tar.gz"
    BOOTSTRAP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/vox-install.XXXXXX")
    trap 'rm -rf "$BOOTSTRAP_DIR"' EXIT
    say "Downloading Vox Transfer $version"
    curl -fsSL -o "$BOOTSTRAP_DIR/$archive" "$REPO_URL/releases/download/$tag/$archive"
    curl -fsSL -o "$BOOTSTRAP_DIR/SHA256SUMS" "$REPO_URL/releases/download/$tag/SHA256SUMS"
    verify_checksum "$BOOTSTRAP_DIR" "$archive"
    tar -xzf "$BOOTSTRAP_DIR/$archive" -C "$BOOTSTRAP_DIR"
    [ -f "$BOOTSTRAP_DIR/vox-$version/install.sh" ] || die "$archive has no install.sh"
    bash "$BOOTSTRAP_DIR/vox-$version/install.sh" "$@"
}

# --- Virtualenv ----------------------------------------------------------------------------

# A run that stopped midway left the last working virtualenv aside; put it back first
recover_old_venv() {
    [ -e "$VENV_OLD" ] || return 0
    rm -rf "$VENV"
    mv "$VENV_OLD" "$VENV"
}

restore_old_venv() {
    local status=$?
    rm -rf "$VENV"
    if [ -e "$VENV_OLD" ]; then
        mv "$VENV_OLD" "$VENV"
        warn "the update failed, so the previous Vox Transfer stays installed"
    fi
    exit "$status"
}

# Build a new virtualenv with "$@" where the old one was (venvs can't be moved), keeping the old
# one aside until the new one passes smoke_test, and putting it back if anything fails
replace_venv() {
    recover_old_venv
    mkdir -p "$(dirname "$VENV")"
    if [ -e "$VENV" ]; then mv "$VENV" "$VENV_OLD"; fi
    trap restore_old_venv EXIT
    trap 'exit 130' INT TERM
    "$@"
    smoke_test
    trap - EXIT INT TERM
    rm -rf "$VENV_OLD"
}

# Import what Vox needs at startup; none of it needs a display
smoke_test() {
    local modules=vox.audio,vox.history,vox.keystore,vox.streaming,vox.transcribe,vox.ui.tray
    case "$(uname -s)" in
    Darwin) modules="$modules,vox.daemon" ;;  # pynput needs an X display on Linux
    Linux) modules="$modules,gi,cairo" ;;
    esac
    # -P and cd /: run from the source tree, python -c would import its vox/ instead of the venv's
    (cd / && "$VENV/bin/python" -P -c "import $modules") || die "the new Vox Transfer environment does not work"
    "$VENV/bin/vox" --help >/dev/null || die "the new vox command does not run"
}

# The binary a virtualenv's python resolves to, or nothing
venv_python() {
    [ -x "$1/bin/python" ] || return 0
    "$1/bin/python" -c 'import os, sys; print(os.path.realpath(sys.executable))' 2>/dev/null || true
}

# --- Linux ---------------------------------------------------------------------------------

# What a Debian package provides, for users of other distributions
describe_package() {
    case "$1" in
    libportaudio2) echo "PortAudio" ;;
    python3-venv) echo "Python's venv module" ;;
    python3-gi) echo "PyGObject" ;;
    python3-gi-cairo) echo "PyGObject Cairo drawing bridge" ;;
    gir1.2-gtk-3.0) echo "GTK 3 introspection data (tray and overlay)" ;;
    gir1.2-gtk-4.0) echo "GTK 4 introspection data" ;;
    gir1.2-adw-1) echo "libadwaita 1.5+ introspection data" ;;
    gir1.2-ayatanaappindicator3-0.1) echo "Ayatana AppIndicator introspection data" ;;
    x11-utils) echo "xprop (X11 utilities)" ;;
    *) echo "$1" ;;
    esac
}

# Debian/Ubuntu packages Vox needs, probed so nothing is reinstalled
linux_deps() {
    local py=$1 missing=() pkg
    command -v xdotool >/dev/null || missing+=(xdotool)
    command -v xclip >/dev/null || missing+=(xclip)
    # Without xprop every window looks like an ordinary app, so terminals get Ctrl+V and nothing pastes
    command -v xprop >/dev/null || missing+=(x11-utils)
    "$py" -c 'import ctypes.util, sys; sys.exit(not ctypes.util.find_library("portaudio"))' || missing+=(libportaudio2)
    "$py" -c 'import ensurepip' 2>/dev/null || missing+=(python3-venv)
    "$py" -c 'import gi' 2>/dev/null || missing+=(python3-gi)
    "$py" -c 'import gi, cairo; gi.require_foreign("cairo")' 2>/dev/null || missing+=(python3-gi-cairo)
    "$py" -c 'import gi; gi.require_version("Gtk", "3.0")' 2>/dev/null || missing+=(gir1.2-gtk-3.0)
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
    if ! command -v apt-get >/dev/null; then
        printf 'error: Vox Transfer needs these, which this installer can only install with apt:\n' >&2
        for pkg in "${missing[@]}"; do
            printf '  %s (Debian/Ubuntu: %s)\n' "$(describe_package "$pkg")" "$pkg" >&2
        done
        printf 'Install them with your package manager, then run install.sh again.\n' >&2
        exit 1
    fi
    say "Installing system packages: ${missing[*]}"
    # Stale or empty package lists can't find them: refresh the lists and try once more
    sudo apt-get install -y "${missing[@]}" || { sudo apt-get update && sudo apt-get install -y "${missing[@]}"; }
}

linux_venv() {
    local repo=$1 py=$2 pip
    "$py" -m venv "$VENV"
    # Share the distro's GTK/Cairo bindings; pip would need a compiler and dev headers
    ln -s "$("$py" -c 'import gi, os; print(os.path.dirname(gi.__file__))')" \
        "$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')/gi"
    ln -s "$("$py" -c 'import cairo, os; print(os.path.dirname(cairo.__file__))')" \
        "$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')/cairo"
    pip=("$VENV/bin/python" -m pip --quiet --disable-pip-version-check)
    # Exactly the locked, hash-checked packages, then Vox itself, built by the locked setuptools
    "${pip[@]}" install --require-hashes --no-deps -r "$repo/requirements.lock"
    "${pip[@]}" install --no-deps --no-build-isolation "$repo"
}

linux_install() {
    local repo=$1 py=/usr/bin/python3  # the system Python: python3-gi (the tray's GTK binding) only installs there
    [ -x "$py" ] || die "Vox Transfer needs the system Python 3 (/usr/bin/python3)"
    # Some dependencies publish wheels only for these versions; others would need a compiler
    "$py" -c 'import sys; sys.exit(not (3, 12) <= sys.version_info[:2] <= (3, 13))' ||
        die "Vox Transfer needs Python 3.12 or 3.13 as /usr/bin/python3; this system has $("$py" -V 2>&1)"
    linux_deps "$py"
    say "Installing Vox Transfer into $VENV"
    replace_venv linux_venv "$repo" "$py"
    linux_tray_host
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
    say "No tray icon until 'AppIndicator and KStatusNotifierItem Support' is enabled (extensions.gnome.org); Vox Transfer still works"
}

app_icon() {
    "$VENV/bin/python" -P -c 'import sys; from vox.ui.icons import make_app_icon; make_app_icon().save(sys.argv[1])' "$1"
}

# Launchers start the login service rather than vox itself: opening one while Vox runs does nothing,
# and on macOS Vox keeps the one identity its Microphone and Accessibility grants belong to
desktop_entry() {
    cat <<EOF
[Desktop Entry]
Type=Application
Name=Vox Transfer
Comment=$1
Exec=systemctl --user start vox.service
Icon=$2
Terminal=false
StartupNotify=false
Categories=Utility;
EOF
}

linux_launchers() {
    local icon="$HOME/.local/share/vox/vox.png"
    local entry="$HOME/.local/share/applications/vox.desktop" autostart="$HOME/.config/autostart/vox.desktop"
    mkdir -p "$(dirname "$icon")" "$(dirname "$entry")" "$(dirname "$autostart")"
    app_icon "$icon"
    desktop_entry "Start voice dictation" "$icon" >"$entry"
    # Starts Vox at login on desktops that never reach graphical-session.target; elsewhere a no-op
    desktop_entry "Start voice dictation at login" "$icon" >"$autostart"
}

linux_service() {
    local repo=$1 unit="$HOME/.config/systemd/user/vox.service"
    mkdir -p "$(dirname "$unit")"
    cp "$repo/packaging/linux/vox.service" "$unit"
    systemctl --user daemon-reload
    systemctl --user enable --quiet vox.service
    systemctl --user restart vox.service
    linux_launchers
    say "Vox Transfer is running (logs: journalctl --user -u vox -f)"
    say "After Quit, start it again from Vox Transfer in your applications"
}

wayland_warning() {
    [ "$(uname -s)" = Linux ] || return 0
    [ "${XDG_SESSION_TYPE:-}" = wayland ] || [ -n "${WAYLAND_DISPLAY:-}" ] || return 0
    warn "this is a Wayland session: Vox Transfer's hotkey and paste only work in X11 (XWayland) apps." \
        "For everything else, log in with an X11 session such as 'Ubuntu on Xorg'."
}

# --- macOS -----------------------------------------------------------------------------------

mac_venv() {
    local repo=$1 version=$2 uvpip
    # The exact patch version links the venv to that build rather than to uv's moving 3.12 alias,
    # so `uv python upgrade` can't swap the interpreter macOS granted Vox's permissions to
    uv venv --quiet --managed-python --python "$version" "$VENV"
    uvpip=(uv pip install --quiet --python "$VENV/bin/python")
    # Exactly the locked, hash-checked packages, then Vox itself, built by the locked setuptools
    "${uvpip[@]}" --require-hashes --no-deps -r "$repo/requirements.lock"
    "${uvpip[@]}" --no-deps --no-build-isolation --no-cache "$repo"
}

mac_install() {
    local repo=$1 old_python new_python version
    command -v uv >/dev/null || die "Vox Transfer installs with uv on macOS: brew install uv (or see https://docs.astral.sh/uv/)"
    uv python find --managed-python 3.12 >/dev/null 2>&1 || uv python install 3.12
    version=$("$(uv python find --managed-python 3.12)" -c 'import platform; print(platform.python_version())')
    recover_old_venv
    old_python=$(venv_python "$VENV")
    say "Installing Vox Transfer into $VENV"
    replace_venv mac_venv "$repo" "$version"
    new_python=$(venv_python "$VENV")
    if [ -n "$old_python" ] && [ "$old_python" != "$new_python" ]; then
        warn "Vox Transfer now runs on a different Python ($new_python)." \
            "macOS will ask again for Microphone, Accessibility and Input Monitoring (and Screen Recording for screen hints);" \
            "remove the old python3.12 entries in System Settings > Privacy & Security."
    fi
}

# A launcher this user's install.sh wrote. The default APFS volume ignores case, so another vendor's
# VOX.app is the same path as the old Vox.app: match the bundle id, and only in files the user owns
is_our_launcher() {
    [ -O "$1/Contents/Info.plist" ] && grep -qs "$LABEL.launcher" "$1/Contents/Info.plist"
}

# Where the launcher goes: /Applications when the user can write it, since Finder, Launchpad and
# Spotlight show apps there and admins need no sudo, otherwise ~/Applications. An app already
# there that is not our launcher is never written into.
launcher_path() {
    local dir
    for dir in "$APPLICATIONS" "$HOME/Applications"; do
        if [ -e "$dir/$LAUNCHER" ] || [ -L "$dir/$LAUNCHER" ]; then
            is_our_launcher "$dir/$LAUNCHER" || continue
        elif [ "$dir" = "$APPLICATIONS" ] && [ ! -w "$dir" ]; then
            continue
        fi
        printf '%s\n' "$dir/$LAUNCHER"
        return 0
    done
    return 1
}

# Delete this user's own launchers with these names, in either place; never another app
remove_launchers() {
    local name app
    for name in "$@"; do
        for app in "$APPLICATIONS/$name" "$HOME/Applications/$name"; do
            # One launcher that can't be deleted must not keep the rest of Vox installed
            if is_our_launcher "$app"; then rm -rf "$app" || warn "could not remove $app"; fi
        done
    done
}

mac_launcher() {
    local app
    if ! app=$(launcher_path); then
        warn "another app is already named Vox Transfer, so no Vox Transfer launcher was added." \
            "After Quit, start Vox Transfer again with: launchctl kickstart gui/$(id -u)/$LABEL"
        return 0
    fi
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
    <key>CFBundleDisplayName</key>
    <string>Vox Transfer</string>
    <key>CFBundleName</key>
    <string>Vox Transfer</string>
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
    # An install from before the rename added Vox.app; this launcher replaces it
    remove_launchers "$OLD_LAUNCHER"
    say "After Quit, start it again from Vox Transfer in Applications or Spotlight"
}

# Write the LaunchAgent from its template; launchd expands neither ~ nor $HOME, so paths are absolute
render_plist() {
    "$VENV/bin/python" - "$1" "$2" "@VOX_BIN@=$BIN" "@LOG_FILE@=$3" "@PATH@=$MAC_PATH" <<'EOF'
import plistlib
import sys

template, dest, *pairs = sys.argv[1:]
values = dict(pair.split("=", 1) for pair in pairs)


def fill(value):
    if isinstance(value, str):
        for placeholder, text in values.items():
            value = value.replace(placeholder, text)
        return value
    if isinstance(value, list):
        return [fill(v) for v in value]
    if isinstance(value, dict):
        return {k: fill(v) for k, v in value.items()}
    return value


with open(template, "rb") as f:
    job = fill(plistlib.load(f))
with open(dest, "wb") as f:
    plistlib.dump(job, f)
EOF
}

# Logs can hold dictated text (vox -v), so only the user may read them
mac_logs() {
    local old
    mkdir -p "$MAC_LOGS"
    chmod 700 "$MAC_LOGS"
    touch "$MAC_LOGS/vox.log"
    chmod 600 "$MAC_LOGS/vox.log"
    for old in "${LEGACY_LOGS[@]}"; do
        if [ -O "$old" ]; then rm -f "$old"; fi
    done
}

mac_service() {
    local repo=$1 plist="$HOME/Library/LaunchAgents/$LABEL.plist" started=0 error=""
    mac_logs
    mkdir -p "$(dirname "$plist")"
    render_plist "$repo/packaging/macos/$LABEL.plist" "$plist" "$MAC_LOGS/vox.log"
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    # launchd may still be retiring the old job right after bootout, so an attempt can fail with
    # "Bootstrap failed: 5: Input/output error"; retry quietly and report only a failure that lasts
    for _ in 1 2 3 4 5; do
        if error=$(launchctl bootstrap "gui/$(id -u)" "$plist" 2>&1); then started=1; break; fi
        sleep 1
    done
    [ "$started" -eq 1 ] || die "could not start the Vox Transfer LaunchAgent: $error"
    say "Vox Transfer is running (logs: $MAC_LOGS/vox.log)"
    mac_launcher
    say "macOS asks once for Microphone, Accessibility and Input Monitoring access, and for Screen Recording while screen hints are on"
}

# --- Install and uninstall -------------------------------------------------------------------

# macOS's default PATH lacks ~/.local/bin, and Ubuntu's adds it only at a login after it exists
path_warning() {
    local dir
    dir=$(dirname "$BIN")
    case ":$PATH:" in
    *":$dir:"*) ;;
    *) warn "$dir is not on your PATH, so a plain 'vox' is not found; run $BIN, or add $dir to PATH" ;;
    esac
}

install_vox() {
    local repo=$1 service=$2
    # setuptools packs whatever is left in build/lib into the wheel, including modules deleted since
    rm -rf "$repo/build"
    case "$(uname -s)" in
    Linux) linux_install "$repo" ;;
    Darwin) mac_install "$repo" ;;
    esac
    mkdir -p "$(dirname "$BIN")"
    ln -sfn "$VENV/bin/vox" "$BIN"

    # Vox asks for the OpenAI API key itself and keeps it in the system keychain, so the installer never handles it
    if [ "$service" -eq 1 ]; then
        case "$(uname -s)" in
        Linux) linux_service "$repo" ;;
        Darwin) mac_service "$repo" ;;
        esac
    fi
    say "Vox Transfer asks for your OpenAI API key when it needs one; change it later on the Transcription page of Settings… in its menu"
    wayland_warning
    path_warning
    say "Done. Run '$BIN --help' for options."
}

uninstall_vox() {
    case "$(uname -s)" in
    Linux)
        if command -v systemctl >/dev/null; then
            systemctl --user disable --now vox.service 2>/dev/null || true
        fi
        rm -f "$HOME/.config/systemd/user/vox.service" "$HOME/.config/autostart/vox.desktop" \
            "$HOME/.local/share/applications/vox.desktop" "$HOME/.local/share/vox/vox.png"
        if command -v systemctl >/dev/null; then
            systemctl --user daemon-reload 2>/dev/null || true
            systemctl --user reset-failed vox.service 2>/dev/null || true
        fi
        ;;
    Darwin)
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
        remove_launchers "$LAUNCHER" "$OLD_LAUNCHER"
        ;;
    esac
    rm -rf "$VENV" "$VENV_OLD"
    if [ -L "$BIN" ]; then rm -f "$BIN"; fi
    say "Vox Transfer is uninstalled"
    say "Kept your settings in ~/.config/vox and your dictation history in ~/.local/share/vox; delete those folders to remove them"
    case "$(uname -s)" in
    Linux)
        say "Your OpenAI API key stays in your login keyring; remove it with: secret-tool clear service vox username openai_api_key"
        if dpkg-query -W -f='${Status}' vox 2>/dev/null | grep -q 'ok installed'; then
            say "The Vox Transfer .deb package is still installed; remove it with: sudo apt remove vox"
        fi
        ;;
    Darwin)
        say "Kept the logs in ~/Library/Logs/Vox"
        say "Your OpenAI API key stays in your login keychain (item \"vox\"); remove it with Keychain Access"
        say "Remove Vox Transfer's python3.12 entries in System Settings > Privacy & Security if you no longer need them"
        ;;
    esac
}

main() {
    local service=1 action=install repo
    [ "$#" -le 1 ] || { usage >&2; exit 2; }
    case "${1:-}" in
    "") ;;
    --no-service) service=0 ;;
    --uninstall) action=uninstall ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
    esac
    [ "$(id -u)" -ne 0 ] || die "run install.sh as your own user, not as root; it asks for sudo itself when it needs system packages"
    case "$(uname -s)" in
    Linux | Darwin) ;;
    *) die "unsupported system: $(uname -s)" ;;
    esac

    if [ "$action" = uninstall ]; then
        uninstall_vox
    elif repo=$(source_tree); then
        install_vox "$repo" "$service"
    else
        bootstrap "$@"
    fi
}

# Everything above only defines functions, so `curl | bash` runs nothing until the whole script
# has arrived. Tests source the file to call its functions without running main.
if ! (return 0 2>/dev/null); then
    main "$@"
fi
