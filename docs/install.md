# Installing Vox Transfer

[Back to the README](../README.md)

- [What you need](#what-you-need)
- [Install](#install)
- [First start: your API key](#first-start-your-api-key)
- [Permissions](#permissions)
- [Wayland](#wayland)
- [Update](#update)
- [Uninstall](#uninstall)

## What you need

- **macOS** on Apple Silicon or Intel, with [uv](https://docs.astral.sh/uv/) (`brew install uv`).
  The installer uses uv to set up a private Python 3.12 for Vox Transfer.
- **Linux** with an X11 session and Python 3.12 or 3.13 as `/usr/bin/python3`, such as Ubuntu
  24.04 and distributions based on it, or Debian 13. On Debian-based systems the installer
  adds the system packages Vox Transfer needs; on other distributions you install them yourself (the
  installer lists them). Wayland sessions work only partly, see [Wayland](#wayland).
- An **OpenAI API key** for the OpenAI modes. Local whisper.cpp transcription needs no key.

## Install

Vox Transfer installs for your user only: its own virtualenv in `~/.local/share/vox/venv`, a `vox`
command in `~/.local/bin` (the installer tells you when that folder is not on your `PATH`), a login
service that starts it when you log in, and a Vox Transfer launcher in your applications. Run the
installer as yourself, not with `sudo`; it asks for `sudo` only to install missing Linux packages.

The command, folders, login service and `.deb` package use the short name `vox`; only the app is
called Vox Transfer.

On macOS the launcher is `/Applications/Vox Transfer.app`, or `~/Applications/Vox Transfer.app`
when you cannot write to `/Applications` or another app there is already called Vox Transfer. An
update replaces the `Vox.app` launcher that an earlier version added. The installer never changes
or removes an app it did not create, such as the VOX music player.

### One line

```sh
curl -fsSL https://github.com/runsonmypc/vox/releases/latest/download/install.sh | bash
```

This downloads the latest release, checks it against the release's `SHA256SUMS`, and installs it.
Options go after `bash -s --`, for example `... | bash -s -- --no-service` to install without the
login service.

### From the release tarball

Download `vox-<version>.tar.gz` and `SHA256SUMS` from the
[latest release](https://github.com/runsonmypc/vox/releases/latest), then:

```sh
sha256sum --check --ignore-missing SHA256SUMS   # macOS: shasum -a 256 --check --ignore-missing SHA256SUMS
tar -xzf vox-<version>.tar.gz
cd vox-<version>
./install.sh
```

`./install.sh --no-service` installs without starting Vox Transfer at login or adding the launcher.
You can delete the extracted folder afterwards. A git checkout works the same way.

### Debian and Ubuntu package

For Ubuntu 24.04 and other distributions based on it (amd64 and arm64), each release also has a
`.deb` that installs Vox Transfer for every user on the computer:

```sh
sudo apt install ./vox_<version>_amd64.deb
```

Vox Transfer then starts at each user's next login. To start it right away, open Vox Transfer from
your applications or run `systemctl --user start vox`. The package installs into `/opt/vox` and
pulls in its system dependencies. It suggests three optional packages for screen hints:
`gir1.2-atspi-2.0` reads window text through the accessibility interface, and `tesseract-ocr` with
`maim` reads windows that do not expose their text that way.

To stop Vox Transfer starting at login for everyone, run
`sudo systemctl --global disable vox.service` and add `Hidden=true` to
`/etc/xdg/autostart/vox.desktop`; updates keep both. One user can opt out with
`systemctl --user mask vox.service`.

The package and the per-user installer can coexist; the per-user install takes precedence for the
user who ran it.

## First start: your API key

In an OpenAI mode, Vox Transfer asks for your OpenAI API key when it starts without one, and again
if you dictate before saving one. You can also set, replace or remove it at any time on the
Transcription page of **Settings…** in its menu.

The key goes into the macOS Keychain or your Linux login keyring (GNOME Keyring, KWallet), never
into a file Vox Transfer writes in plain text, unless your Linux system has no keyring at all, in
which case it goes into `~/.config/vox/.env`, readable only by you. An `OPENAI_API_KEY` environment
variable overrides the stored key.

The key window refuses a key with an invisible or typographic character, such as a zero-width space
or a curly quote, which copying from a web page or a chat can pick up; copy the key again.

No key? Choose **Local (whisper.cpp)** instead: see
[Local transcription with whisper.cpp](settings.md#local-transcription-with-whispercpp).

## Permissions

### macOS

macOS asks you to allow these the first time Vox Transfer needs them. The prompts and the entries in
**System Settings > Privacy & Security** name **python3.12**, the Python that runs Vox Transfer.

| Permission | Why |
| --- | --- |
| Microphone | To record you. |
| Accessibility | To paste the transcript into the focused app (Vox Transfer presses Cmd+V for you). |
| Input Monitoring | To notice the hotkey from any app. |
| Screen Recording | Only for screen hints (`[context] screen`, on by default): Vox Transfer reads text in the focused window. Turn screen hints off and macOS never asks. |

If Accessibility is not allowed when Vox Transfer starts, its menu shows **Accessibility access
needed**. If a recording comes back as pure digital silence, which usually means Vox Transfer may
not use the microphone, the menu shows **Microphone is silent: check its permission** until a
recording has sound again.

These grants belong to that exact Python binary. If an update moves Vox Transfer to a different
Python build, the installer warns you, and macOS asks again; remove the old python3.12 entries from
the lists in System Settings.

### Linux

Linux has no permission prompts. Vox Transfer needs an X11 session (see [Wayland](#wayland)),
`xdotool` and `xclip` for pasting, `xprop` (x11-utils) to tell terminals from other windows, and on
GNOME the [AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/)
for its tray icon. The installer enables that extension, or asks GNOME to install it; click
**Install** in the dialog that appears.

## Wayland

Vox Transfer's hotkey, active-window detection and paste use X11. In a Wayland session (the default
on recent GNOME and KDE), they work only while an X11 app (running through XWayland) has focus;
native Wayland apps do not see the hotkey or the paste. Vox Transfer warns about this at install and
in its menu. For full support, choose an X11 session at the login screen, such as **Ubuntu on Xorg**
or **GNOME on Xorg**. macOS is not affected.

## Update

Run the installer again: the one-line command installs the latest release. Updating keeps your
settings and history. The new version is built next to the old one, and if anything fails the
previous Vox Transfer stays installed and running.

With the `.deb`, install the new package the same way as the first one.

## Uninstall

```sh
./install.sh --uninstall
# or, without a source tree:
curl -fsSL https://github.com/runsonmypc/vox/releases/latest/download/install.sh | bash -s -- --uninstall
```

This stops Vox Transfer and removes its login service, launcher, autostart entry, virtualenv and the
`vox` command. It keeps your settings (`~/.config/vox`), history (`~/.local/share/vox`), macOS logs
(`~/Library/Logs/Vox`) and your API key; delete those folders to remove them, and remove the key
with Keychain Access (macOS, item "vox") or `secret-tool clear service vox username openai_api_key`
(Linux). On macOS you can also remove the python3.12 entries from System Settings > Privacy
& Security.

The `.deb` is removed with `sudo apt remove vox`; user settings and history stay in each home
folder. `sudo apt purge vox` also deletes the autostart entry in `/etc/xdg/autostart`.
