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
  OpenAI's upload limit, then joined in order.
- A transcript is always saved to history, even when pasting it fails.
- Custom dictionary and snippets, edited in the Vocabulary & Snippets window.

### Privacy

- Screen hints (`[context] screen`, on by default) read only the focused window, drop words that
  look like passwords, tokens or API keys before anything is sent, and can be turned off entirely,
  in which case Vox never captures the screen.
- The OpenAI API key lives in the macOS Keychain or the Linux login keyring.
- Logs record events and lengths, not what you said; transcripts appear only with `vox -v`. On
  macOS the log moved from `/tmp` to `~/Library/Logs/Vox/vox.log`, readable only by you.
- History can be searched, and entries deleted one at a time or all at once; deleted text is
  overwritten. The history database is readable only by you.
- Pasting restores everything that was on the clipboard before, including images, files and rich
  text; on macOS Vox's temporary copy is hidden from clipboard managers and Universal Clipboard.

### Desktop

- Menu bar (macOS) and tray (Linux) icon with status, pause, input device, transcription mode,
  recording limit, recent dictations, history search, vocabulary and API key.
- The menu shows problems that need you: a missing API key, a broken whisper.cpp setup, missing
  Accessibility access, a silent microphone, or a Wayland session.

### Installing

- `install.sh` installs exactly the dependency versions in `requirements.lock`, verified by hash,
  and no longer needs a C compiler on Linux.
- Updates build the new version next to the old one and keep the old one if anything fails.
- `install.sh` run on its own downloads the latest release and verifies its checksum, so
  `curl -fsSL .../install.sh | bash` works.
- `install.sh --uninstall` removes Vox and keeps your settings and history.
- Vox starts at login on Linux desktops without systemd session integration (Xfce, MATE and
  others) through an autostart entry, and warns about Wayland sessions.
- On macOS, Homebrew's `whisper-cli` and `tmux` are found when Vox runs at login, and the
  installer warns when a Python change means granting permissions again.
- A `.deb` package for Ubuntu 24.04 and derivatives (amd64 and arm64) that starts Vox at login for
  every user.
- `install.sh` refuses to run as root, retries `apt-get` after refreshing package lists, and tells
  users of other distributions which packages to install.

[1.0.0]: https://github.com/runsonmypc/vox/releases/tag/v1.0.0
