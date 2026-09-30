# Troubleshooting

[Back to the README](../README.md)

- [Menu messages](#menu-messages)
- [Common problems](#common-problems)

## Menu messages

While Vox Transfer is idle, the first line of its menu names the most urgent problem that stops or
affects dictation, in this order:

| Message | What to do |
| --- | --- |
| Settings file has an error | Fix `~/.config/vox/config.toml`; the log names the setting. Vox Transfer does not record until then. |
| API key needed | Choose **Set API Key…**, or switch to Local (whisper.cpp) in Settings. |
| Can’t read the keyring | Unlock your login keychain or keyring; Vox Transfer tries again on the next hotkey press. Without a keyring, **Set API Key…** says why `~/.config/vox/.env` can’t be read; it must be owned by you, readable (`chmod 600`) and plain UTF-8 text. |
| A whisper.cpp problem, such as a missing model | Fix `[whisper_cpp]`, or choose an OpenAI mode. |
| Accessibility access needed | macOS: allow python3.12 under Accessibility, then quit and reopen Vox Transfer. |
| Microphone is silent: check its permission | Allow the microphone (macOS: Microphone for python3.12) and check the microphone on the General page of Settings. |
| Wayland: hotkey and paste only work in X11 apps | See [Wayland](install.md#wayland). |
| Last dictation only partly transcribed: see History | A long recording failed partway. The parts that were transcribed are in **Search History…**. |

## Common problems

The commands below use `~/.local/bin/vox`, where the installer puts Vox Transfer, since
`~/.local/bin` is often not on your `PATH`. With the `.deb`, use `/usr/bin/vox`.

- **The hotkey does nothing (macOS).** Check that python3.12 is allowed under Accessibility and
  Input Monitoring in System Settings > Privacy & Security, then quit and reopen Vox Transfer. After
  an update that changed Python, remove the old entries and allow the new ones.
- **Recordings come back empty.** Allow the microphone (macOS: Microphone permission for
  python3.12), check the microphone on the General page of Settings, and try
  `~/.local/bin/vox --list-devices`.
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
- **Local (whisper.cpp) can't be chosen.** The line under it on the Transcription page of Settings
  says what is missing. Choose the whisper.cpp program and model there, or set `[whisper_cpp] model`,
  and `binary` as a full path.
- **Local transcription is slow or fails while a game is running.** whisper.cpp keeps the model on
  the graphics card, and a game can leave no room for it. Vox Transfer then transcribes on the
  processor instead, which is slower; the log says so. With `[whisper_cpp] cpu_fallback = false`,
  dictation fails instead, with the error sound. A smaller model needs less room.
- **"Vox Transfer needs Python 3.12 or 3.13".** Some of Vox Transfer's dependencies do not yet
  publish packages for newer Pythons, and building them would need a compiler.
- **Something else.** Read the log (`~/Library/Logs/Vox/vox.log` on macOS,
  `journalctl --user -u vox -e` on Linux). For more detail, quit Vox Transfer and run
  `~/.local/bin/vox -v` in a terminal; note that verbose logs include what you dictate and the
  titles of your windows.

Still stuck? An AI coding assistant can read the log and your settings with you: point it at
[the setup guide for AI assistants](ai-setup.md).

## Failed recording recovery

Open **Search History…** after a terminal transcription error. Local retry needs a working
whisper.cpp executable and model; OpenAI Batch needs a key and uploads the entire saved
recording, potentially incurring another charge. The selected method is checked independently
of the default method. Close Settings and wait for the current dictation before retrying.

If recovery is disabled, enable **Keep failed recordings for retry** in General Settings.
If Vox is not running, start it and reopen History. A failed control connection while Vox is
running blocks deletion until it reconnects, so deletion cannot race an active retry.

“Recording could not be saved” means the WAV was not retained; any available partial text
is preserved when possible. “Audio was saved but is not yet available in History” means the
index could not be written: fix storage permissions or free disk space, then restart Vox or
reopen History to reconcile it without retranscription. Missing or corrupt audio is refused
before calling a provider; copy available text and delete the entry or record again.

If History cannot yield focus, select an external app and try again. Reopening a Vox window
at completion leaves the full result ready to copy. A paste failure also leaves completed
text in History. Saved audio is removed after full text is saved or through Delete/Clear;
cleanup errors remain pending and are retried on startup. There is no expiry.
