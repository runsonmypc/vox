## Context

See proposal.md for why. The requirements are in the delta specs: `desktop-frontend` (Settings Window, General Settings, Microphone, Transcription, Hotkey, Vocabulary), `dictation-pipeline` (Recording Limit, Settings Reload, Input Device Rescan), `api-key` and `screen-context`.

What exists today and shapes the approach:
- Every window is its own process, started by the tray with `python -P -m vox.ui.<name>_window`. The tray waits on each process in a thread, brings an open one forward instead of starting a second, and calls an `on_exit` callback however the process ends. Windows never talk to the daemon: they write `config.toml` or the keychain, and the daemon picks the change up.
- Each window has a toolkit-free model (`vocab_model`, `key_model`, `hotkey_model`) and two thin views: AppKit in `vox/ui/mac/`, GTK 4 with libadwaita in `vox/ui/gtk/`. The macOS vocabulary window is already an `NSTabViewController` with toolbar tabs, the macOS Settings layout. The GTK one is an `Adw.ViewStack` with an `Adw.ViewSwitcher` in the header bar.
- `vox/config.py` writes through `_read_document`, `_edited_table` and `_write_document`. This is an atomic tomlkit write that keeps comments and the file's mode, creates a new file 0600, and never writes a file that would not load. The existing writers are `update_dictionary`, `update_snippet`, `update_transcription_mode`, `update_max_recording_seconds` and `update_hotkey`.
- The daemon's `_config_reloader` polls `config.toml` every 2 s and applies every setting, including `[hotkey]` through `_apply_hotkey`. Audio settings are applied only when the file changed them. `mode_problem` in `vox/modes.py` decides whether a mode can run.
- The tray sends `hotkey:suspend` and `hotkey:resume` around the hotkey window, and `api_key` when the key window closes. `mode:<m>` and `limit:<s>` come from the submenus. The device submenu sets `config.audio_device` in memory only, from the device list the daemon sends.

## Goals / Non-Goals

**Goals:**
- One Settings process that hosts the five pages. Each page reuses the existing model and view code, not a rewrite.
- Every change reaches `config.toml` through the existing atomic writer, so the file's safety guarantees still hold.
- Nothing the window saves waits for the 2 s poll once the window has closed.

**Non-Goals:**
- Settings the window does not show (see the proposal). They stay file-only.
- Showing custom sound files, or choosing them. `~/.config/vox/sounds` stays a folder the user fills.
- Live device hotplug in the list. Refresh and reopening cover it.
- Talking to the daemon from the window, or dictating while Settings is open.
- Wayland and headless. No tray means no Settings, as before.

## Decisions

**D1. One process, `vox.ui.settings_window --config PATH [--page NAME]`, one model per page.** A new `SettingsModel` holds the General and Transcription settings and owns the shared state: the config path, the loaded `Config`, and the load error. The Vocabulary, Snippets, Hotkey and API key pages keep `VocabModel`, `HotkeyModel` and `KeyModel` unchanged, built with the same path. On macOS, `SettingsController` adds the General, Hotkey and Transcription tabs to the vocabulary window's `NSTabViewController`. On Linux, `SettingsWindow` adds `Adw.PreferencesPage`s to the vocabulary window's `Adw.ViewStack`. The hotkey and key views become page views: their Save/Cancel buttons are removed, and the key entry becomes a sheet (macOS) or `Adw.Dialog` (Linux) opened from the page.
- Rejected: keeping separate windows and adding a fourth for the rest. That is what the owner asked to replace.
- Rejected: `Adw.PreferencesDialog` on Linux. Vocabulary and Snippets need list editing and their own dialogs, which the current window already does inside an `Adw.ApplicationWindow`.

**D2. Apply on change, per platform convention.** Switches, choices and recorded keys write at once. Text fields write on Return, on focus leaving, and on window close. The slider writes on release. A write goes through the model, which calls a writer in `vox/config.py`. On `ConfigError` or `OSError`, the view shows "Couldn't Save" and reloads the control from the file.
- Rejected: a Save/Cancel pair for the whole window. Neither macOS nor GNOME settings work that way, and it would mean the vocabulary pages, which already apply at once, behave differently from the rest.
- The API key is the exception: it keeps Save, the OpenAI check, and Remove with a confirmation (see the api-key spec).

