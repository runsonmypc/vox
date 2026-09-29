# Setting up Vox Transfer: a guide for AI assistants

This page is written for an AI coding assistant (Claude Code, Codex, Cursor and others) that is
helping someone install, configure or fix Vox Transfer on their computer. People are welcome to read
it too.

Vox Transfer is a dictation app for macOS and Linux. The user taps a hotkey (right Shift by
default), speaks, and taps it again; Vox Transfer transcribes the speech and pastes the text into
the focused app. It runs in the background, with a microphone icon in the menu bar (macOS) or the
system tray (Linux). The command, folders and login service use the short name `vox`; only the app
is called Vox Transfer.

## Ground rules

- **Never handle the OpenAI API key.** Do not ask the user to paste it into the chat, and never
  write it into a file. Tell them to choose **Settings…** from the Vox Transfer menu and set it on
  the Transcription page, which stores it in the macOS Keychain or the Linux login keyring.
- **Never ask for the user's password.** Run the installer as the user, never with `sudo`, and
  never pass a password to `sudo -S`. On Linux the installer runs `sudo apt-get` when system
  packages are missing, and your shell usually cannot answer that prompt: have the user run the
  install command in their own terminal, then continue from step 3.
- **The user must click permission prompts themselves.** On macOS you cannot grant Microphone,
  Accessibility, Input Monitoring or Screen Recording; tell the user what to allow and why.
- **Edits to `config.toml` apply by themselves** within a few seconds, with no restart. Check the
  log after each save (step 5). Custom sound files are the exception: they load when Vox Transfer
  starts.
- **Don't leave verbose logging on.** `vox -v` logs what the user dictates and their window titles.
- **Ask before changing a default the user did not mention**, such as screen hints or the
  transcription mode.

## 1. Check the computer

macOS (Apple Silicon or Intel):

```sh
brew --version   # Homebrew, to install uv
uv --version     # needed by the installer; if missing: brew install uv
```

Linux:

```sh
echo "$XDG_SESSION_TYPE"    # must be x11; wayland works only partly
/usr/bin/python3 --version  # must be 3.12 or 3.13
grep -E '^(ID|VERSION_ID)=' /etc/os-release
```

On Wayland, the hotkey and paste reach only X11 apps. Tell the user to choose an X11 session at the
login screen (such as "Ubuntu on Xorg") for full support. Without apt (distributions not based on
Debian), the installer stops if a system package it needs is missing, and lists what to install;
the user installs those, then runs it again.

## 2. Install

```sh
curl -fsSL https://github.com/runsonmypc/vox/releases/latest/download/install.sh | bash
```

This installs for the current user: the app in `~/.local/share/vox/venv`, the command at
`~/.local/bin/vox`, a login service, and a launcher in the user's applications. It verifies the
download against the release's checksums. Running it again updates Vox Transfer and keeps settings
and history. [install.md](install.md) covers the `.deb` package (for all users on Ubuntu 24.04) and
installing from a tarball.

If `curl` fails, `bash` gets no input and the pipeline still succeeds, so always check with step 3.

## 3. Check that it runs

```sh
~/.local/bin/vox --version    # prints "Vox Transfer" and the version
```

- macOS: `launchctl print gui/$(id -u)/com.runsonmypc.vox | grep -E 'state|pid'`; log at
  `~/Library/Logs/Vox/vox.log`.
- Linux: `systemctl --user status vox`; log with `journalctl --user -u vox -e`.

