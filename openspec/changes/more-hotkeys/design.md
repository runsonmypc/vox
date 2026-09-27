## Context

See proposal.md for why. The change is on branch `vt/keys`, which starts from `vt/sounds-final`, with its review fixes (D4, D7, D8 and the wording in D6) on `vt/keys-final`, and builds on the hotkey window of the `hotkey-window` change.

What the pieces do today:
- The listener (`vox/hotkey.py`, pynput) fires "toggle" when the tap-alone key is released, if no other key went down while it was held and it was held for at least 30 ms; a second tap within `double_tap_timeout_ms` fires "cancel". A second press of the key while it is counted as down is dropped as an auto-repeat. Keys are named by `_key_name`, and configured names go through `resolve_key` and `_ALIASES`.
- pynput's xorg backend has `Key.pause` and `Key.scroll_lock` (keysyms `Pause` 0xFF13 and `Scroll_Lock` 0xFF14), so the listener already names them `pause` and `scroll_lock`. pynput's darwin backend has neither, and no `Key` for fn either.
- pynput's darwin listener handles every flags-changed event by looking the key up in `_MODIFIER_FLAGS`, which has no entry for fn. For fn (key code 63) the lookup gives 0, so fn going down and coming up both reach `on_release` as `KeyCode(vk=63)`. The callback gets the key only, not the Quartz event.
- The hotkey windows record keys through `Capture` and check them with `HotkeyModel.record_key` and `record_combination` against `TAP_KEYS` and `COMBINATION_KEYS`. The macOS window ignored flags-changed events for key code 63, so fn+F5 recorded F5.

## Goals / Non-Goals

**Goals:**
- Pause and Scroll Lock on Linux, and fn (Globe) on macOS, as tap-alone hotkeys, recorded by the window and fired by the listener.
- Holding fn for a function key on a Mac laptop keeps working in the window and in the listener.
- No event, lost or late, can leave fn counted as down.

**Non-Goals:**
- The new keys in combinations. They are tapped alone.
- Pause and Scroll Lock on macOS, where Mac keyboards have neither; a PC keyboard on a Mac sends them as F15 and F14, which are already allowed.
- fn on Linux, where the fn key is usually handled inside the keyboard and never reaches X.
- Changing the macOS settings for the Globe key from Vox. The window says what to change.

## Decisions

**D1. Tap-alone only, and per platform.** `pause` and `scroll_lock` are in `TAP_KEYS` on Linux, and `fn` on macOS, through `_TAP_ONLY_LABELS`, which also labels them (Pause, Scroll Lock, "fn (Globe)"). None is in `MODIFIERS` or `COMBINATION_KEYS`, so `record_combination` refuses them. `LINUX_KEYS` maps the keysyms `Pause` and `Scroll_Lock`, and `MAC_KEYS` maps key code 63 to `fn`; the existing tests that check both tables against pynput's names cover the new rows.

**D2. fn is a press or a release by the HID system's fn flag, read at each fn event.** `_key_name` names `KeyCode(vk=63)` `fn` on macOS, and `resolve_key` reads `globe` as `fn`. When an fn event arrives and fn is the hotkey (D4), `_on_release` asks `CGEventSourceFlagsState(kCGEventSourceStateHIDSystemState)` whether `kCGEventFlagMaskSecondaryFn` (0x800000) is set. Set, the event is handed to `_on_press`; clear, it is a release. The function and both constants exist in the installed pyobjc (12.2.2), which pynput already needs on macOS. The HID system state is the state of the keyboards themselves.
- Rejected: pynput's `darwin_intercept` or `suppress=True`, which would hand the callback the event. Either turns the listen-only event tap into an active filter that every keystroke on the Mac waits for: typing lags system-wide, and macOS disables a tap that is slow to answer.
- Rejected: overriding pynput's `Listener._handle_message` to read the event's own flags. It is exact, but it is a private method pynput may change in any release.
- Rejected: flipping a remembered fn state at each event. One missed event would invert it for good.
- Rejected: `kCGEventSourceStateCombinedSessionState`, which also counts the events that apps post.

