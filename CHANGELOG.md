# Changelog

All notable changes to Vox Transfer are listed here. Vox Transfer follows
[semantic versioning](https://semver.org/).

## [1.1.0] - 2026-09-30

### Failed recording recovery

- Failed transcriptions now retain the complete original recording locally by default.
  History offers Local (whisper.cpp) and OpenAI Batch retries on macOS and Linux;
  choosing Batch uploads the recording and may incur another charge.
- Successful retries update the original History entry and paste into the focused
  external app, restoring its clipboard. Reopening History suppresses the paste,
  and the completed text remains available to copy.
- Retry progress, cancellation and separately copyable partial attempts appear in
  History. Recovery survives restarts without automatically retrying or uploading.
- General Settings includes "Keep failed recordings for retry". Turning it off
  stops new retention and retries; existing recordings remain until deleted.
- Deleting an entry or clearing History also deletes retained audio. Deletion
  cancels an active retry and prevents its result from recreating the entry.
- Recovery files are private to your account. Retries omit window and screen hints,
  and normal logs omit dictated text and provider credentials.

### Validation

- Added native desktop integration on macOS AppKit and Linux GTK, covering real
  focus changes, paste keystrokes, clipboard restoration and recovery controls.
- Linux CI now runs the desktop recovery integration under Openbox and Xvfb.

## [1.0.0] - 2026-09-29

The first public release. The command is `vox`, and its settings live in `~/.config/vox`.

### Dictation

- Tap right Shift to record and tap again to paste the transcript into the focused app;
  double-tap to cancel. The Hotkey page of Settings picks another key, and an optional key
  combination that toggles dictation too.
- Transcription with OpenAI in batch or live streaming mode, or on your computer with whisper.cpp,
  switchable from the Transcription submenu or in Settings.
- A recording limit, 15 minutes by default and adjustable in Settings
  (5 to 60 minutes). A recording that reaches it stops and is transcribed like any other.
- Long recordings are sent as 16 kHz mono audio and split at pauses when they would exceed
  OpenAI's upload limit, then joined in order. A part with nothing audible in it, such as the
  quiet end of a recording left running, is not uploaded.
- A transcript is always saved to history, even when pasting it fails. When a long recording fails
  partway, the parts already transcribed are saved there, and the menu says so; if history cannot
  store them, they are pasted instead.
- History records which transcription produced each dictation: batch, streaming or whisper.cpp.
- Custom dictionary and snippets, edited on the Vocabulary and Snippets pages of Settings.
- Local transcription keeps working when the graphics card has no memory left for the model, as
  when a game is running: it transcribes on the processor instead, more slowly.
  `[whisper_cpp] cpu_fallback = false` turns this off, and the error then says the graphics card
  is full.
- Local transcription keeps a transcript even when whisper.cpp returns a broken character
  (possible with Chinese, Japanese, Korean or emoji): the character shows as � instead of the
  whole transcript being lost.
- Your own sounds: WAV files in `~/.config/vox/sounds` replace the built-in ones. On Linux a long
  one is no longer cut off when Vox Transfer rescans microphones after a dictation.
- On Linux the built-in sounds resemble the macOS alert sounds instead of plain beeps.

### Privacy

- Screen hints (`[context] screen`, on by default) read only the focused window, drop words that
  look like passwords, tokens or API keys before anything is sent, and can be turned off entirely,
  in which case Vox Transfer never captures the screen. The filter also removes the user name and
  password in a URL, a password on a command line (`--password`, `--passphrase`, `-p…`,
  `-u user:…`), and the value after names such as `password:`, `DB_PASS=` or `MYSQL_PWD=` (to the
  end of the line when it is not in quotes).
- In a terminal, screen hints read a tmux pane only when the terminal app has a single session
  (one window and one tab, not split by the terminal), so the pane is the one on screen.
- On Linux, Local (whisper.cpp) mode gives whisper.cpp only your dictionary and `prompt` setting,
  never window or screen words, because other accounts on the computer can see its command line;
  it does not capture the screen.
- The OpenAI API key lives in the macOS Keychain or the Linux login keyring.
- Logs record events and lengths, not what you said; transcripts and window titles appear only with
  `vox -v`, and the API key never does. On macOS the log moved from `/tmp` to
  `~/Library/Logs/Vox/vox.log`, readable only by you, and is emptied when Vox Transfer starts if it
  has grown past 10 MiB.
- History can be searched, and entries deleted one at a time or all at once; deleted text is
  overwritten. The history database is readable only by you.
- Pasting puts back what was on the clipboard before: on macOS all of it, including images, files
  and rich text, and on Linux one form of it (copied files, else text, else an image or HTML). On
  macOS Vox Transfer's temporary copy is hidden from clipboard managers and Universal Clipboard; on
  Linux a clipboard manager may still record it.
- A password that a password manager marks as one when you copy it is never put back unmarked: on
  Linux (KeePassXC and others) the clipboard is empty after the paste, and on macOS the password
  is put back for this Mac only, never to your other devices.

### Desktop

- Menu bar (macOS) and tray (Linux) icon with status, pause, recent dictations, history search,
  a Transcription submenu and Settings…. Set API Key… appears at the top of the menu only while
  the key is missing or can't be read, and opens Settings on its Transcription page.
- One Settings window, opened with Settings… while Vox Transfer is idle, with the pages General
  (microphone, recording limit, sounds, lowering other audio, screen hints), Hotkey, Transcription
  (mode, API key, spoken language, prompt, whisper.cpp program and model), Vocabulary and
  Snippets. Each change is saved to `config.toml` as it is made and applies when the window
  closes, with no restart; the hotkey does not dictate while it is open.
- The microphone you pick is saved to `[audio] device` by name. System Default removes it.
- The Hotkey page records the key you tap and an optional key combination as you press them, and
  refuses keys you type with.
- Besides the modifier keys and F1 to F20, the hotkey can be fn (Globe) on macOS, or Pause or
  Scroll Lock on Linux.
- Hotkey settings edited in the settings file also apply within seconds, without a restart.
- The menu shows problems that need you: a missing API key, an error in the settings file, a broken
  whisper.cpp setup, missing Accessibility access, a silent microphone, a Wayland session, or a
  dictation that was only partly transcribed.
- An error in the settings file no longer stops Vox Transfer from starting: it starts, shows the
  problem, and does not record until the file is fixed, which it picks up without a restart. While
  the file has an error, Settings names the problem and changes nothing but the API key.
- Local (whisper.cpp) mode checks its setup on each hotkey press, before recording, and the menu
  follows edits to the settings file within seconds, including a switch to another mode.
- Settings refuses an API key with an invisible or typographic character (such as a
  zero-width space or a curly quote) picked up while copying, and asks you to copy it again.
- Without a keyring, a `~/.config/vox/.env` that Vox Transfer cannot read no longer stops it from
  starting: the menu shows "Can’t read the keyring", and the Transcription page of Settings says
  why.
- Stopping Vox Transfer as a service (`systemctl --user stop vox` on Linux, logging out, an update)
  quits like Quit Vox Transfer, so a volume lowered for a recording is restored.
- Microphones connected while Vox Transfer runs appear in Settings.

### Installing

- `install.sh` installs exactly the dependency versions in `requirements.lock`, verified by hash,
  and no longer needs a C compiler on Linux.
- Updates build the new version next to the old one and keep the old one if anything fails.
- `install.sh` run on its own downloads the latest release and verifies its checksum, so
  `curl -fsSL .../install.sh | bash` works.
- `install.sh --uninstall` removes Vox Transfer and keeps your settings and history.
- On macOS the launcher is `Vox Transfer.app`. The installer never overwrites or removes an app it
  did not create, such as the VOX player; when another app is called Vox Transfer, the launcher goes
  to `~/Applications`. An update replaces the `Vox.app` launcher an earlier version added.
- The installer ends by printing the full path of the `vox` command, and warns when its folder is
  not on your `PATH`.
- On Linux, `install.sh` and the `.deb` also need `xprop` (x11-utils), which tells terminals from
  other windows so pasting into a terminal works.
- Vox Transfer starts at login on Linux desktops without systemd session integration (Xfce, MATE and
  others) through an autostart entry, and warns about Wayland sessions.
- On macOS, Homebrew's `whisper-cli` and `tmux` are found when Vox Transfer runs at login, and the
  installer warns when a Python change means granting permissions again.
- A `.deb` package for Ubuntu 24.04 and derivatives (amd64 and arm64) that starts Vox Transfer at
  login for every user.
- `install.sh` refuses to run as root, retries `apt-get` after refreshing package lists, and tells
  users of other distributions which packages to install.

[1.1.0]: https://github.com/runsonmypc/vox/releases/tag/v1.1.0
[1.0.0]: https://github.com/runsonmypc/vox/releases/tag/v1.0.0
