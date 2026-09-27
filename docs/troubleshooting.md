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
| API key needed | Choose **Set API Key…**, or switch to Local (whisper.cpp). |
| Can’t read the keyring | Unlock your login keychain or keyring; Vox Transfer tries again on the next hotkey press. Without a keyring, **Set API Key…** says why `~/.config/vox/.env` can’t be read; it must be owned by you, readable (`chmod 600`) and plain UTF-8 text. |
| A whisper.cpp problem, such as a missing model | Fix `[whisper_cpp]`, or choose an OpenAI mode. |
| Accessibility access needed | macOS: allow python3.12 under Accessibility, then quit and reopen Vox Transfer. |
| Microphone is silent: check its permission | Allow the microphone (macOS: Microphone for python3.12) and check the Input Device menu. |
| Wayland: hotkey and paste only work in X11 apps | See [Wayland](install.md#wayland). |
| Last dictation only partly transcribed: see History | A long recording failed partway. The parts that were transcribed are in **Search History…**. |

## Common problems

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

Still stuck? An AI coding assistant can read the log and your settings with you: point it at
[the setup guide for AI assistants](ai-setup.md).
