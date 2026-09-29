# api-key Specification

## Purpose
Keeps the user's OpenAI API key encrypted by the operating system and lets them set, replace and remove it from the tray, without a text editor or the command line.

## Requirements

### Requirement: Encrypted Key Storage
The system SHALL store the OpenAI API key in the operating system's keychain: the macOS Keychain, or the Secret Service on Linux. The key SHALL NOT be written to disk in plain text while a keychain is available, and SHALL NOT appear in logs, command-line arguments, or the environment of processes Vox starts. With a tray, a plain-text fallback file that cannot be read SHALL NOT stop Vox from starting; headless Vox, which has nowhere to ask for a key, treats it like a missing key.

#### Scenario: Saving on macOS
- **WHEN** the user saves a key on macOS
- **THEN** it is stored in the login keychain as the item "vox" (account "openai_api_key") and no plain-text copy is written

#### Scenario: Saving on Linux with a keyring
- **WHEN** the user saves a key on a Linux desktop running a Secret Service provider such as GNOME Keyring or KWallet
- **THEN** it is stored in that keyring and no plain-text copy is written

#### Scenario: Linux without a keyring
- **WHEN** the user saves a key on Linux and no Secret Service provider is available
- **THEN** the key is saved to `~/.config/vox/.env` readable only by the user, and the Transcription page of Settings warns beforehand that it will not be encrypted

#### Scenario: Unreadable plain-text file
- **WHEN** Vox has a tray, no keyring is available, and `~/.config/vox/.env` cannot be read (no permission, a directory, or not UTF-8 text)
- **THEN** Vox still starts and reports a key it can't read (the tray's first line says "Can’t read the keyring" and the Transcription page of Settings shows the reason), and saving or removing a key fails with that reason and leaves the file as it is

#### Scenario: Keyring locked or refusing
- **WHEN** a keychain is available but locked, or refuses access
- **THEN** the key is not saved anywhere else and the window shows the error

### Requirement: Key Entry in Settings
The Transcription page of Settings SHALL show whether an OpenAI API key is saved, showing only its last four characters, and SHALL let the user set, replace and remove it. Key entry SHALL mask the key unless the user turns on Show, SHALL say where the key will be stored, and SHALL refuse a key with a character that cannot be part of an OpenAI key. Unlike other settings, the key SHALL be saved only when the user clicks Save.

#### Scenario: Setting a key
- **WHEN** the user clicks Set Key…, pastes a key and clicks Save
- **THEN** the key is stored, the page shows its last four characters, the OpenAI modes become available, and the first dictation after the window closes uses it without restarting Vox

#### Scenario: Replacing a key
- **WHEN** a key is already saved and the user saves a different one
- **THEN** the new key replaces the old one and takes effect without restarting Vox

#### Scenario: Removing a key
- **WHEN** the user clicks Remove and confirms
- **THEN** the stored key is deleted and Vox behaves as if no key was set

#### Scenario: Checking the key before saving
- **WHEN** the user saves with "Check with OpenAI" turned on and OpenAI rejects the key
- **THEN** the key is not saved and the page says OpenAI rejected it

#### Scenario: OpenAI unreachable during the check
- **WHEN** the check cannot reach OpenAI
- **THEN** the page says so and lets the user save the key anyway

#### Scenario: Key with a character that is not part of an OpenAI key
- **WHEN** the user saves a key that contains a character outside printable ASCII (such as a zero-width space, byte-order mark, curly quote or en dash)
- **THEN** the key is not saved, whether or not "Check with OpenAI" is on, and the page says the key has a character that isn't part of an OpenAI key and asks the user to copy it again

#### Scenario: Settings file broken
- **WHEN** `config.toml` cannot be loaded
- **THEN** the key can still be set, replaced and removed, because it is not stored in `config.toml`

### Requirement: Running Without a Key
When the configured transcription mode calls OpenAI and no key is available, the system SHALL keep running instead of exiting, so the service manager does not restart it.

