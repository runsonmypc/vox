## Why

The hotkey window offers the eight modifiers, AltGr on Linux and F1 to F20. Each of them either does something of its own (a Super tap opens the GNOME overview, an Alt tap shows a menu bar, F5 reloads a page) or is missing from many keyboards (F13 to F20). The owner decided to also offer keys that clash with nothing, tapped on their own: Pause and Scroll Lock on Linux, which PC keyboards have and almost nothing uses, and on macOS the fn (Globe) key, which every Mac keyboard has.

## What Changes

- Linux: Pause and Scroll Lock can be the tap-alone hotkey, named `pause` and `scroll_lock` as pynput's xorg backend names them. The GTK window records them from their keysyms (`Pause`, `Scroll_Lock`) and labels them Pause and Scroll Lock. A Pause tap counts however short it is, because PS/2 keyboards send its press and release together. They are not offered on macOS, where Mac keyboards have neither (a PC keyboard on a Mac sends them as F15 and F14, which are already allowed).
- macOS: fn (Globe, key code 63) can be the tap-alone hotkey, named `fn`, with `globe` accepted as another spelling. pynput has no key and no modifier flag for fn, so its darwin listener reports fn going down and coming up both as a release of key code 63. The listener tells them apart by the fn flag (`kCGEventFlagMaskSecondaryFn`) in `CGEventSourceFlagsState` for the HID system state, read at each fn event, in a way that a missed event cannot leave fn counted as down. When fn is not the hotkey the listener leaves its events alone, as before, so holding fn for a function key on a Mac laptop never counts as another key. The event tap stays listen-only.
- The macOS window records fn tapped on its own as fn, labelled "fn (Globe)". fn held with another key records only that other key, because Mac laptops need fn for the function keys, and fn is never part of a combination. Choosing fn shows an orange warning to set System Settings > Keyboard > "Press 🌐 key to" (or "Press fn key to") to "Do Nothing", and to change a Dictation shortcut that presses 🌐 or fn twice, since macOS acts on the key too.
- None of the new keys can be part of a key combination. A combination pressed while the tap-alone key is held, such as Control+fn+F5 with fn as the hotkey, no longer also counts as a tap of that key.
- The refusal that lists the allowed keys, the README (Set Hotkey… and the `[hotkey] key` setting), `config.example.toml`'s `[hotkey]` comment and the CHANGELOG's 1.0.0 entry name the new keys.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dictation-pipeline`: Hotkey Names accepts `fn` and `globe` on macOS and `pause`, `scroll_lock` and "Scroll Lock" on Linux, and says how the listener tells an fn press from a release, why a missed fn event cannot leave fn down, that fn held for a function key changes nothing when fn is not the hotkey, that a combination pressed while fn is held is not also an fn tap, and that a Pause tap counts however short it is.
- `desktop-frontend`: Hotkey Selection from Tray lists the new keys among those the window accepts, replaces "the window ignores fn" with what the macOS window records for fn, keeps fn, Pause and Scroll Lock out of combinations, and adds the fn warning.

## Impact

- Code: `vox/hotkey.py` (the name `fn`, the alias `globe`, telling an fn press from a release, a combination while the tap-alone key is held, and no minimum hold for Pause), `vox/ui/hotkey_model.py` (key tables, labels, the allowed keys, `Capture`'s rule for fn, the fn warning, the refusal text) and `vox/ui/mac/hotkey.py` (fn's flag in the flags-changed handling, instead of ignoring key code 63). The GTK window needs no code change: it reads `LINUX_KEYS`.
- No new dependency: pyobjc's Quartz is already installed on macOS for pynput.
- Tests: `tests/test_hotkey.py` and `tests/test_hotkey_window.py`.
- Docs: README, `config.example.toml`, CHANGELOG.
