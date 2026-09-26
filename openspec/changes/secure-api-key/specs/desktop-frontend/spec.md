## MODIFIED Requirements

### Requirement: Self-Contained Per-User Installation
The system SHALL provide one install script for macOS and Linux that installs Vox and its tray support for the current user, without keeping the source checkout and without a compiler. The installer SHALL NOT ask for, copy, or store the OpenAI API key.

#### Scenario: Fresh install
- **WHEN** the user runs `./install.sh`
- **THEN** Vox is installed into its own virtualenv with a `vox` command, missing system packages are installed, Vox starts at login whether or not an OpenAI API key is set, and a Vox launcher is added to the system's applications

#### Scenario: Start after Quit
- **WHEN** the user has quit Vox from the tray and opens the Vox launcher (the Applications folder or Spotlight on macOS, the applications list on Linux)
- **THEN** Vox starts again through its login service with the same permissions, and opening the launcher while Vox is running does nothing

#### Scenario: Already running
- **WHEN** Vox is started while another instance is running
- **THEN** it exits successfully before any permission prompt, so the service manager does not retry it

#### Scenario: GNOME tray host
- **WHEN** the installer runs on GNOME and no tray host is present
- **THEN** it installs and enables the AppIndicator extension through GNOME's own confirmation dialog, without sudo or logging out

#### Scenario: Update
- **WHEN** the user runs `./install.sh` again from a newer checkout
- **THEN** the virtualenv is rebuilt from that checkout, existing configuration and history are kept, and the running service is restarted
