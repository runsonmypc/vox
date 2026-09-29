## Why

Vox Transfer's settings are spread across three menu submenus, three separate windows (Vocabulary & Snippets, Set API Key, Set Hotkey) and hand edits of `config.toml`. Common settings such as the language, the prompt, sounds, lowering the volume, screen hints and the whisper.cpp paths can only be changed in the file, which most users will never open. The owner wants one Settings window that holds them all, the way desktop apps usually do.

## What Changes

- **New Settings window** (AppKit on macOS, GTK 4 with libadwaita on Linux; a separate process like the other windows), with five pages:
  - **General**: microphone, recording limit, sounds, lower the volume while recording (and how far), and screen hints.
  - **Hotkey**: the tap-alone key and the optional key combination, recorded as the Set Hotkey window records them today.
  - **Transcription**: the mode (OpenAI batch, OpenAI streaming, local whisper.cpp), the spoken language, the prompt, the OpenAI API key, and the whisper.cpp program and model files.
  - **Vocabulary**: dictionary words.
  - **Snippets**: trigger phrases and their expansions.
- **Changes apply as they are made**, following each platform's settings conventions, with no Save button. Each change is written to `config.toml` at once, keeping the rest of the file and its comments. The API key keeps its own Save and Remove buttons, because it is a secret that can be checked with OpenAI first.
- **Settings the window does not show stay in the file and are never changed by it**: sample rate, channels, `double_tap_timeout_ms`, the OpenAI model names and `[window_classes]`.
- **BREAKING (menu)**: the tray menu gets a single "Settings…" item, available only while Vox is idle. The Input Device, Transcription and Recording Limit submenus and the Vocabulary & Snippets…, Set API Key… and Set Hotkey… items are removed. When the API key is missing, the "Set API Key…" item at the top of the menu stays, and opens Settings on the Transcription page. The menu keeps the status line, Pause Dictation, recent dictations, Search History… and Quit.
- **BREAKING (microphone)**: a microphone picked in Settings is saved to `[audio] device` by name and is kept after Vox restarts. The menu's choice used to last only until Vox quit.
- **Dictation is off while Settings is open**: the tray sends `settings:open` before the window starts and `settings:closed` when its process ends, however it ends. These replace `hotkey:suspend`, `hotkey:resume` and `api_key`. When the window closes, the daemon reads the API key and `config.toml` again at once, so everything saved applies straight away. Edits also apply within a few seconds through the existing settings reload.
- The daemon no longer sends its input-device list to the tray. It still rescans devices, so a microphone picked by name that is plugged in later can record.
- The standalone vocabulary, API key and hotkey windows are replaced by pages of the Settings window. Their models (`vocab_model`, `key_model`, `hotkey_model`) and their rules carry over unchanged.
- README, `docs/settings.md`, CHANGELOG, `config.example.toml` and the screenshots describe the Settings window.

## Capabilities

### New Capabilities

None. The Settings window is part of the existing desktop front end.

### Modified Capabilities

- `desktop-frontend`: new requirements for the Settings window and its General page; the menu loses its settings submenus; audio device, transcription, vocabulary and hotkey selection move into Settings.
- `dictation-pipeline`: the recording limit is chosen in Settings, not a submenu; Settings Reload applies at once when Settings closes; Input Device Rescan no longer feeds a tray menu.
- `api-key`: the key is entered on the Transcription page of Settings, which is where Vox opens it when the key is missing.
- `screen-context`: screen hints can be turned on and off in Settings.

## Impact

- Code: new `vox/ui/settings_model.py`, `vox/ui/settings_window.py`, `vox/ui/mac/settings.py` and `vox/ui/gtk/settings.py`. The page code comes from `vox/ui/mac/{vocab,key,hotkey}.py` and `vox/ui/gtk/{vocab,key,hotkey}.py`. The `vox/ui/{vocab,key,hotkey}_window.py` entry points are removed. Also changed: `vox/ui/tray.py` (menu, `settings:open`/`settings:closed`, no device list), `vox/daemon.py` (the new events replace the old ones and reload the key and the file on close; the device list is no longer sent), and `vox/config.py` (new writers for the General and Transcription settings, on the existing atomic writer).
- Events: `settings:open` and `settings:closed` replace `hotkey:suspend`, `hotkey:resume` and `api_key`. The `mode:` and `limit:` events and the tray's device setter are removed.
- Settings: no new keys and no default changes. `[audio] device` is now written by the app.
- Dependencies: none new.
- Tests: new `tests/test_settings_window.py`. The window tests for vocabulary, key and hotkey move to the Settings pages. `test_tray.py` and `test_pipeline_daemon.py` change for the new menu and events.
