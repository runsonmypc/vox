## MODIFIED Requirements

### Requirement: Key Sources
The system SHALL use the `OPENAI_API_KEY` environment variable when it is set, and otherwise the stored key. It SHALL NOT read the key from `.env` files or `config.toml`, except for the Linux plain-text fallback file when no keyring is available. A key taken from the environment SHALL be removed from Vox's own environment at startup, before Vox starts any process.

#### Scenario: Environment variable override
- **WHEN** `OPENAI_API_KEY` is set in Vox's environment
- **THEN** Vox uses it, removes it from its own environment at startup so processes Vox starts (whisper-cli, xclip, its windows and others) do not inherit it, and the key window, which learns of the override without receiving the key, says the environment variable is overriding the stored key

#### Scenario: Stale key files
- **WHEN** a key remains in a checkout's `.env` or elsewhere Vox no longer reads
- **THEN** Vox ignores it

### Requirement: Moving Plain-Text Keys
On start, when a keychain is available and holds no key, the system SHALL move a key found in `~/.config/vox/.env` or the `[api]` section of `config.toml` into the keychain, and SHALL delete the plain-text copy only after reading the key back from the keychain. It SHALL never move a key it cannot tell is the right one, and SHALL never raise.

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
- **THEN** none of them is moved, the file is left unchanged, and a warning names the file and asks the user to save the right key with Set API Key…

#### Scenario: Symlinked .env
- **WHEN** `~/.config/vox/.env` is a symlink, for example into a dotfiles repository
- **THEN** the key line is removed from the file the link points to, or that file is deleted when nothing else remains; the link is never replaced by a regular file, and the log names the real file

#### Scenario: No keychain, key in config.toml
- **WHEN** no keychain is available and `config.toml` holds `[api] openai_api_key`
- **THEN** Vox leaves the setting in place and logs a warning that it is not read and stays in plain text
