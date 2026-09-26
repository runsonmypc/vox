# api-key Specification

## Purpose
Keeps the user's OpenAI API key encrypted by the operating system and lets them set, replace and remove it from the tray, without a text editor or the command line.

## Requirements

### Requirement: Encrypted Key Storage
The system SHALL store the OpenAI API key in the operating system's keychain: the macOS Keychain, or the Secret Service on Linux. The key SHALL NOT be written to disk in plain text while a keychain is available, and SHALL NOT appear in logs, command-line arguments, or the environment of processes Vox starts.

#### Scenario: Saving on macOS
- **WHEN** the user saves a key on macOS
- **THEN** it is stored in the login keychain under "Vox" and no plain-text copy is written

#### Scenario: Saving on Linux with a keyring
- **WHEN** the user saves a key on a Linux desktop running a Secret Service provider such as GNOME Keyring or KWallet
- **THEN** it is stored in that keyring and no plain-text copy is written

#### Scenario: Linux without a keyring
- **WHEN** the user saves a key on Linux and no Secret Service provider is available
- **THEN** the key is saved to `~/.config/vox/.env` readable only by the user, and the key window warns beforehand that it will not be encrypted

#### Scenario: Keyring locked or refusing
- **WHEN** a keychain is available but locked, or refuses access
- **THEN** the key is not saved anywhere else and the window shows the error

### Requirement: Key Entry from the Tray
The system SHALL provide a "Set API Key…" tray item that opens a key window. The window SHALL mask the key unless the user turns on Show, SHALL say where the key will be stored, and SHALL show only the last four characters of a saved key.

#### Scenario: Setting a key
- **WHEN** the user pastes a key and clicks Save
- **THEN** the key is stored, the window closes, and the next dictation uses it without restarting Vox

#### Scenario: Replacing a key
- **WHEN** a key is already saved and the user saves a different one
- **THEN** the new key replaces the old one and takes effect without restarting Vox

#### Scenario: Removing a key
- **WHEN** the user clicks Remove and confirms
- **THEN** the stored key is deleted and Vox behaves as if no key was set

#### Scenario: Checking the key before saving
- **WHEN** the user saves with "Check with OpenAI" turned on and OpenAI rejects the key
- **THEN** the key is not saved and the window says OpenAI rejected it

#### Scenario: OpenAI unreachable during the check
- **WHEN** the check cannot reach OpenAI
- **THEN** the window says so and lets the user save the key anyway

### Requirement: Running Without a Key
When the configured transcription mode calls OpenAI and no key is available, the system SHALL keep running instead of exiting, so the service manager does not restart it.

#### Scenario: Starting without a key
- **WHEN** Vox starts with a tray and no key
- **THEN** the tray says "API key needed" and the key window opens

#### Scenario: Hotkey without a key
- **WHEN** the user presses the hotkey and no key is set
- **THEN** no recording starts, the error sound plays, and the key window opens

#### Scenario: Starting without a key or a tray
- **WHEN** Vox starts with no key and no tray (for example over SSH)
- **THEN** it logs how to set the key and exits with an error, as before

#### Scenario: Local transcription
- **WHEN** the configured transcription mode does not call OpenAI
- **THEN** Vox runs normally without a key and does not open the key window

### Requirement: Key Sources
The system SHALL use the `OPENAI_API_KEY` environment variable when it is set, and otherwise the stored key. It SHALL NOT read the key from `.env` files or `config.toml`, except for the Linux plain-text fallback file when no keyring is available.

#### Scenario: Environment variable override
- **WHEN** `OPENAI_API_KEY` is set in Vox's environment
- **THEN** Vox uses it, and the key window says the environment variable is overriding the stored key

#### Scenario: Stale key files
- **WHEN** a key remains in a checkout's `.env` or elsewhere Vox no longer reads
- **THEN** Vox ignores it

### Requirement: Moving Plain-Text Keys
On start, when a keychain is available and holds no key, the system SHALL move a key found in `~/.config/vox/.env` or the `[api]` section of `config.toml` into the keychain, and SHALL delete the plain-text copy only after reading the key back from the keychain.

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
