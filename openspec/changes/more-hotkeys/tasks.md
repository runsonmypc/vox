The work is on branch `vt/keys`, which starts from `vt/sounds-final` (8be7d5a). Every test named here exists on that branch. Task 5.5 is left open: it needs a person at a real Mac and at the Linux PC's own keyboard, which agents must not use.

## 1. Listener

- [x] 1.1 Name `KeyCode(vk=63)` `fn` on macOS, and read `globe` as `fn` in `resolve_key`; verified by `test_globe_is_another_name_for_fn` and `test_mac_key_codes_name_the_keys_the_listener_reports` (macOS)
- [x] 1.2 At each fn event, which pynput reports as a release either way, read `kCGEventFlagMaskSecondaryFn` from `CGEventSourceFlagsState(kCGEventSourceStateHIDSystemState)` and treat the event as a press when it is set; keep the event tap listen-only (no `darwin_intercept`, no `suppress`). Checked that the function and both constants exist in the installed pyobjc Quartz (12.2.2: `kCGEventFlagMaskSecondaryFn` is 0x800000, `kCGEventSourceStateHIDSystemState` is 1). Verified, with the Quartz state mocked, by `test_fn_tapped_alone_toggles_and_double_tapped_cancels`, `test_fn_held_for_another_key_does_not_toggle` and `test_fn_held_for_a_function_key_keeps_a_combination_working` (macOS)
- [x] 1.3 Make sure no missed event leaves fn down: fn down while counted as down starts a new press instead of being dropped as a repeat, and a release with no press fires nothing; verified by `test_a_missed_fn_event_cannot_leave_fn_down` (macOS)
- [x] 1.4 Pause and Scroll Lock, which pynput's xorg backend already names `pause` and `scroll_lock`, toggle and cancel like other tap-alone keys; verified by `test_pause_and_scroll_lock_toggle_when_tapped` (Linux)

## 2. Model

- [x] 2.1 Map `Pause` and `Scroll_Lock` in `LINUX_KEYS` and key code 63 in `MAC_KEYS`, label them Pause, Scroll Lock and "fn (Globe)", and accept them as the tap-alone key on their own platform only; verified by `test_linux_key_names_name_the_keys_the_listener_reports` (Linux), `test_recorded_names_read_back_unchanged` and `test_fn_on_macos_and_pause_and_scroll_lock_on_linux_can_be_the_hotkey`
- [x] 2.2 In `Capture`, record fn only when it is tapped on its own, and record only the other key when fn is held with one; verified by `test_fn_tapped_alone_is_recorded_as_fn` and `test_fn_held_with_another_key_records_only_that_key`
- [x] 2.3 Keep fn, Pause and Scroll Lock out of combinations; verified by `test_a_combination_needs_a_modifier_and_space_or_a_function_key`
- [x] 2.4 Warn when fn is chosen, say so for no other new key, and list the new keys in the refusal for typing keys (`TYPING_KEY`); verified by `test_fn_warns_that_macos_acts_on_it_too_and_pause_and_scroll_lock_do_not`

## 3. Windows

- [x] 3.1 macOS: read fn's state from `NSEventModifierFlagFunction` in flags-changed events instead of ignoring key code 63; verified by `test_mac_records_fn_tapped_on_its_own_and_warns`, `test_mac_records_the_key_pressed_while_fn_is_held` and `test_mac_keeps_fn_out_of_combinations`
- [x] 3.2 Linux: the GTK window records Pause and Scroll Lock through `LINUX_KEYS`, with no code change of its own; verified by `test_gtk_records_pause_and_scroll_lock_tapped_on_their_own` (Linux, under Xvfb)

## 4. Docs and specs

- [x] 4.1 List the new keys in the README (Set Hotkey… and the `[hotkey] key` setting, with what to change in System Settings for fn), in `config.example.toml`'s `[hotkey]` comment, and in one line of the CHANGELOG's 1.0.0 entry
- [x] 4.2 Write the delta specs for `dictation-pipeline` (Hotkey Names) and `desktop-frontend` (Hotkey Selection from Tray); verified by `openspec validate more-hotkeys --strict` and `openspec validate --all --strict`. They are not synced into `openspec/specs` yet: the unarchived `hotkey-window` change also modifies Hotkey Names, and with the new scenarios in the main spec its MODIFIED block would drop them, which `openspec validate --all --strict` refuses. A trial archive in a scratch copy, `hotkey-window` first and then `more-hotkeys`, updated both requirements and left every spec valid, so `hotkey-window` must be archived first

## 5. Verification

- [x] 5.1 `uv run --frozen pytest -q -p no:cacheprovider` passes on macOS (1046 passed, 43 skipped; 1030 passed and 40 skipped before this change), and so does `uv run --frozen ruff check vox tests`. Each of twelve mutations of the new logic (among them: dropping the new-press rule for fn, never reading the fn flag, letting fn join a capture held with Control, and removing fn from the macOS window's flags) makes at least one of the new tests fail
- [x] 5.2 The Linux suite, with the GTK window tests, passes under Xvfb on the Linux test PC: 968 passed and 83 skipped without GTK (956 and 77 before), and 38 GTK tests passed (37 before)
- [x] 5.3 On the Linux PC, on a private Xvfb display where `xmodmap -pke` gives keycode 127 = Pause and 78 = Scroll_Lock, a real `HotkeyListener` fed raw XTEST taps: key `pause` fires one toggle for a Pause tap and nothing for a Scroll Lock tap; key `scroll_lock` (and "Scroll Lock") fires one toggle for a Scroll Lock tap and nothing for a Pause tap; a double-tap of either fires toggle, then cancel (7 of 7 checks)
- [x] 5.4 On the same kind of private display, the real GTK hotkey window, clicked and fed an XTEST tap of keycode 127, then 78, shows Pause, then Scroll Lock, with no warning, and Save writes `key = "pause"`, then `key = "scroll_lock"`, to a scratch `config.toml`, and closes with status 0
- [ ] 5.5 A person checks what only real keyboards show. On a Mac: with fn as the hotkey, tapping fn (Globe) starts and stops dictation and a double-tap cancels; fn+F5 or fn with an arrow key does not; the Set Hotkey window records fn tapped alone as "fn (Globe)" with the warning, records F5 for fn+F5 on a laptop, and leaves fn out of Control+fn+Space. On the Linux PC's own keyboard: a Pause tap and a Scroll Lock tap toggle, which also shows whether the keyboard sends Pause's release only when the key comes up (design.md, Risks)
