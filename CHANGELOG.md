# Changelog

All notable changes to Vox are listed here. Vox follows [semantic versioning](https://semver.org/).

## [1.0.0] - 2026-09-26

The first public release.

### Dictation

- Tap right Shift to record and tap again to paste the transcript into the focused app;
  double-tap to cancel. An optional key combination can toggle dictation too.
- Transcription with OpenAI in batch or live streaming mode, or on your computer with whisper.cpp,
  switchable from the menu.
- A recording limit, 15 minutes by default and adjustable from the new Recording Limit menu
  (5 to 60 minutes). A recording that reaches it stops and is transcribed like any other.
- Long recordings are sent as 16 kHz mono audio and split at pauses when they would exceed
  OpenAI's upload limit, then joined in order. A part with nothing audible in it, such as the
  quiet end of a recording left running, is not uploaded.
- A transcript is always saved to history, even when pasting it fails. When a long recording fails
  partway, the parts already transcribed are saved there, and the menu says so; if history cannot
  store them, they are pasted instead.
- History records which transcription produced each dictation: batch, streaming or whisper.cpp.
- Custom dictionary and snippets, edited in the Vocabulary & Snippets window.
- Local transcription keeps a transcript even when whisper.cpp returns a broken character
  (possible with Chinese, Japanese, Korean or emoji): the character shows as � instead of the
  whole transcript being lost.
- Your own sounds: WAV files in `~/.config/vox/sounds` replace the built-in ones. On Linux a long
  one is no longer cut off when Vox rescans microphones after a dictation.

### Privacy

- Screen hints (`[context] screen`, on by default) read only the focused window, drop words that
  look like passwords, tokens or API keys before anything is sent, and can be turned off entirely,
  in which case Vox never captures the screen. The filter also removes the user name and password
  in a URL, a password on a command line (`--password`, `--passphrase`, `-p…`, `-u user:…`), and
  the value after names such as `password:`, `DB_PASS=` or `MYSQL_PWD=` (to the end of the line
  when it is not in quotes).
- In a terminal, screen hints read a tmux pane only when the terminal app has a single session
  (one window and one tab, not split by the terminal), so the pane is the one on screen.
- On Linux, Local (whisper.cpp) mode gives whisper.cpp only your dictionary and `prompt` setting,
  never window or screen words, because other accounts on the computer can see its command line;
  it does not capture the screen.
- The OpenAI API key lives in the macOS Keychain or the Linux login keyring.
- Logs record events and lengths, not what you said; transcripts and window titles appear only
  with `vox -v`, and the API key never does. On macOS the log moved from `/tmp` to
  `~/Library/Logs/Vox/vox.log`, readable only by you, and is emptied when Vox starts if it has
  grown past 10 MiB.
- History can be searched, and entries deleted one at a time or all at once; deleted text is
  overwritten. The history database is readable only by you.
- Pasting puts back what was on the clipboard before: on macOS all of it, including images, files
  and rich text, and on Linux one form of it (copied files, else text, else an image or HTML). On
  macOS Vox's temporary copy is hidden from clipboard managers and Universal Clipboard; on Linux a
  clipboard manager may still record it.
- A password that a password manager marks as one when you copy it is never put back unmarked: on
  Linux (KeePassXC and others) the clipboard is empty after the paste, and on macOS the password
  is put back for this Mac only, never to your other devices.

### Desktop

- Menu bar (macOS) and tray (Linux) icon with status, pause, input device, transcription mode,
  recording limit, recent dictations, history search, vocabulary and API key.
- The menu shows problems that need you: a missing API key, an error in the settings file, a broken
  whisper.cpp setup, missing Accessibility access, a silent microphone, a Wayland session, or a
  dictation that was only partly transcribed.
- An error in the settings file no longer stops Vox from starting: it starts, shows the problem,
  and does not record until the file is fixed, which it picks up without a restart. While the file
  has an error, the Vocabulary & Snippets window is read-only.
- Local (whisper.cpp) mode checks its setup on each hotkey press, before recording, and the menu
  follows edits to the settings file within seconds, including a switch to another mode.
- The Set API Key window refuses a key with an invisible or typographic character (such as a
  zero-width space or a curly quote) picked up while copying, and asks you to copy it again.
- Without a keyring, a `~/.config/vox/.env` that Vox cannot read no longer stops it from
  starting: the menu shows "Can’t read the keyring", and the Set API Key window says why.
- Stopping Vox as a service (`systemctl --user stop vox` on Linux, logging out, an update) quits
  like Quit Vox, so a volume lowered for a recording is restored.
- Microphones connected while Vox runs appear in the Input Device menu.

### Installing

- `install.sh` installs exactly the dependency versions in `requirements.lock`, verified by hash,
  and no longer needs a C compiler on Linux.
- Updates build the new version next to the old one and keep the old one if anything fails.
- `install.sh` run on its own downloads the latest release and verifies its checksum, so
  `curl -fsSL .../install.sh | bash` works.
- `install.sh --uninstall` removes Vox and keeps your settings and history.
- On macOS the installer never overwrites or removes another app named Vox (or VOX): the launcher
  then goes to `~/Applications`.
- The installer ends by printing the full path of the `vox` command, and warns when its folder is
  not on your `PATH`.
- On Linux, `install.sh` and the `.deb` also need `xprop` (x11-utils), which tells terminals from
  other windows so pasting into a terminal works.
- Vox starts at login on Linux desktops without systemd session integration (Xfce, MATE and
  others) through an autostart entry, and warns about Wayland sessions.
- On macOS, Homebrew's `whisper-cli` and `tmux` are found when Vox runs at login, and the
  installer warns when a Python change means granting permissions again.
- A `.deb` package for Ubuntu 24.04 and derivatives (amd64 and arm64) that starts Vox at login for
  every user.
- `install.sh` refuses to run as root, retries `apt-get` after refreshing package lists, and tells
  users of other distributions which packages to install.

[1.0.0]: https://github.com/runsonmypc/vox/releases/tag/v1.0.0