#### Scenario: Starting without a key
- **WHEN** Vox starts with a tray and no key
- **THEN** the tray says "API key needed" and Settings opens on the Transcription page

#### Scenario: Hotkey without a key
- **WHEN** the user presses the hotkey and no key is set
- **THEN** no recording starts, the error sound plays, and Settings opens on the Transcription page, or comes to the front if it is already open

#### Scenario: Starting without a key or a tray
- **WHEN** Vox starts with no key and no tray (for example over SSH)
- **THEN** it logs how to set the key and exits with an error, as before

#### Scenario: Local transcription
- **WHEN** the configured transcription mode does not call OpenAI
- **THEN** Vox runs normally without a key and does not open Settings

### Requirement: Key Sources
The system SHALL use the `OPENAI_API_KEY` environment variable when it is set, and otherwise the stored key. It SHALL NOT read the key from `.env` files or `config.toml`, except for the Linux plain-text fallback file when no keyring is available. A key taken from the environment SHALL be removed from Vox's own environment at startup, before Vox starts any process.

#### Scenario: Environment variable override
- **WHEN** `OPENAI_API_KEY` is set in Vox's environment
- **THEN** Vox uses it, removes it from its own environment at startup so processes Vox starts (whisper-cli, xclip, its windows and others) do not inherit it, and the Transcription page of Settings, which learns of the override without receiving the key, says the environment variable is overriding the stored key

#### Scenario: Stale key files
- **WHEN** a key remains in a checkout's `.env` or elsewhere Vox no longer reads
- **THEN** Vox ignores it

### Requirement: Moving Plain-Text Keys
On start, when a keychain is available and holds no key, the system SHALL move a key found in `~/.config/vox/.env` or the `[api]` section of `config.toml` into the keychain, and SHALL delete the plain-text copy only after reading the key back from the keychain. It SHALL never move a key it cannot tell is the right one, SHALL never raise, and SHALL NOT open the keychain when there is nothing to move.

#### Scenario: First start after updating
- **WHEN** Vox starts with a key in `~/.config/vox/.env` and an empty keychain
- **THEN** the key is stored in the keychain, the `OPENAI_API_KEY` line is removed from the file, the file is deleted if nothing else remains, and Vox runs with the key

#### Scenario: Key in config.toml
- **WHEN** Vox starts with `openai_api_key` in `config.toml` and an empty keychain
- **THEN** the key moves into the keychain and the setting is removed from `config.toml` with its other contents and comments intact

#### Scenario: Keychain already holds a different key
- **WHEN** a plain-text key differs from the one already in the keychain
- **THEN** the keychain key is used, the plain-text copy is left in place, and a warning names the file

#### Scenario: Keychain already holds the same key
- **WHEN** a plain-text key matches the one in the keychain
- **THEN** the plain-text copy is deleted

#### Scenario: Several keys in .env
- **WHEN** `~/.config/vox/.env` holds more than one different `OPENAI_API_KEY` value
- **THEN** none of them is moved, the file is left unchanged, and a warning names the file and asks the user to save the right key on the Transcription page of Settings

#### Scenario: Symlinked .env
- **WHEN** `~/.config/vox/.env` is a symlink, for example into a dotfiles repository
- **THEN** the key line is removed from the file the link points to, or that file is deleted when nothing else remains; the link is never replaced by a regular file, and the log names the real file

#### Scenario: No keychain, key in config.toml
- **WHEN** no keychain is available and `config.toml` holds `[api] openai_api_key`
- **THEN** Vox leaves the setting in place and logs a warning that it is not read and stays in plain text

#### Scenario: Nothing to move
- **WHEN** Vox starts and neither `~/.config/vox/.env` nor `config.toml` holds a plain-text key
- **THEN** the move does not touch the keychain, so a locked keyring is not asked to unlock for it

#### Scenario: Settings file that does not load
- **WHEN** `config.toml` cannot be parsed and `~/.config/vox/.env` holds a key
- **THEN** each source is checked on its own: the `.env` key still moves, and a warning says `config.toml` could not be checked