**D3. A missed or late fn event cannot leave fn down.** Three things make this so. The state is read, never flipped, so each fn event brings the listener back to the keyboard's real state. A release while fn is not counted as down fires nothing (the tap-alone check needs a press first). And fn never auto-repeats, so fn down again while it is counted as down means its release was missed: `_on_press` starts a new press instead of dropping it as a repeat, which it still does for every other key. So a missed release costs nothing, and a missed press loses only that tap. If the callback runs so late that the user has already let go, the press reads as a release and that tap is lost, but nothing stays down; taps shorter than 30 ms are ignored anyway.

**D4. fn when it is not the hotkey is left alone.** The listener reads fn's state only when fn is the hotkey; otherwise fn's events do nothing, as before fn could be chosen. Mac laptops hold fn for the function keys, so fn must not count as another key: that would restart the double-tap timing whenever fn goes down, and pressing fn+F5 (hotkey `f5`) or Control+fn+F5 (combination `ctrl+f5`) twice quickly, letting go of fn between the two, would toggle twice instead of cancelling. A combination fires when its keys are a subset of the keys down, so `ctrl+f5` still fires with fn held.
- Rejected: passing fn's presses to `_on_press` like any other modifier's. The first version did, and it broke that double-tap.

**D5. The macOS window reads fn from its own event, and `Capture` keeps fn to itself.** Flags-changed events carry their own flags, so the window takes fn's state from `NSEventModifierFlagFunction`, added to `_DOWN` next to the device bits of the other modifiers, instead of ignoring key code 63. `Capture` never puts fn in `held` or `pressed`. It remembers whether fn went down with nothing else held and nothing else has gone down since (`fn_alone`); releasing fn then completes `("fn",)`, and otherwise nothing. So fn tapped alone records fn, fn held with F5 records F5, fn held with Right Command records Right Command, and fn held during a combination is left out of it; fn tapped alone in the Key combination field is refused like any single key. The GTK window needs no change: it looks keys up in `LINUX_KEYS`.

**D6. A warning for fn; none for Pause and Scroll Lock.** macOS acts on the Globe key too: depending on "Press 🌐 key to" it changes the input source, shows the emoji picker or starts dictation, and a Dictation shortcut can be "press 🌐 twice", which a double-tap to cancel would also trigger. Choosing fn shows an orange warning saying to set "Press 🌐 key to" to "Do Nothing" and to change such a Dictation shortcut; saving is still allowed. On a keyboard with an fn key and no Globe key, System Settings calls these "Press fn key to" and "Press Fn (Function) Key Twice" (macOS 15's own strings), so the warning and the README name both. Pause and Scroll Lock get no warning: nothing common uses them.

**D7. A combination pressed while the tap-alone key is held is not also a tap of that key.** When a combination fires while the tap-alone key is down, the listener counts that key as held with another key, as it already did for keys pressed after it. Otherwise, with fn the hotkey and `ctrl+f5` the combination, pressing Control, then fn (which a laptop needs for F5), then F5 fires the combination and then a second toggle when fn comes up. The rule holds for every tap-alone key; fn is just the one where that order comes naturally.

**D8. A Pause tap counts however short it is.** PS/2 keyboards, which include most laptops' built-in ones, send Pause's make and break codes together when the key goes down and nothing when it comes up, so Linux reports the press and release at once and the tap is held about 0 ms. The listener's 30 ms minimum hold, which filters out phantom events, would drop every such tap, so for Pause the minimum hold is 0; USB keyboards report Pause like any other key and are unaffected. Every other key keeps the 30 ms.
- Rejected: dropping Pause and keeping only Scroll Lock. The owner asked for both, and nothing sends phantom Pause events.

## Risks / Trade-offs

- [Not tried on a real Mac] The fn logic is tested with the Quartz state mocked, and the window with synthetic NSEvents; no synthetic key events may be posted on the owner's Mac. → Task 5.5: a person taps fn on a real Mac.
- [Pause on PS/2 keyboards] A Pause tap from a PS/2 keyboard is held about 0 ms. → Pause has no minimum hold (D8), covered by a unit test and by an XTEST tap whose press and release arrive together. The Linux PC's own keyboard is USB (a Logitech G213), so task 5.5 on it cannot show the PS/2 case; a laptop's built-in keyboard can.
- [The system acts on fn] A user who ignores the warning gets the emoji picker, an input source change or macOS dictation with every tap. → The warning, and the README, say what to change.
- [Keyboards without fn] Most non-Apple keyboards handle their Fn key inside the keyboard, so macOS never sees it and it cannot be recorded. → Other keys remain.
