## MODIFIED Requirements

### Requirement: Quitting on a Stop Signal
The system SHALL quit on SIGTERM, which a service stop or restart, a logout, `install.sh` updating Vox and the `.deb`'s upgrade or removal all send, the same way it quits from the tray's Quit Vox Transfer: on macOS and Linux, with or without a tray, a lowered output volume SHALL be restored and the microphone released.

#### Scenario: Service stop during a recording
- **WHEN** Vox receives SIGTERM while it records with the output volume lowered, even while the volume is still being lowered
- **THEN** the recording is not transcribed, the output volume returns to its level before the recording, and Vox exits through the same path as Quit Vox Transfer

#### Scenario: Second SIGTERM
- **WHEN** a second SIGTERM arrives while Vox is shutting down, or after the tray's event loop has ended
- **THEN** Vox ends at once
