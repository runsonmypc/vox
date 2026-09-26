## Why

Vox keeps the OpenAI API key in plain text (`~/.config/vox/.env`, `config.toml`, or a checkout's `.env`), and the only way to add or change it is a text editor plus re-running `install.sh`. A missing key makes Vox exit with an error, which launchd and systemd treat as a crash and keep restarting. Anyone who installs Vox without the command line has no way to give it a key, and the key sits unencrypted on disk and in backups.

## What Changes

- Store the key in the operating system's keychain: the macOS Keychain, or the Secret Service (GNOME Keyring, KWallet) on Linux. The OS encrypts it and unlocks it at login.
- Add a "Set API Key…" tray item that opens a small window. It has a masked field with a Show toggle, shows where the key will be stored, and offers Save and Remove. It can optionally check the key with OpenAI before saving.
- When no key is set, Vox keeps running instead of exiting. The tray says a key is needed, the key window opens, and the hotkey explains why it can't record.
- A saved or removed key takes effect without restarting Vox.
- **BREAKING**: Vox no longer reads the key from `.env` files or `config.toml`. On first start it moves a key found in `~/.config/vox/.env` or `config.toml` into the keychain, checks it reads back, and then deletes the plain-text copy. The `OPENAI_API_KEY` environment variable still overrides the stored key.
- On Linux without any keyring service, the window saves the key to `~/.config/vox/.env` (owner-only) and warns that it is not encrypted.
- `install.sh` no longer asks for or copies the key; it installs and starts Vox, which asks for the key itself.

## Capabilities

### New Capabilities
- `api-key`: where the OpenAI API key is stored, how the user sets, replaces and removes it from the tray, how Vox behaves without one, and the move away from plain-text key files.

### Modified Capabilities
- `desktop-frontend`: the installer's "Fresh install" scenario no longer waits for an OpenAI API key before starting Vox at login.

## Impact

- New dependency: `keyring` (pure Python). On Linux it pulls `SecretStorage`, `jeepney` and `cryptography`, all available as wheels, so installs still need no compiler.
- Code: new `vox.keystore`, `vox.ui.key_model`, `vox.ui.key_window`, `vox.ui.mac.key` and `vox.ui.gtk.key`; `vox.config` drops `.env` loading and the `[api]` key; `vox.__main__` stops exiting without a key; `vox.daemon` and `vox.transcribe` pick up a key change at runtime; `vox.ui.tray` gains the menu item and a "key needed" state; `install.sh` drops `setup_key`.
- User data: both installed machines have a plain-text key that moves into the keychain and is deleted on first start after updating. The checkout's `.env` is left alone but no longer read.
- Builds on the whisper.cpp mode and tray provider switching (51c9c18) and the native AppKit and GTK windows (1fdab3f). The missing-key behavior applies only to modes that call OpenAI.
