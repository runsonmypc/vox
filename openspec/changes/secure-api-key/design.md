## Context

See proposal.md for motivation and specs/api-key/spec.md for behavior.

Today `vox.config.load_config` reads `[api] openai_api_key` from `config.toml`, then loads every `KEY=VALUE` from the checkout's `.env` and `~/.config/vox/.env` into `os.environ`, then falls back to `OPENAI_API_KEY`. Because of that, the key sits in the environment of every child process (the history and vocabulary windows, and the whisper.cpp subprocess). `vox.__main__` exits 1 without a key (unless the mode is `whisper_cpp`). `install.sh` `setup_key` copies the key into `~/.config/vox/.env`. The config reloader copies `openai_api_key` from each reload, and `Transcriber` builds its `AsyncOpenAI` client once in `__init__`; openai 3.14.1 raises `OpenAIError` when that key is empty.

Vox runs as a launchd agent (macOS) or a systemd user service (Linux), inside the user's GUI session, so both the login keychain and the session D-Bus are reachable. On the Mac, the interpreter is uv's CPython 3.12.5 with only an ad-hoc signature. The windows are separate processes started with `sys.executable -m vox.ui.<window>` (AppKit on macOS, GTK 4 and libadwaita on Linux, each drawing from a toolkit-independent model), so they run the same binary as the daemon.

## Goals / Non-Goals

**Goals:**
- Only the user, through the OS keychain, can read the key at rest. It is never in plain text on disk when a keychain exists.
- Setting, replacing and removing the key takes no text editor, no terminal, and no restart.

**Non-Goals:**
- Protecting the key from other code running as the same user. On Linux, any process in the session can ask the unlocked Secret Service. On macOS, any script run by the same Python binary is trusted. That is the normal desktop keychain model.
- A command-line key setter. `OPENAI_API_KEY` covers scripted and development use.
- Keys for providers other than OpenAI.

## Decisions

### 1. Use the `keyring` library
It wraps the macOS Keychain (`SecItemAdd` / `SecItemCopyMatching` through ctypes) and the Secret Service (pure-Python D-Bus through `jeepney`), so one API covers both platforms with no compiler.
- Alternative: shell out to `security` and `secret-tool`. Rejected. `security add-generic-password -w KEY` puts the key in argv, and the Keychain would then trust `/usr/bin/security`, so any process could read the key through it without a prompt. `secret-tool` is not installed by default.
- Alternative: PyObjC Security on macOS. PyObjC is already a dependency there, but Linux would still need a separate path.

Entry: service `vox`, username `openai_api_key`.

### 2. Decide the fallback by backend, never by error
`vox.keystore` asks `keyring.get_keyring()` which backend won.
- **No backend:** `keyring` picks the fail backend (priority 0) only when no real backend is viable; a chainer wins only when two or more are. So the fail backend means no Secret Service provider exists. Only then does Vox use the plain-text file, `~/.config/vox/.env`, created with mode 600 and read directly. It is not loaded into `os.environ`.
- **Real backend that raises** (`KeyringLocked`, access denied, a D-Bus error): the error is surfaced, and Vox never writes plain text. A transient keyring error therefore cannot quietly downgrade storage.
- Tests pin the backend with `keyring.set_keyring` (see Risks).

### 3. The key window writes the keychain itself
The key window matches the history and vocabulary windows: `vox.ui.key_window` picks `vox.ui.mac.key` (AppKit, `NSSecureTextField`) or `vox.ui.gtk.key` (libadwaita `Adw.PasswordEntryRow`, which has its own reveal button), both drawing from the toolkit-independent `vox.ui.key_model`. It runs as its own process started from the tray and reads and writes the keychain directly, so the key never crosses a pipe, argv or the environment. Because it runs the same interpreter binary as the daemon, macOS treats both as the same keychain client, and neither gets a prompt.

