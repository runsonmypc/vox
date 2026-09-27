## Context

See proposal.md for why the window exists. The change builds on branch `vt/rename` (the app is called Vox Transfer in everything users see) and is done on branch `vt/hotkey`.

What the hotkey listener (`vox/hotkey.py`, pynput) does today, which the window has to match:
- It handles one key tapped on its own (`[hotkey] key`) and at most one combination (`[hotkey] fallback`). The key fires "toggle" when it is released, if no other key was pressed while it was down and it was held for at least 30 ms. A second tap within `double_tap_timeout_ms` fires "cancel".
- The combination fires when it is pressed, as soon as its names are a subset of the keys down. Pressing it twice quickly fires "cancel". The tap-alone key never counts towards a combination, so a combination that includes the hotkey never fires. The tap-alone key is always active next to the combination.
- Keys are named with pynput's names, mapped through `_KEY_NAMES` and `_ALIASES`: `right_shift`, `shift` (the left key), `right_ctrl`, `ctrl`, `right_alt`, `alt`, `cmd_r`, `cmd`, `space`, `f1` to `f20`. On macOS, Caps Lock reports a press and release at once, so it never passes the 30 ms hold, and fn (key code 63) only ever reports a release. On X11, Right Alt on AltGr layouts (`ISO_Level3_Shift`) has no pynput `Key`, so the listener names it `vk_65027`. X11 also reports the Alt keys as `Meta_L` and `Meta_R` while Shift is held; the listener and the window name those `alt` and `right_alt`, so an Alt let go after Shift is matched to its press and never stays in the combination state.
- pynput never suppresses keys: the app in front receives the hotkey and the combination too.
- Before this change the config reloader copied every setting except `[hotkey]`, so a hand edit of `[hotkey]` was silently ignored until a restart.

## Goals / Non-Goals

**Goals:**
- A user picks the hotkey, and an optional combination, by pressing them, and the window saves only what the listener can detect.
- A saved hotkey works as soon as the window closes, with no restart, and so does a hand edit of `[hotkey]`.
- The current hotkey cannot start dictation while the window records keys, and the hotkey always works again once the window is gone.

**Non-Goals:**
- `double_tap_timeout_ms` in the window. It stays a file setting.
- Letters or digits in a combination recorded by the window. A hand-written combination with letters keeps working.
- A combination-only hotkey, with no tap-alone key.
- Suppressing keys, so that the app in front would not receive them.
- Wayland.
- Detecting shortcuts the OS reserves (Cmd+Space, Ctrl+Space, Super+Space, Ctrl+Alt+F1 to F12). The OS takes them before the window sees them, so they cannot be recorded anyway.

## Decisions

**D1. Two fields: the tap-alone key and an optional combination.** The key goes to `[hotkey] key`, the combination to `[hotkey] fallback`, as the listener already reads them. Showing both makes it visible that the tap-alone key stays active next to a combination.
- Rejected: storing a combination in `key`. The listener compares one name there, so this would need a listener change and a way to have no tap key at all.
- Rejected: one field that sends whatever was pressed to `key` or `fallback`. It is surprising, and it hides that right Shift stays active.

**D2. Only keys that don't fire while typing.** The tap-alone key may be any of the eight modifiers, AltGr on Linux, or F1 to F20. A combination needs a modifier other than Shift and ends with Space or F1 to F20.
- Rejected: letters and digits in combinations. pynput reports the character the modifiers produce (Option+D gives "∂" on macOS, and AltGr changes the character on Linux), and on macOS with Control held it uses fixed US key positions, so a recorded letter combination could silently never fire. Keys are not suppressed either, so Cmd+D would also bookmark the page in a browser.
- Rejected: Shift alone with Space, which happens while typing.

**D3. Tables from the toolkits' key codes to the listener's names.** `MAC_KEYS` maps NSEvent key codes and `LINUX_KEYS` maps GTK key value names (X keysyms). Tests check both against pynput's own `Key` values and the listener's `_key_name`, so the window can never save a name the listener does not report. On macOS, left and right modifiers are told apart by the device-dependent bits of flags-changed events (`NX_DEVICE*KEYMASK` in `IOLLEvent.h`). fn is ignored, so Fn+F5 records F5. A key the window does not know is passed on as unknown and refused. A modifier already down when recording started is ignored, and after a refused try the window starts a fresh capture, so keys still held can never complete a later "single key".

**D4. A suspend flag in the daemon, set by the tray around the window's process.** The tray sends `hotkey:suspend` before it starts the window and `hotkey:resume` when the process ends: after Save, Cancel, a crash, `kill -9` or Force Quit, since `_call_after_exit` waits on the process. A launch that fails sends the resume at once. Both go through `call_soon_threadsafe`, and the suspend is queued before the process exists, so the order is always suspend, then resume. A second click while the window is open sends suspend again, which is idempotent, and brings the open window forward.
- Rejected: stopping the listener while the window is open. The flag survives listener swaps made meanwhile (the reloader may pick up Save before the process exits) and needs no restart just to suspend.
- Rejected: the window talking to the daemon. Windows stay separate processes that know nothing of the daemon, as before.

**D5. Live apply by replacing the listener.** A pynput listener cannot restart, so `_apply_hotkey` stops the old one and starts a new one with the new settings, only when `key`, `fallback` or `double_tap_timeout_ms` changed. It runs from two places: `hotkey:resume` re-reads the file at once, so the old key is never live for up to 2 s after Save, and the reloader passes every version that loads, so hand edits apply on the next poll. When both run, the second finds no change. All of this runs on the event-loop thread. A listener copies its settings when it is built, and its pynput thread only posts to the queue. A new listener starts with no half-seen tap or double-tap, and a key held across the swap only produces a release, which does nothing.
- A hand edit during a recording swaps the listener at once, and the new key stops the recording. Rejected: deferring the swap until idle, which adds a pending state for a rare case. The recording limit still applies.

**D6. "Set Hotkey…" only while idle.** The menu item is disabled while Vox records or processes. A recording that starts in the race between the click and the suspend is cancelled, as Pause does.

**D7. The write reuses the config writer.** `update_hotkey` sets `key`, and sets `fallback` or removes it when empty, through `_read_document`, `_edited_table` and `_write_document`: an atomic write that keeps the file's mode and comments, a new file created 0600, no unparseable result ever written, and a `ConfigError` for a `hotkey` that is not a table. The model keeps what the file says until the user records something, so an untouched hand-written spelling such as `"Left_Ctrl+Space"` is written back as it was. When `config.toml` does not load, the window refuses to save, as the vocabulary window does.

**D8. Warnings that still allow saving.** F1 to F12: most Mac keyboards send them only with fn unless the standard-function-keys setting is on, and on Linux apps use them too (F5 reloads a page). On Linux, a left Super tap opens the GNOME and KDE overview, and an Alt tap shows some apps' menu bar. Combinations get no warning: the note under the field says the app in front receives them.

## Risks / Trade-offs

- [Mac laptop F-keys] Without the standard-function-keys setting, F1 to F12 are media keys unless fn is held. → The window warns in orange when one of them is the hotkey.
- [`XF86*` keysyms] Some Linux keyboards send `XF86Tools` and similar for F13 and up, which the window does not know and refuses. → A hand edit of `[hotkey] key` still works for any key the listener names.
- [A tap the desktop grabs] A Super or Alt tap that the desktop takes (for its overview or a menu) moves focus away, which stops recording, so such a key cannot be recorded there. → The user can pick another key, or edit the file.
- [Untested on a real display] The windows are tested with synthetic key events, headless. → A person should try the window once on macOS and on Linux before the release.
