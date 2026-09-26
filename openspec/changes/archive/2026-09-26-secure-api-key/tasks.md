## 0. Prerequisite

- [x] 0.1 Confirm the parallel whisper.cpp work is committed and the working tree has no changes to `vox/__main__.py`, `vox/config.py`, `vox/daemon.py`, `vox/transcribe.py` or `install.sh`; verify with `git status`

## 1. Keystore

- [x] 1.1 Add `keyring` to `pyproject.toml` and add an autouse fixture in `tests/conftest.py` that installs an in-memory backend for every test; verify with a test asserting `keyring.get_keyring()` is the fake backend, so no test can reach the real Keychain or Secret Service
- [x] 1.2 Add `vox/keystore.py` with `get_api_key`, `get_stored_key`, `set_api_key`, `delete_api_key` and `storage_name` (environment override, then keychain, then the Linux mode-600 fallback file only when the fail backend is active); verify with tests for each source, for a locked backend raising without writing plain text, and for the fallback file's mode
- [x] 1.3 Add `keystore.migrate_plaintext` (config.toml `[api]` then `~/.config/vox/.env`; store, read back, then delete; keep and warn on a differing key; delete a matching one; keep `config.toml` comments; never raise); verify with tests covering each "Moving Plain-Text Keys" scenario, using dummy keys only

## 2. Config and startup

- [x] 2.1 Remove `.env` loading and the `[api]` mapping from `vox.config.load_config`, add `Config.uses_openai`, stop the config reloader copying the key, and update `config.example.toml`; verify `tests/test_config.py` passes and a test shows `.env` values no longer reach `os.environ`
- [x] 2.2 Change `vox.__main__` to run migration, load the key through the keystore, and start without a key; make `daemon.run` exit 1 when there is no tray and no key; verify with tests in `tests/test_main.py`

## 3. Daemon

- [x] 3.1 Make `Transcriber` build its OpenAI client lazily and rebuild it when `config.openai_api_key` changes; verify with a test that an empty key no longer raises at construction and a changed key reaches the next request
- [x] 3.2 Handle the `api_key` event (re-read in an executor), and the missing-key and unreadable-keyring cases on a hotkey press (re-read first; then the error sound and, only for a missing key, the key window); open the key window once at start when the key is missing; verify with daemon tests

## 4. Key window and tray

- [x] 4.1 Add `vox/ui/key_model.py` (storage line or unencrypted warning, last-four hint, environment-override notice, key validation, save, remove, and "Check with OpenAI" using a models list); verify with unit tests mocking the keystore and the OpenAI call
- [x] 4.2 Add `vox/ui/key_window.py`, `vox/ui/mac/key.py` (AppKit, `NSSecureTextField`, Show swaps in a plain field) and `vox/ui/gtk/key.py` (libadwaita `Adw.PasswordEntryRow`), with Save, Remove and Cancel and the check on a background thread; verify with window tests under the existing pattern (AppKit on the Mac, GTK under xvfb on the PC)
- [x] 4.3 Add "Set API Key…" to the tray, show "API key needed" or "Can't read the keyring" in the status line with the item moved to the top, and send `api_key` to the daemon when the key window exits; verify with tray tests
- [x] 4.4 Ask the user which way "Check with OpenAI" defaults, then set it; verify the window opens with that state

## 5. Installer

- [x] 5.1 Remove `setup_key` from `install.sh` so it always installs and starts the service, and change its closing message to say Vox will ask for the key; verify with `bash -n install.sh` and a dry read of the script

## 6. Verification

- [x] 6.1 Run the full test suite on the Mac and the window, tray and integration tests on the PC under `dbus-run-session -- xvfb-run -a`; verify all pass
- [x] 6.2 With the user's go-ahead, run `./install.sh` on the Mac and confirm from the log that the key moved into the Keychain and `~/.config/vox/.env` is gone, without printing the key
- [x] 6.3 With the user's go-ahead, pull and install on the PC and confirm the same against GNOME Keyring