The window has these parts:
- A masked field with a way to reveal it (a Show checkbox on macOS, which swaps in a plain field; the entry's own reveal button on Linux).
- Where the key is kept: "macOS Keychain", "your login keyring" (Secret Service) or "KWallet", or a warning naming the unencrypted file.
- "Saved key ending in abcd" (the last four characters only), or an error if the keychain can't be read.
- An override notice when `OPENAI_API_KEY` is set.
- A "Check with OpenAI" checkbox.
- Remove, Cancel and Save buttons.

The check lists models (`GET /v1/models`) on a background thread, with no retries. It transcribes nothing and uses no tokens.
- A 401 blocks the save.
- Anything else (no connection, a timeout, a 5xx, or a 403 from a restricted key that can't list models) says the key couldn't be checked and offers "Save Anyway".

A key is trimmed. An empty key, or one with spaces inside, is refused before anything is stored.

### 4. The key lives on `Config`, loaded outside `load_config`
`load_config` stops touching the key. The config reloader and the vocabulary window both call it, and a keychain read there could block on an unlock prompt. `vox.__main__` fills `config.openai_api_key` from `keystore.get_api_key()` once, after migration. The reloader no longer copies the key.

A key change reaches the daemon as an `api_key` queue event. The tray sends that event when the key-window process exits, whatever its exit code; a re-read is cheap and harmless. The daemon re-reads the keystore in an executor thread, so an unlock prompt can't stall the event loop, and updates `config.openai_api_key`. `Transcriber` builds its client lazily and rebuilds it whenever `config.openai_api_key` differs from the key it was built with. Streaming already reads the key per connection. So an updated `Config` is all a new key needs, whichever path changed it.
- Alternative: re-read the keychain on every hotkey press. Rejected, because it adds a keychain or D-Bus round trip to the hotkey path that was recently tuned for latency. The keychain is re-read on a hotkey press only when the key is missing.

### 5. No key is a state, not an exit
`Config.uses_openai` (the batch and streaming modes) decides whether a key is needed. When it is and none is found, `__main__` still starts the daemon.
- The tray's status line reads "Vox — API key needed", and "Set API Key…" moves to the top of the menu. The tray reads this from the shared `Config`, so switching to whisper.cpp clears it without extra messages.
- The key window opens once at start.
- A hotkey press re-reads the keystore first. If there is still no key, it plays the existing `error` sound and opens the window instead of recording.
- With no tray (headless), `daemon.run` logs how to set the key and exits 1, as today. It is not under a service in that case.
- **Locked keyring:** a Secret Service locked after autologin prompts for the login password on the first read. If the read fails, the daemon keeps the error, and the tray shows "Vox — Can't read the keyring" instead of "API key needed". A hotkey press retries the read, which prompts again, but doesn't open the key window, so the user is never pushed to overwrite a key that exists.

### 6. Migration runs in Vox, not the installer
On start, before the key is read, `keystore.migrate_plaintext()` runs only when a real backend is present and holds no key.
It looks at `config.toml` `[api].openai_api_key` first (it used to win), then the `OPENAI_API_KEY` line in `~/.config/vox/.env`. For each source that has a key:
1. If the keychain is empty, it calls `set_password`, then `get_password`, and compares the two. On any mismatch or error, it stops and deletes nothing.
2. A key that matches the keychain's is removed from its source: the `.env` line (deleting the file when only blank lines are left), or the `config.toml` setting through tomlkit, like the vocabulary writes, removing an `[api]` table left empty.
3. A key that differs from the keychain's is left in place, with a warning naming the file.

It never raises. A keychain error is logged, and Vox starts with whatever key it can read.

Running this in Vox means an update with `./install.sh`, or any start of a new build, moves the key. The installer never handles it.

### 7. Stop loading `.env` into the environment
`_load_dotenv` and the `[api]` mapping are removed from `load_config` (Decision 4). `vox.keystore.get_api_key()` returns `OPENAI_API_KEY` from the real environment when it is set, then the keychain, then the Linux fallback file. The checkout's `.env` is no longer read at all. `start.sh` users export the variable instead.

## Risks / Trade-offs

- **[Risk] A Python upgrade changes the ad-hoc signature, and macOS asks "python3.12 wants to use your confidential information stored in 'vox'".** → The dialog is expected. The user clicks Always Allow, or re-saves the key from the window. The design notes this, and the window's error text points to it.
- **[Risk] Tests touch the real keychain.** → An autouse fixture in `tests/conftest.py` installs an in-memory `keyring` backend for every test, and the keystore tests assert the backend is the fake one. Tests use dummy keys only.
- **[Risk] Migration deletes the only copy of the key.** → The plain-text copy is deleted only after a successful read-back comparison. Nothing is deleted on any error.
- **[Risk] A Linux session with no Secret Service stores the key in plain text.** → This is the user's chosen fallback. The window warns before saving, and the file is mode 600.
- **[Trade-off] A key change reaches the daemon only when the key window closes, or on a hotkey press while the key is missing.** → A key edited in Keychain Access or Seahorse while Vox runs is picked up after Quit and reopen. That is acceptable for a rare action.

## Migration Plan

1. Builds on 51c9c18 (whisper.cpp) and 1fdab3f (native windows).
2. `./install.sh` on each machine. On first start, Vox moves the key into the keychain and deletes `~/.config/vox/.env`. The log names what moved.
3. Rollback: revert the commit and re-run `./install.sh`. Older Vox reads only plain text, so recreate `~/.config/vox/.env` with the key printed by `~/.local/share/vox/venv/bin/python -c 'import keyring; print(keyring.get_password("vox", "openai_api_key"))'` (run before reverting; `secret-tool` is not installed on the PC).

## Open Questions

- Resolved: "Check with OpenAI" starts checked, as the user chose once the window was built.
