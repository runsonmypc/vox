# Vox Transfer

Voice dictation for macOS and Linux. Tap a key, speak, tap it again, and Vox Transfer types what you
said into whatever app has focus.

- Transcribes with OpenAI (batch or live streaming) or entirely on your computer with
  [whisper.cpp](https://github.com/ggml-org/whisper.cpp).
- Lives in the menu bar or system tray: pause dictation, pick a microphone, switch transcription,
  set the recording limit, copy recent dictations, and search your history.
- Spells your own words right: a custom dictionary, plus optional hints from the window you are
  dictating into.
- Snippets: say a trigger phrase and Vox Transfer pastes its expansion instead.
- Lowers the system volume while you record, and plays short sounds so you know what it is doing.

Vox Transfer is free software under the GNU GPL, version 3.

## Contents

- [Requirements](#requirements)
- [Install](#install)
- [Permissions](#permissions)
- [Privacy](#privacy)
- [Using Vox Transfer](#using-vox-transfer)
- [Configuration](#configuration)
- [Local transcription with whisper.cpp](#local-transcription-with-whispercpp)
- [Wayland](#wayland)
- [Troubleshooting](#troubleshooting)
- [Uninstall](#uninstall)
- [Development](#development)
- [License](#license)

## Requirements

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

The command is still `vox`, and the folders, login service and `.deb` package keep their names;
only the app's name changed to Vox Transfer.

On macOS the launcher is `/Applications/Vox Transfer.app`, or `~/Applications/Vox Transfer.app`
when you cannot write to `/Applications` or another app there is already called Vox Transfer. An
update replaces the `Vox.app` launcher that an earlier version added. The installer never changes
or removes an app it did not create, such as the VOX music player.

### One line

```sh
curl -fsSL https://github.com/runsonmypc/vox/releases/latest/download/install.sh | bash
```

This downloads the latest release, checks it against the release's `SHA256SUMS`, and installs it.
Run the same command again to update. Options go after `bash -s --`, for example
`... | bash -s -- --no-service` to install without the login service.

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

Updating keeps your settings and history. The new version is built next to the old one, and if
anything fails the previous Vox Transfer stays installed and running.

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
`maim` reads windows that do not expose their text that way. Remove it with `sudo apt remove vox`.

To stop Vox Transfer starting at login for everyone, run
`sudo systemctl --global disable vox.service` and add `Hidden=true` to
`/etc/xdg/autostart/vox.desktop`; updates keep both. One user can opt out with
`systemctl --user mask vox.service`.

The package and the per-user installer can coexist; the per-user install takes precedence for the
user who ran it.

### First start

In an OpenAI mode, Vox Transfer asks for your OpenAI API key when it starts without one, and again
if you dictate before saving one. You can also choose **Set API Key…** from its menu at any time.
The key goes into the macOS Keychain or your Linux login keyring (GNOME Keyring, KWallet), never
into a file Vox Transfer writes in plain text, unless your Linux system has no keyring at all, in
which case it goes into `~/.config/vox/.env`, readable only by you. An `OPENAI_API_KEY` environment
variable overrides the stored key. The key window refuses a key with an invisible or typographic
character, such as a zero-width space or a curly quote, which copying from a web page or a chat can
pick up; copy the key again.

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

## Privacy

Vox Transfer talks to one service, OpenAI, and only when you dictate in an OpenAI mode or save an
API key (it checks a new key by listing OpenAI's models, which costs nothing). It has no analytics,
telemetry or update checks.

### What leaves your computer

| Mode | Sent to OpenAI |
| --- | --- |
| OpenAI (batch), the default | The recording (as 16 kHz mono audio), the model name, your `language` and `prompt` settings, and a list of spelling hints. |
| OpenAI (streaming) | The recording as you speak (as 24 kHz mono audio), the model name, your `language` and `prompt` settings, and spelling hints from your dictionary and the window title. If the live session fails, Vox Transfer sends the recording as in batch mode. |
| Local (whisper.cpp) | Nothing. Audio, hints and text stay on your computer. |

The spelling hints are your dictionary words plus, when screen hints are on, up to 10 words from the
focused window's title and up to 25 words from the text visible in it: at most 40 words in total.
Streaming mode sends its hints as it connects, before the window's text could be read, so it uses
only the dictionary and the title. On Linux, Local (whisper.cpp) mode uses only the dictionary:
whisper.cpp gets its hints and your `prompt` setting on its command line, which other accounts on
the computer can see, so Vox Transfer gives it no window or screen words there and does not capture
the screen for it. Those accounts can still see your dictionary and `prompt`, so keep secrets out of
them. Words that look like passwords, tokens or API keys are removed before anything is sent, and
before they reach whisper.cpp:

- keys and tokens such as `sk-…`, `ghp_…`, `AKIA…`, JSON web tokens, and long random strings;
- the value after a name such as `API_KEY=`, `token:`, `DB_PASS=`, `MYSQL_PWD=` or `password:`,
  and for a password the rest of its line when the value is not in quotes;
- the user name and password in a URL, as in `postgres://user:password@host` (the host stays);
- passwords on a command line, as in `--password VALUE`, `--pass=VALUE`, `--passphrase VALUE`,
  `-pVALUE` and `-u user:VALUE`.

The filter works from patterns and cannot catch every password, so turn screen hints off while
secrets are on screen. Your API key goes to OpenAI with each request, as any OpenAI client's does.
OpenAI's API terms and data usage policies apply to what it receives.

### Screen hints (`[context] screen`)

Screen hints help the transcriber spell names and terms that are on screen. With them on (the
default), Vox Transfer reads text from the focused window only, never the whole screen or other
windows:

- **macOS**: the text of the tmux pane when the focused app is a terminal running tmux in its only
  session (one window and one tab, not split by the terminal itself), so the pane is the one on
  screen; otherwise a screenshot of the focused window, read with Apple's on-device text recognition.
- **Linux** (not in Local mode, see above): the window's text through the accessibility interface
  (AT-SPI). If that finds little, a screenshot of the window read with `tesseract` when `maim` and
  `tesseract` are installed, or, in a terminal running tmux in its only session, the pane text.

Screenshots are never kept: on macOS the temporary file is deleted as soon as it is read, and on
Linux the image goes straight from `maim` to `tesseract`. Text recognition runs on your computer,
and only the filtered words listed above are sent.

With `screen = false` under `[context]`, Vox Transfer sends no window-title words and no screen text
to any service and never captures the screen, so macOS never asks for Screen Recording. It still
looks up which app has focus, locally, to pick the right paste shortcut.

### What stays on your computer

- **History**: every dictation's text, with its time, length, the kind of app it went to (not the
  window title) and the transcription that produced it (batch, streaming or whisper.cpp; a streaming
  recording that fell back to batch counts as batch), in `~/.local/share/vox/history.db`, readable
  only by you. Recordings are never saved. Open **Search History…** to find past dictations,
  **Delete** one, or **Clear History** to erase them all; deleted text is overwritten, not just
  unlinked.
- **Recent dictations**: the last three appear in the Vox Transfer menu, so anyone who sees your
  screen, for example in a screen share, can read them.
- **Logs**: `~/Library/Logs/Vox/vox.log` on macOS (readable only by you, and emptied when
  Vox Transfer starts if it has grown past 10 MiB), the user journal on Linux
  (`journalctl --user -u vox`). They record events, lengths and errors, not what you said. Only
  `vox -v` (verbose) logs transcripts, snippet expansions and the titles of the windows you dictate
  into. Your API key is never logged.
- **Settings**: `~/.config/vox/config.toml`, which holds your dictionary and snippets.
- **Clipboard**: Vox Transfer pastes through the clipboard and then puts back what was there before.
  On macOS that includes images, files and rich text, and Vox Transfer marks its temporary copy so
  clipboard managers and Universal Clipboard ignore it. On Linux one form comes back: copied files,
  otherwise plain text, otherwise an image or HTML, so rich text copied from a browser returns as
  plain text. Vox Transfer cannot mark its temporary copy on Linux, so a clipboard manager there may
  keep each dictation in its history. A password that a password manager marks as one when you copy
  it is treated differently. On Linux (KeePassXC marks it this way) Vox Transfer does not put it
  back, and the clipboard is empty after the paste: the password could only come back without its
  mark, and a clipboard history would then keep it. On macOS it comes back for this Mac only, so
  Universal Clipboard does not offer it to your other devices.

## Using Vox Transfer

| Action | What happens |
| --- | --- |
| Tap **right Shift** on its own | Start recording (Vox Transfer lowers the volume and plays a sound). |
| Tap it again | Stop, transcribe, and paste the text into the focused app. |
| Double-tap it | Cancel: discard the recording, or stop a transcription in progress. Nothing is pasted. |

Pressing Shift together with another key, as when typing a capital letter, does nothing. A
recording that reaches the recording limit (15 minutes unless you change it) stops and is
transcribed as if you had tapped the key. In batch mode a recording longer than about 13 minutes
is uploaded in parts, split at pauses; a part with nothing audible in it is not uploaded, and if a
part fails, the text of the parts before it is kept in history (or pasted, if history cannot
store it).

The Vox Transfer menu, from the menu bar icon on macOS or the tray icon on Linux:

- **Status**: idle, recording, processing, paused, or while idle a problem to fix (see
  [Menu messages](#menu-messages)).
- **Pause Dictation**: ignore the hotkey until you resume.
- **Input Device**: the microphone to record from, or the system default. A microphone connected
  while Vox Transfer runs appears after your next dictation, and on macOS also within 30 seconds
  while idle.
- **Transcription**: OpenAI (batch), OpenAI (streaming) or Local (whisper.cpp). A mode that is not
  set up (no API key, or no whisper.cpp model) is greyed out.
- **Recording Limit**: 5, 10, 15, 30 or 60 minutes.
- **Recent dictations**: click one to copy it.
- **Search History…**, **Vocabulary & Snippets…**, **Set API Key…**
- **Quit Vox Transfer**. To start it again, open Vox Transfer from Applications or Spotlight (macOS)
  or your applications list (Linux). Vox Transfer quits the same way when its login service stops
  (for example `systemctl --user stop vox` on Linux), when you log out, or during an update, so a
  volume lowered for a recording is restored.

The transcription mode and recording limit you choose are saved in `~/.config/vox/config.toml`. The
input device you choose lasts until Vox Transfer quits; set `[audio] device` to keep one.

## Configuration

Vox Transfer works without a config file. To change a setting, create `~/.config/vox/config.toml` (a
commented copy of every setting is in [`config.example.toml`](config.example.toml)). Vox Transfer
applies changes within a few seconds of a save, except `[hotkey]`, which applies after you quit and
reopen Vox Transfer. Screen hints and the recording limit apply from the next dictation, and
`[audio]` changes wait for a recording in progress to end.

| Setting | Default | Meaning |
| --- | --- | --- |
| `dictionary` (top level) | `[]` | Words to spell exactly as written. |
| `[hotkey] key` | `"right_shift"` | The key to tap on its own, such as `"right_ctrl"`, `"right_alt"`, `"cmd_r"` or `"f13"`. |
| `[hotkey] fallback` | `""` (none) | An extra key combination that toggles dictation, such as `"ctrl+space"`. |
| `[hotkey] double_tap_timeout_ms` | `400` | How fast a double-tap must be to cancel. |
| `[audio] device` | unset (system default) | Input device index or name, from `vox --list-devices`. |
| `[audio] sample_rate` | `48000` | Recording sample rate in Hz. |
| `[audio] channels` | `1` | Recording channels. |
| `[audio] max_recording_seconds` | `900` | The recording limit; a recording that reaches it stops and is transcribed. |
| `[transcription] mode` | `"batch"` | `"batch"`, `"streaming"` or `"whisper_cpp"`. |
| `[transcription] streaming_model` | `"gpt-live-transcribe"` | OpenAI model for streaming mode. |
| `[transcription] language` | unset (detect) | Spoken language as an ISO code, such as `"en"`. |
| `[transcription] prompt` | `""` | Text that primes the transcriber, in every mode. |
| `[whisper] model` | `"gpt-transcribe"` | OpenAI model for batch mode. |
| `[whisper_cpp] binary` | `"whisper-cli"` | The whisper.cpp command; a full path is safest. |
| `[whisper_cpp] model` | `""` | Path to a GGML model file; required for whisper.cpp mode. |
| `[context] screen` | `true` | Screen hints, see [Privacy](#screen-hints-context-screen). |
| `[sounds] enabled` | `true` | Play sounds for start, stop, cancel and errors. |
| `[attenuation] enabled` | `true` | Lower the system volume while recording. |
| `[attenuation] level` | `0.5` | Recording volume as a fraction of the current volume. |
| `[snippets]` | none | `"trigger phrase" = "expansion"` pairs. |
| `[window_classes]` | none | `"part of window class" = "TERMINAL"` (or `EDITOR`, `CHAT`, `EMAIL`, `BROWSER`, `OTHER`). On Linux, terminals get Ctrl+Shift+V instead of Ctrl+V. |

Paths may be absolute, start with `~`, or be relative to the config file. `vox --config PATH` uses
another file.

If the file has an error when Vox Transfer starts (a typo, or a value of the wrong kind such as
`level = "0.5"`), Vox Transfer starts anyway but does not record: the menu shows **Settings file has
an error**, the log names the file and the setting, and the hotkey plays the error sound.
Vox Transfer does not fall back to defaults for dictation, since they might send audio to OpenAI
when you chose local transcription. Save a fixed file and Vox Transfer picks it up within a few
seconds. While the file has an error, the Vocabulary & Snippets window shows the problem and changes
nothing; fix the file, then reopen the window. An error in an edit while Vox Transfer is running is
logged and ignored, and the previous settings stay in effect.

### Sounds

Vox Transfer plays a sound when dictation starts, stops, is cancelled or fails, when you press the
hotkey while it is busy or paused, and when you pause or resume. To use your own, put WAV files in
`~/.config/vox/sounds`, named after the sound they replace: `start.wav`, `stop.wav`, `cancel.wav`,
`error.wav`, `busy.wav`, `pause.wav` and `resume.wav`. Vox Transfer loads them when it starts; any
it does not find keep the built-in sound. `[sounds] enabled = false` turns all of them off.

## Local transcription with whisper.cpp

1. Build whisper.cpp and download a model, in a checkout of
   [whisper.cpp](https://github.com/ggml-org/whisper.cpp):

   ```sh
   cmake -B build && cmake --build build -j --config Release
   sh ./models/download-ggml-model.sh large-v3-turbo
   ```

   On macOS, `brew install whisper-cpp` provides `whisper-cli` too; you still need a model file.

2. Point Vox Transfer at them in `~/.config/vox/config.toml`:

   ```toml
   [whisper_cpp]
   binary = "~/whisper.cpp/build/bin/whisper-cli"
   model = "~/whisper.cpp/models/ggml-large-v3-turbo.bin"
   ```

   Give the full path to `whisper-cli`: the login service may not search the same `PATH` as your
   shell. On macOS it searches `/opt/homebrew/bin` and `/usr/local/bin` as well as the system
   folders.

3. Choose **Local (whisper.cpp)** from the Transcription menu.

If the whisper.cpp setup later breaks (a moved model, say), Vox Transfer still starts, shows the
problem in its menu, and lets you switch back to an OpenAI mode. Each hotkey press checks the setup
again before recording: while it is broken, Vox Transfer plays the error sound and records nothing,
and once you fix it, dictation works without restarting Vox Transfer. An edit to `[whisper_cpp]` or
the mode in `config.toml` updates the menu's first line within a few seconds.

## Wayland

Vox Transfer's hotkey, active-window detection and paste use X11. In a Wayland session (the default
on recent GNOME and KDE), they work only while an X11 app (running through XWayland) has focus;
native Wayland apps do not see the hotkey or the paste. Vox Transfer warns about this at install and
in its menu. For full support, choose an X11 session at the login screen, such as **Ubuntu on Xorg**
or **GNOME on Xorg**. macOS is not affected.

## Troubleshooting

### Menu messages

While Vox Transfer is idle, the first line of its menu names the most urgent problem that stops or
affects dictation, in this order:

| Message | What to do |
| --- | --- |
| Settings file has an error | Fix `~/.config/vox/config.toml`; the log names the setting. Vox Transfer does not record until then. |
| API key needed | Choose **Set API Key…**, or switch to Local (whisper.cpp). |
| Can’t read the keyring | Unlock your login keychain or keyring; Vox Transfer tries again on the next hotkey press. Without a keyring, **Set API Key…** says why `~/.config/vox/.env` can’t be read; it must be owned by you, readable (`chmod 600`) and plain UTF-8 text. |
| A whisper.cpp problem, such as a missing model | Fix `[whisper_cpp]`, or choose an OpenAI mode. |
| Accessibility access needed | macOS: allow python3.12 under Accessibility, then quit and reopen Vox Transfer. |
| Microphone is silent: check its permission | Allow the microphone (macOS: Microphone for python3.12) and check the Input Device menu. |
| Wayland: hotkey and paste only work in X11 apps | See [Wayland](#wayland). |
| Last dictation only partly transcribed: see History | A long recording failed partway. The parts that were transcribed are in **Search History…**. |

### Common problems

The commands below use `~/.local/bin/vox`, where the installer puts Vox Transfer, since
`~/.local/bin` is often not on your `PATH`. With the `.deb`, use `/usr/bin/vox`.

- **The hotkey does nothing (macOS).** Check that python3.12 is allowed under Accessibility and
  Input Monitoring in System Settings > Privacy & Security, then quit and reopen Vox Transfer. After
  an update that changed Python, remove the old entries and allow the new ones.
- **Recordings come back empty.** Allow the microphone (macOS: Microphone permission for
  python3.12), check the Input Device menu, and try `~/.local/bin/vox --list-devices`.
- **A paste did not arrive.** Vox Transfer plays the error sound and still saves the text in
  history: open **Search History…** to copy it.
- **Nothing pastes into a terminal (Linux).** Install `xprop` (x11-utils on Debian and Ubuntu).
  Without it Vox Transfer cannot tell a terminal from other windows and presses Ctrl+V instead of
  Ctrl+Shift+V; the log warns about it when Vox Transfer starts.
- **No tray icon on GNOME.** Enable "AppIndicator and KStatusNotifierItem Support" in the Extensions
  app, or run the installer again. Vox Transfer keeps working without the icon.
- **Vox Transfer does not start at login (Linux).** The installer adds both a systemd user service
  and an autostart entry. On a bare window manager that runs neither, start `~/.local/bin/vox` from
  your session startup file. If Vox Transfer starts but cannot reach the display, run
  `systemctl --user import-environment DISPLAY XAUTHORITY` in your session startup first.
- **Local (whisper.cpp) is greyed out.** Set `[whisper_cpp] model`, and `binary` as a full path.
  Once whisper.cpp is the selected mode, the menu's first line shows what is missing.
- **"Vox Transfer needs Python 3.12 or 3.13".** Some of Vox Transfer's dependencies do not yet
  publish packages for newer Pythons, and building them would need a compiler.
- **Something else.** Read the log (`~/Library/Logs/Vox/vox.log` on macOS,
  `journalctl --user -u vox -e` on Linux). For more detail, quit Vox Transfer and run
  `~/.local/bin/vox -v` in a terminal; note that verbose logs include what you dictate and the
  titles of your windows.

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

## Development

```sh
uv sync                      # Python 3.12 virtualenv with the dev tools
uv run vox -v                # run from the checkout (quit the installed Vox Transfer first: one instance at a time)
uv run pytest                # tests
uv run ruff check vox tests  # lint
```

On Linux, the GTK window tests need GTK 4, libadwaita and a display, and must run in their own
process: `dbus-run-session -- xvfb-run -a uv run pytest -m gtk`, then `uv run pytest -m "not gtk"`.
The virtualenv also needs the system's PyGObject, for example
`ln -s /usr/lib/python3/dist-packages/gi .venv/lib/python3.12/site-packages/`.

Dependencies are locked in `uv.lock`. After changing them, run `uv lock` and `scripts/lock.sh`,
which regenerates `requirements.lock`, the hash-pinned list the installer and the `.deb` install
from. `scripts/lock.sh --check`, which CI runs, fails when the two disagree or when a locked package
has no wheel for Linux (x86_64, arm64) or macOS with Python 3.12 or 3.13, since installing must never
need a compiler. Lock with the uv version CI pins (`UV_VERSION` in `.github/workflows/ci.yml`);
another version may write a different lock. [RELEASING.md](RELEASING.md#relock) shows how to run
that version without installing it.

CI also runs the installer and the package in clean Ubuntu 24.04 containers, which you can do with
Docker from the checkout:

```sh
docker run --rm -v "$PWD:/src:ro" ubuntu:24.04 /src/scripts/test-install-linux.sh
# on Ubuntu 24.04 with uv and sudo, build the package, then install, upgrade and remove it:
packaging/deb/build-deb.sh 1.0.0 amd64
docker run --rm -v "$PWD:/src:ro" -w /src ubuntu:24.04 packaging/deb/smoke-test.sh dist/vox_1.0.0_amd64.deb
```

[RELEASING.md](RELEASING.md) describes how to make a release and how to try the release workflow
without publishing anything.

## License

Copyright (C) 2026 Alex

Vox Transfer is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License, version 3, as published by the Free Software Foundation. Vox Transfer is
distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See [LICENSE](LICENSE) for the full
text.