Then ask the user to look for the microphone icon in the menu bar or tray. The hotkey works even
without the icon. On GNOME the tray icon needs the AppIndicator extension, which the installer
enables or offers to install; if the icon is missing, see
[No tray icon on GNOME](troubleshooting.md#common-problems).

## 4. First start

- **OpenAI (the default)**: the user creates a key at https://platform.openai.com/api-keys, chooses
  **Set API Key…** at the top of the menu (or the Transcription page of **Settings…**) and pastes it. Vox Transfer checks it with OpenAI, which costs
  nothing.
- **No key, or nothing should leave the computer**: set up local transcription (below).
- **macOS permissions**: the prompts name **python3.12**. Microphone, Accessibility and Input
  Monitoring are needed; Screen Recording only for screen hints. After allowing Accessibility and
  Input Monitoring, the user quits Vox Transfer from its menu and opens it again from Applications.

Then have the user try it: tap right Shift, say a sentence, tap right Shift again.

## 5. Customize

Ask the user what they would like. Most things are in **Settings…** in the menu: the microphone,
recording limit, sounds, screen hints, hotkey, transcription mode, language, prompt, whisper.cpp
files, vocabulary and snippets. For the rest, edit
`~/.config/vox/config.toml` (create it if missing). [`config.example.toml`](../config.example.toml)
lists every setting with its default, and [settings.md](settings.md) explains each one.

How to edit the file safely:

- **Read it first.** The Settings window writes to it too, so it may already have sections such as
  `[hotkey]`, `[transcription]` or `[snippets]`. Add keys to an existing section; a section header
  that appears twice is an error.
- **`[section] key` is shorthand** for a `key = value` line under the `[section]` header, as in the
  examples below. Top-level keys such as `dictionary` go above the first header.
- **Check the log after each save**: `Config reloaded from` means it worked; `Config reload failed`
  means the edit was ignored and the previous settings stay, so fix the file. macOS:
  `tail -n 20 ~/Library/Logs/Vox/vox.log`. Linux: `journalctl --user -u vox -n 20 --no-pager`.

Common requests:

- **Another hotkey.** Easiest: the user opens the Hotkey page of **Settings…** and taps the key; the
  page refuses keys that type text. In the file, `[hotkey] key` can be `"right_ctrl"`, `"right_alt"`
  (on Linux only when right Alt is not AltGr), `"cmd_r"` (macOS), `"f13"`, `"fn"` (macOS), or
  `"pause"` or `"scroll_lock"` (Linux). Never set a key that types text, such as a letter: the file
  accepts it, and then typing that letter on its own starts dictation. For `"fn"`, the user sets
  **Press 🌐 key to** (or **Press fn key to**) to **Do Nothing** in System Settings > Keyboard, and
  changes a Dictation shortcut of pressing 🌐 or fn twice. An extra key combination goes in
  `[hotkey] fallback`, such as `"ctrl+space"`.
- **Words spelled right** (names, jargon, product names), at the top of the file:

  ```toml
  dictionary = ["Kubernetes", "PostgreSQL", "Priya"]
  ```

- **Snippets**: say the trigger phrase on its own, and Vox Transfer types the expansion instead.

  ```toml
  [snippets]
  "my email" = "sam@example.com"
  "sign off" = "Best,\nSam"
  ```

- **Language** (an ISO code; without it, the language is detected):

  ```toml
  [transcription]
  language = "de"
  ```

- **A specific microphone**: the user picks it on the General page of Settings. In the file, run
  `~/.local/bin/vox --list-devices`, then set `[audio] device` to the device's name.
- **Longer recordings**: `[audio] max_recording_seconds` (default 900), or the recording limit on the General page of Settings.
- **No volume lowering while recording**: `[attenuation] enabled = false`.
- **No sounds**: `[sounds] enabled = false`. Custom sounds are WAV files in `~/.config/vox/sounds`
  (see [Sounds](settings.md#sounds)); they load when Vox Transfer starts, so the user quits it from
  its menu and opens it again.
- **No screen hints** (don't read the focused window): `[context] screen = false`.
- **Terminal paste on Linux**: if a terminal gets Ctrl+V instead of Ctrl+Shift+V, check that `xprop`
  is installed, or map part of its window class under `[window_classes]`, such as
  `"myterm" = "TERMINAL"`.

### Local transcription with whisper.cpp

1. Install whisper.cpp.
   - macOS: `brew install whisper-cpp`. The binary is `$(brew --prefix)/bin/whisper-cli`.
   - Linux: the user runs `sudo apt-get install -y git cmake build-essential` in their own
     terminal. Then build it:

     ```sh
     git clone https://github.com/ggml-org/whisper.cpp ~/whisper.cpp
     cmake -S ~/whisper.cpp -B ~/whisper.cpp/build
     cmake --build ~/whisper.cpp/build -j --config Release
     ```

     The binary is `~/whisper.cpp/build/bin/whisper-cli`.
2. Download a model into a folder such as `~/whisper-models`. Tell the user the size first:
   `ggml-large-v3-turbo.bin` (about 1.6 GB, accurate) or `ggml-base.en.bin` (about 150 MB, fast,
   English only), from `https://huggingface.co/ggerganov/whisper.cpp/resolve/main/<file>`:

   ```sh
   mkdir -p ~/whisper-models
   curl -L -o ~/whisper-models/ggml-large-v3-turbo.bin \
     https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
   ```

3. Configure it with full paths (use the binary path from step 1):

   ```toml
   [transcription]
   mode = "whisper_cpp"

   [whisper_cpp]
   binary = "/opt/homebrew/bin/whisper-cli"
   model = "~/whisper-models/ggml-large-v3-turbo.bin"
   ```

   The user can also choose both files and the mode on the Transcription page of Settings, which
   says why a mode can't be chosen. If the menu's first line names a whisper.cpp problem, fix the
   path it mentions.

## 6. When something is wrong

- The first line of the Vox Transfer menu names the most urgent problem. Match it in
  [troubleshooting.md](troubleshooting.md#menu-messages).
- Read the log (step 3). If `config.toml` has an error when Vox Transfer starts, it does not record
  until the file is fixed, and the log names the setting.
- For more detail, the user quits Vox Transfer and runs `~/.local/bin/vox -v` in a terminal.
  Remind them that this logs what they say.

## Uninstall

```sh
curl -fsSL https://github.com/runsonmypc/vox/releases/latest/download/install.sh | bash -s -- --uninstall
```

This keeps settings, history and the API key; [install.md](install.md#uninstall) says how to remove
those too.