**D3. New writers.** `vox/config.py` gets one small writer per setting, all on the existing `_edited_table`/`_write_document` path: `update_audio_device(path, name | None)`, `update_flag(path, section, key, value)` for sounds, attenuation and screen, `update_attenuation_level(path, level)`, `update_language(path, code | None)`, `update_prompt(path, text)`, and `update_whisper_cpp(path, binary=…, model=…)`. `None` or an empty string removes the key, so the file only holds what differs from the default when the user clears it. The existing `update_transcription_mode`, `update_max_recording_seconds`, `update_hotkey`, `update_dictionary` and `update_snippet` are used as they are. A write that changes nothing writes nothing, so opening Settings never creates `config.toml` (the spec's "Nothing changed").

**D4. `settings:open` / `settings:closed` replace `hotkey:suspend`, `hotkey:resume` and `api_key`.** The tray sends `settings:open` before starting the process, and `settings:closed` from `on_exit`, or at once if the launch fails. So the order is always open, then closed (the same reasoning as the hotkey window's D4). On open, the daemon sets `hotkey_suspended` and cancels a recording that is running. The existing `_suspend_hotkey` logic becomes `_settings_opened`. On close, `_settings_closed` clears the flag, reloads the API key (the old `api_key` handler), and runs one reload pass. That pass is the reloader's apply step, factored out of `_config_reloader` into `_apply_config(new)` so that both call it: every setting, `_apply_hotkey`, the audio rule, and the tray refresh. A mode change that arrives this way is checked on the next hotkey press, as a hand edit is today.
- Rejected: keeping the three old events and sending all of them on close. They would race, and "suspend" no longer describes just the hotkey.
- Rejected: suspending only while the Hotkey page records keys. That needs the window to talk to the daemon, which no window does. Dictating into the Settings window is not a use case worth that.

**D5. The status line says dictation is off.** While `hotkey_suspended` is set and nothing more urgent is wrong, the tray's first line reads "Dictation is off while Settings is open". The hotkey is silent while suspended (no busy sound, as with the hotkey window), and this explains it.

**D6. Menu.** `_menu_items` keeps the status line, "Set API Key…" at the top only while `_key_problem()` is set (it now opens Settings with `--page transcription`), Pause Dictation, recent dictations, Search History…, "Settings…" (enabled only while idle), and Quit. `_device_items`, `_limit_items`, `_transcription_items`, `_mode_setter`, `_limit_setter`, `_device_setter`, `_open_vocab`, `_open_key`, `_open_hotkey` and `set_devices` go. `open_key_window()`, which the daemon calls when the key is missing, becomes `open_settings(page="transcription")`. If Settings is already open, it is brought forward on the page it shows. There is no channel to switch its page, and the only way to reach the "key missing" path with Settings open is at startup, before the user could have opened it.

**D7. Microphones are listed by the window and saved by name.** The window lists input devices with `sounddevice` in its own process when it opens and on Refresh. The tray never did this, because the query blocks its UI thread. In the window's process it happens before the page shows, and on Refresh with a spinner. The selected row uses `match_input_device`, the recorder's own matching, so the selected device is the one that records. System Default removes `[audio] device`. The daemon's rescan stays, because PortAudio only sees devices connected after start once it is re-initialised. It stops sending the list to the tray (`set_devices`, `_apply_devices` and `_last_devices_sent` go).
- Rejected: getting the list from the daemon. The window would need to talk to the daemon (see D4).
- Consequence: the reloader's audio rule no longer has a menu choice to protect, since every device choice is now in the file. It stays as it is, because it also keeps a recording from being reconfigured mid-way.

**D8. Mode availability computed in the window.** The Transcription page calls `mode_problem(config, mode)` with a `Config` from the file, plus `openai_api_key` from `keystore.get_api_key()`, so the page follows the daemon's rules. It checks again after a key save or removal, and after the whisper.cpp paths change. The saved mode stays selectable even when it cannot run, as the menu did, so the user sees why.

**D9. Language list.** `settings_model.LANGUAGES` is a fixed list of `(code, name)` pairs for the languages Whisper supports, which OpenAI's speech-to-text guide refers to, sorted by name, with "Detect automatically" first. Javanese is left out, since Whisper names it `jw` rather than by its ISO 639-1 code `jv`. A code in the file that is not in the list is shown as the raw code and kept. It is only written when the user picks something else.

**D10. Reload while open.** The window polls the file's stamp every 2 s, as the daemon does. When the file changed and loads, it refreshes the controls that are not being edited. When it doesn't load, it shows the error state from the spec. A control that is focused or recording keeps its edit, and the next write merges into the new file, because every writer re-reads the document before writing.

**D11. Entry points.** `vox.ui.vocab_window`, `vox.ui.key_window` and `vox.ui.hotkey_window` are removed. Nothing outside the tray and the tests starts them, and there are no console scripts for them (`pyproject.toml` has only `vox`). `vox.ui.history_window` stays.

## Risks / Trade-offs

- [No dictation while Settings is open] A user who leaves Settings open wonders why the hotkey is silent. → The status line says so (D5), and the window repeats it in small text: in the title bar on macOS, and in a line at the bottom on Linux.
- [Listing devices on macOS] PortAudio's first query can take up to a second. → It runs before the window shows the General page, and on Refresh with a spinner. It never runs on the tray's thread.
- [Keychain prompt in a new process] Reading the key from the window process could prompt on macOS if the keychain item's access list does not include the Python binary. → The window runs the same interpreter as the daemon and the old key window, which already read it without a prompt.
- [Microphone choice is now kept] A user who picked a device from the menu expected it to reset after a restart. → This is the documented change (BREAKING in the proposal), and System Default restores the old default.
- [Write storms] Each change is a whole-file atomic write, and a hand edit in an editor at the same moment could be overwritten. → The same risk exists with the vocabulary window today. Writers re-read the file before each write, so the window never works from a stale copy for longer than one write.
- [Untested on a real display] Tests drive the views with synthetic events, headless (Xvfb on the Linux PC; never its live display). → A person tries every page once on macOS and on Linux before release (task 9.4).

## Migration Plan

- There is no data migration: `config.toml` keeps its keys, and no default changes.
- One release note: the Input Device, Transcription and Recording Limit submenus are now in Settings, and a microphone choice is now kept.
- Rollback is reverting the change. The settings the window wrote are ordinary `config.toml` keys that older builds read the same way.

## Open Questions

- The exact wording and order of rows on each page, and the page icons (SF Symbols on macOS, symbolic icons on Linux), can be settled during implementation with screenshots. They don't change behavior.
