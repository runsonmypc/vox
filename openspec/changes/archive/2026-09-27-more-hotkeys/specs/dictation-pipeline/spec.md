## MODIFIED Requirements

### Requirement: Hotkey Names
The system SHALL accept the hotkey names users naturally write and SHALL match them to the key that was actually pressed. Without a usable keyboard backend it SHALL exit with a clear message.

#### Scenario: Spellings
- **WHEN** `[hotkey] key` is `right_shift`, `"Right Shift"`, `right_ctrl`, `right_alt`, `left_shift`, `left_ctrl` or `left_alt`, `fn` or `globe` on macOS, or `pause`, `scroll_lock` or `"Scroll Lock"` on Linux, or `fallback` is a combination such as `"left_ctrl+space"`
- **THEN** each names the intended key

#### Scenario: Left and right modifiers
- **WHEN** the hotkey is `left_shift`
- **THEN** it fires on the left Shift key and not on the right one

#### Scenario: Alt with Shift held on Linux
- **WHEN** an Alt key is pressed or released while Shift is held, which X11 reports as Meta
- **THEN** it is still named `alt` or `right_alt`, so `alt+shift+space` fires whichever of Alt and Shift goes down first, and an Alt let go after Shift never stays counted as held

#### Scenario: Pause and Scroll Lock on Linux
- **WHEN** the hotkey is `pause` or `scroll_lock` and the user taps that key on its own
- **THEN** it starts or stops dictation, and a double-tap cancels, like any other tap-alone key
- **AND** a Pause tap counts however short it is, because PS/2 keyboards send its press and release together, while every other key still has to be held at least 30 ms

#### Scenario: fn on macOS
- **WHEN** the hotkey is `fn` and the user taps fn (Globe) on its own
- **THEN** it starts or stops dictation, and a double-tap cancels, like any other tap-alone key, and fn held while another key is pressed, such as fn+F5, does not
- **AND** Vox tells an fn press from a release by the fn flag in the HID system's modifier state, read at each fn event, because pynput reports both as a release; its event tap stays listen-only, so no keystroke waits for Vox
- **AND** a combination pressed while fn is held, such as Control, then fn, then F5 for `ctrl+f5`, fires once and is not also an fn tap

#### Scenario: fn held for a function key
- **WHEN** fn is not the hotkey and a Mac laptop needs fn held for a function key, such as fn+F5 for the hotkey `f5` or Control+fn+F5 for the combination `ctrl+f5`
- **THEN** Vox leaves fn alone, so it does not count as another key, and pressing that key twice quickly cancels even when fn is let go between the two presses

#### Scenario: A missed fn event
- **WHEN** an fn press or release never reaches Vox
- **THEN** fn is not left counted as down: a release with no press fires nothing, and fn going down while Vox counts it as down starts a new press, so the next tap works

#### Scenario: No keyboard backend
- **WHEN** global hotkeys cannot be set up, for example with no X11 display on Linux
- **THEN** Vox logs that global hotkeys are unavailable, with the reason in a few words (such as "no X display: DISPLAY is not set"), and that it needs an X11 display, and exits with status 1 instead of a traceback
