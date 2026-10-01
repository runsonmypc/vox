## MODIFIED Requirements

### Requirement: General Settings
The General page of Settings SHALL let the user choose the microphone, the recording limit, whether sounds play, whether and how far Vox lowers the system volume while recording, whether screen hints are used, and whether the recording overlay is shown.

#### Scenario: Sounds
- **WHEN** the user turns Sounds off
- **THEN** `[sounds] enabled = false` is saved, and no sound plays after the window closes

#### Scenario: Lowering the volume
- **WHEN** the user turns "Lower other audio while recording" on and sets the slider to 30%
- **THEN** `[attenuation] enabled = true` and `level = 0.3` are saved, the slider goes from 0% to 100% in steps of 5%, and it is disabled while lowering is off

#### Scenario: Slider saves once
- **WHEN** the user drags the volume slider
- **THEN** the value is saved when the slider is released, not for every step it passes

#### Scenario: Screen hints
- **WHEN** the user turns Screen hints off
- **THEN** `[context] screen = false` is saved, and the page links to what screen hints send (the Privacy documentation)

#### Scenario: A level from the file that is not a step
- **WHEN** `config.toml` sets `[attenuation] level = 0.33`
- **THEN** the slider shows 33%, and the value in the file is kept until the user moves the slider

#### Scenario: Overlay opt in
- **WHEN** the user enables “Show recording overlay” on macOS or Linux/X11
- **THEN** `[overlay] enabled = true` is persisted through the shared settings path and applied without restarting

#### Scenario: Default off
- **WHEN** the configuration has no overlay preference
- **THEN** the setting is off and no overlay appears

#### Scenario: Save failure
- **WHEN** saving the preference fails or the settings file is invalid
- **THEN** Settings reports its existing error and keeps the control consistent with persisted configuration

#### Scenario: Linux availability
- **WHEN** the user opens General Settings on Linux/X11
- **THEN** the overlay control is available and persists the same shared preference

#### Scenario: Unsupported Linux session
- **WHEN** General Settings runs on a non-X11 Linux display
- **THEN** the overlay control is unavailable with a short X11 availability note
