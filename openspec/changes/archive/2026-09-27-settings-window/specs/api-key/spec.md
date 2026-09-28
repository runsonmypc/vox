## MODIFIED Requirements

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

## RENAMED Requirements

- FROM: `### Requirement: Key Entry from the Tray`
- TO: `### Requirement: Key Entry in Settings`
