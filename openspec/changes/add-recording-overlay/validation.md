# Recording overlay validation

Date: 2026-09-30. Change: `add-recording-overlay` (spec-driven).

## Delivered behavior and enablement

The overlay is **off by default**. On macOS or Linux/X11, enable **Settings → General → Show recording
overlay**, or use `[overlay] enabled = true` in `config.toml`. Enabling during an operation waits
for the next recording; disabling applies through the existing poll/Settings-close path and
releases the panel, meter, timer and observers. Native Wayland and headless operation use a no-op backend.

The real recorder callback supplies a latest-only normalized RMS value. The main-thread native
panel displays Listening, cloud processing, or captured local processing, with generation/revision
checks, coalesced delivery, reduced motion, and a 190 ms empty/no-speech fade (X11 opacity needs a compositor). Text delivery hides the panel before paste with a bounded native acknowledgment. Cancellation and failures
dismiss immediately. Initial and streaming-fallback screenshots share the acknowledged hide guard.
History retries cannot show the panel through their PROCESSING state.

## Automated checks

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q` (macOS GUI, local sockets allowed) | **1,269 passed, 61 skipped**, 35.24 s |
| Final focused audio, overlay, daemon, config, window and docs checks | **205 passed** after nonfinite-level hardening; native notification/cleanup checks also passed in the preceding **207-test** focused GUI run |
| `.venv/bin/ruff check vox tests scripts/integration/overlay_desktop.py` | Passed |
| `git diff --check` | Passed |
| `scripts/lock.sh --check` | Passed; both lockfiles agree and wheel checks passed |
| `shellcheck install.sh scripts/*.sh packaging/deb/*.sh packaging/deb/postinst packaging/deb/prerm packaging/deb/postrm` | Passed |
| `bash -n` on installer/scripts/deb shell scripts; `sh -n` on deb maintainer scripts | Passed |
| `openspec validate add-recording-overlay --strict` | Passed |

An initial sandbox run could not bind the existing loopback/Unix-socket test servers and could not
access WindowServer. Re-running with desktop/socket access resolved those environment failures.
The full-suite run also exposed tray test doubles missing the new dispatcher and capture-guard
arguments; these now model the current interfaces. No production regression remains in that run.
The skips cover unavailable platform-specific tests, primarily GTK/Linux and install environments.
Docker was initially absent from PATH; the Docker Desktop executable was subsequently found
and used for the Linux/X11 extension below. Ubuntu installer/deb end-to-end jobs remain CI coverage. A fresh Python subprocess explicitly
verified Linux and macOS-headless overlay creation without importing AppKit or the native backend.

## Performed desktop checks

Command:

```sh
.venv/bin/python scripts/integration/overlay_desktop.py \
  --output openspec/changes/add-recording-overlay/validation --microphone
```

[Machine-readable report](validation/desktop.json). This was **automated native desktop
validation**, not a human dictation session. It used two dedicated native text-target processes,
existing Accessibility/Screen Recording permissions, one physical display, production paste and
window detection, and a two-second real microphone sample. It made no provider requests and
retained no microphone audio. Original clipboard types, foreground app and pointer position were
restored by the harness.

Passed observations:

- Target focus, text insertion and clipboard restoration with overlay off, listening and processing.
- Switching target applications changes paste destination without moving the overlay's selected display.
- A real mouse event through the panel moves the underlying target's insertion cursor; paste lands
  at that cursor and window detection still reports the target app.
- Actual window-targeted screenshots and Vision OCR contain the synthetic context marker and no
  overlay/status text in listening and fallback-processing states. Capture asserts the native
  panel is hidden before `screencapture` starts, then observes restoration afterward.
- [Listening capture](validation/context-1.png) and [processing capture](validation/context-2.png)
  were visually inspected. Their pixel difference is confined to `(329, 57)–(334, 85)`, the target's
  blinking insertion caret. Neither includes overlay pixels. This is not a claim of pixel equality.
- Real microphone callbacks drive the live overlay. The second run produced 14 sampled levels,
  ranging from 0 to approximately 0.00262 normalized RMS, consistent with a quiet room. This does
  not establish quiet/loud speech tuning.
- Reduced-motion callback produces a static panel and immediate completion; disable, re-enable
  on the next recording, and quit release the native panel/timer/observers.

### Visual and performance observations

The delivered renderer was used to rasterize and inspect all three statuses:
[Listening](validation/listening-batch.png), [Transcribing](validation/processing-batch.png),
and [Transcribing locally](validation/processing-whisper_cpp.png).
The 360 × 56 point layout fits the longest label, with the Vox icon, charcoal rounded panel,
subtle rim, brass/green layered carriers, and quiet processing amplitude. It follows the local
website reference's baseline/ruler, envelope and palette; it does not copy simulated completion
progress. The previews are at the display's 2× backing scale (720 × 112 pixels).

[Callback benchmark](validation/callback-performance.json): 2,000 synthetic 50 ms stereo blocks at
48 kHz averaged 0.00106 ms without metering and 0.00422 ms with metering. This measures callback
cost on this machine, not end-to-end latency or a cross-device performance guarantee.

Native observer notification delivery/removal, panel flags and background-thread rejection are
also exercised by `tests/test_overlay_mac.py`. The implementation follows the workspace-specific
notification center described in [Apple's accessibility notification documentation](https://developer.apple.com/documentation/appkit/nsworkspace/accessibilitydisplayoptionsdidchangenotification).

## Scenario review

Every delta scenario was reviewed against code and available evidence. Existing General behavior
continues to use the shared settings model. An independent review using `review-implementation`
found two cleanup gaps (dispatch failure after showing a panel, and exceptions short-circuiting
native teardown); both were fixed and regression-tested. No dead scaffolding or further concrete
implementation defect was found.

| Delta scenario | Evidence and limits |
| --- | --- |
| Sounds | Existing config/model/native settings regression tests pass. |
| Lowering the volume | Existing settings and daemon volume tests pass. |
| Slider saves once | Existing native Settings drag/release tests pass. |
| Screen hints | Existing settings and window capture regression tests pass. |
| A level from the file that is not a step | Existing settings tests preserve 33% until editing. |
| Overlay opt in | Config round trips, real macOS checkbox, Settings-close and poll tests. |
| Default off | Missing/default config tests; headless/no-op and deferred enabling tests. |
| Save failure | Invalid TOML and write-error tests restore persisted model/control state. |
| Linux availability | Native GTK4 toggle persists on X11; unsupported display note; all 54 GTK4 tests pass. |
| Recording starts on a secondary display | Negative-origin/spanning/mixed logical geometry tests and native injected-screen tests; physical secondary display unavailable. |
| No target window geometry | Quartz application-PID fallback, primary fallback, visible bounds, small screens and display-removal tests. |
| Disabled or failed start | Daemon failed-start/default-off tests; no panel is created before microphone success. |
| Speech and silence | Silence/quiet/loud/int16-limit/stereo block tests, deterministic attack/release/stale decay, real quiet-room microphone; human speech tuning unavailable. |
| Slow UI | One pending dispatch after repeated changes; latest-value slot, streaming-preservation tests and callback benchmark. |
| Streaming fallback | Daemon integration keeps cloud processing and passes the guard to the fallback screenshot; real guarded processing screenshot/OCR tested separately. |
| Local operation and settings change | Parameterized daemon test changes next mode during local operation; native green preview. |
| Completion and failure | Daemon start/stop/digital-silence/no-speech/empty/error tests, 190 ms fade tests, existing partial/delivery regression coverage. |
| Cancel recording or processing | Daemon cancellation and early-dismiss tests; recording/settings cancellation checks dismissal before awaited cleanup; processing completion cannot revive hidden state. |
| Rapid consecutive recordings | Old tick/completion/fade and cancellation/restart tests; generation isolation in controller. |
| Focus and text delivery | Native target focus, mouse click-through, cursor movement, app switching, production paste and clipboard restoration pass. Linux Mousepad/XTerm are exercised below; physical hotkeys remain untested. |
| Context capture | Acknowledged hide-before-capture, overlap ownership, timeout, finally restoration, disable/cancel/restart tests; real screenshot pixels and Vision OCR inspected. |
| Reduced motion changes | Deterministic controller tests, native notification and teardown tests, static native rendering; actual System Settings toggle not performed. |
| Disable or quit | Live reload/deletion, resource cleanup tests and real desktop disable/re-enable/quit checks. |
| Hidden panel | Timer is invalidated; stale callbacks do not draw or resurrect the panel. |
| Unsupported or failed backend | Fresh-process import test; constructor/dispatch/render/timer/hide/close/meter failure injection; audio/provider/paste regression tests. |

## Unavailable checks and remaining risks

These were **not passed**, and the task checkboxes for verification record their disclosed limits:

- Human quiet/loud speech calibration and subjective animation/performance assessment require an
  interactive speaker; the actual microphone run sampled a quiet room only.
- Dedicated native text targets establish OS focus/paste/cursor/click-through behavior. Real
  macOS editor/terminal apps, human mouse interaction and physical hotkey presses still need an operator.
- Full-screen Spaces were not exercised. Collection flags and the elevated non-activating panel
  level are implemented, but OS/app-specific fullscreen behavior remains a manual check.
- Reduced-motion notification plumbing and injected preference changes passed; the user's actual
  System Settings preference was not changed.
- Only one display is attached. Negative origins, screen removal and mixed-scale logical geometry
  have automated coverage; physical multi-display placement remains unverified.
- Cloud/local provider lifecycle uses controlled responses in tests. No real provider audio was
  uploaded and no live whisper.cpp transcription was attempted during desktop validation.
- Native GTK was exercised in the Linux container below. Full Ubuntu installer/deb jobs remain CI coverage.

Optional overlay failures disable that backend for the process and log once with restart guidance.
If both the tray dispatcher and Cocoa's independent main operation queue fail, a static panel may
not be removable until the GUI loop recovers or the process exits; dictation remains independent.

## Linux/X11 extension

The user requested Linux support and confirmed X11. The delivered backend is GTK 3/Cairo on
the tray’s GLib loop, with no second GUI loop. GTK 4 Settings remains in its own process.
The installer, `.deb` dependencies and CI include GTK 3 and `python3-gi-cairo`; installed
venvs link both distro bindings. A clean isolated-venv test imports the actual Cairo bridge.

Final automated results after the extension:

| Check | Result |
| --- | --- |
| Linux/X11 full suite, unprivileged Ubuntu 24.04 Docker user, `pytest -m 'not gtk'` | **1,177 passed, 110 skipped, 54 deselected**, 19.07 s |
| Separate Linux GTK4 process, `VOX_REQUIRE_GTK=1 pytest -m gtk` | **54 passed**, 1.70 s |
| Final macOS suite with desktop and local-socket access | **1,274 passed, 67 skipped**, 40.34 s |
| Actual GTK3 native backend tests | **6 passed**; included in full Linux suite |

The initial root-container run invalidated a filesystem-permission test; rerunning as an
unprivileged user resolved it. Tests caught GDK version selection, a destroyed-window lookup
returning NULL, and GTK assigning an empty window a 200-pixel natural height. Explicit GDK 3
selection, a missing-window fallback, and the panel’s logical size request fix these issues.
The full-suite GLib dispatcher test now drains pending events instead of assuming five iterations.
The documentation contract now includes the new overlay setting.

Desktop reproduction is documented in [development.md](../../../../docs/development.md#recording-overlay-validation).
The harness uses actual Openbox/Xvfb, production window detection/paste/clipboard helpers,
Mousepad and XTerm (configured for Vox’s existing Ctrl+Shift+V terminal paste shortcut).
Waveform levels are controlled; no Linux microphone or provider is used.

Capture validation intentionally overlaps the target with the panel. An unguarded screen-region
capture contains the status and dark panel pixels. During the real guarded `maim -i` call, a
second capture of the same region contains no status, and the panel interior changes from
dark to the white target background. Production Tesseract output retains the context marker.
This covers both listening and fallback processing, with restoration before OCR. `maim -i`
alone can omit other windows on this desktop, so the screen-region comparison establishes
actual pixel exclusion rather than relying solely on window-targeted OCR.

X11 limitations: one virtual 1× display; no physical mixed-DPI/hotplug validation, human speech,
physical hotkeys or provider transcription. Openbox has no compositor in this harness, so
fade appearance is unverified; the window shape supplies clean rounded corners without one.
Native Wayland remains outside the requested scope.

[Linux desktop report](validation/linux/linux.json) records all ten passed scenario groups:
focus/paste/clipboard with overlay off/listening/processing, app switching, actual click-through,
guarded captures, restart/cancel/reduced motion, editor/terminal delivery, and disable/re-enable/quit.
XTerm receives typed markers around the pasted text to verify cursor placement; nothing is executed.

Actual native desktop screenshots (controlled levels):
[Listening in Mousepad](validation/linux/desktop-listening-batch.png),
[cloud processing](validation/linux/desktop-processing-batch.png),
[local processing](validation/linux/desktop-processing-whisper_cpp.png), and
[Listening in XTerm](validation/linux/desktop-terminal-listening.png).
The 360 × 56 pixel panel previews and visible/hidden capture pairs are saved alongside them.

The independent extension review found no concrete runtime defect. Its capture-evidence concern
was resolved with overlapping visible/hidden screen-region comparisons and direct pixel checks.
Final Ruff, shellcheck, diff whitespace and strict OpenSpec validation pass. The 70 installer,
`.deb` script and release-contract tests pass after adding installed Cairo/GTK smoke checks.

## Transcription bead refinement

At the user’s request, processing now uses a horizontal track and glowing bead in place of the
listening waveform. The shared bead geometry sweeps left-to-right every two seconds, fading at
the endpoints. Its clock starts at the stop transition; it never estimates completion. Local
processing keeps green, cloud keeps brass, reduced motion shows a static line, and completion
retains the existing fade and generation guards.

Focused macOS controller/native/daemon/settings checks: **48 passed**. The corresponding
Linux/X11 run: **50 passed**. Native AppKit and GTK/Cairo rendered the new states successfully.
The independent refinement review found no concrete defect. Ruff, diff whitespace and strict
OpenSpec validation passed. Earlier full-suite and desktop results above predate this drawing
refinement; their old processing previews are superseded by the images below.

[Mac/Linux side-by-side comparison](validation/bead/mac-linux-side-by-side.png) was opened in
Preview. Both columns use actual native renderers at 720 × 112 pixels for a 360 × 56 logical
panel, with matching phase and input level. The comparison sheet only arranges and labels
those native bitmaps; it does not substitute a mockup. Native panels can be regenerated using
`scripts/integration/overlay_preview.py --output <directory>` on each platform (Linux needs an
X11 display and session bus).

## Larger microphone icon refinement

The icon’s drawing slot is now 40 × 40 logical pixels on both platforms, twice its previous
width and height. Its visible plate is approximately 32 × 32 after the app asset’s transparent
inset. The source raster is 128 × 128 to keep it crisp, and the signal starts at x=54 with a
132-pixel width; the panel and text positions retain their existing dimensions.

All **4 native macOS tests** and **6 native Linux/X11 tests** passed. Native renders were
visually inspected; independent review found no concrete layout/rendering defect.
The [updated side-by-side image](validation/larger-icon/mac-linux-side-by-side.png) was opened
in Preview and supersedes the earlier small-icon comparison. Ruff and strict OpenSpec
validation passed.

## Speech sensitivity refinement

User testing confirmed the installed overlay works but normal speech appeared too flat. The old
linear mapping produced only 2.4% visual height at normalized RMS 0.005. The new logarithmic
-60 to -26 dBFS mapping produces 41.1% at that level, with a 35 ms attack and 140 ms release.
Levels at or below 0.001 remain flat, loud input is bounded, and stale/session checks remain
unchanged. This changes visual amplitude only; recording and transcription audio are untouched.

**81 focused overlay, audio and daemon tests passed**, including quiet-speech onset, steady
background noise, silence decay, loud clipping, stale levels and recording identity. Independent
review found no concrete regression. Ruff, whitespace and strict OpenSpec checks passed. The
updated build was installed locally, its overlay module verified against the checkout, and the
restarted service reported ready with the overlay enabled. The numeric comparison is synthetic;
further subjective tuning awaits the user's next microphone test.

## Microphone-only listening motion

The user requested no independent animation while listening. The controller now fixes the
listening carrier phase at zero: only the measured, smoothed microphone RMS changes its height.
This remains a stylized level visualization, not a raw PCM oscilloscope. Processing retains the
explicitly requested looping bead. Preview harnesses now use the same fixed listening phase.

**82 overlay, audio and daemon tests passed**, including a regression that advances time while
checking fixed listening phase, louder-input response, silence decay and moving processing bead.
Independent review found no concrete defect. Ruff, whitespace and strict OpenSpec checks passed.
The local installation matches the updated controller, retains overlay enablement, and restarted
successfully with a ready log and no startup errors.

## Compact stacked layout and restored drift

The user clarified that decorative listening drift was desired; it is restored while the
more sensitive microphone-driven amplitude remains. The earlier microphone-only-motion
refinement above is superseded. The user then requested a narrower panel with status below
the signal. The current layout is **240 × 80 logical pixels**, with a 40-pixel icon slot on
the left, a 164-pixel signal above its centered label, and 12-pixel rounded corners. Shared
size/signal geometry drives both native renderers, placement, test captures and previews.

Focused controller/native/daemon tests: **44 passed on macOS**, **46 passed on Linux/X11**.
The [fresh Linux desktop run](validation/compact/desktop/linux.json) passed focus, click-through,
clipboard/paste, guarded screenshot pixels/OCR, restart/cancel, reduced motion and cleanup.
Page OCR can skip the small stacked label inside the visible dark panel, so baseline presence
is established by the direct dark-to-white pixel comparison rather than requiring status OCR.
Independent review found no concrete layout, clipping, stale-size or lifecycle defect. Ruff,
whitespace and strict OpenSpec checks passed.

The [new Mac/Linux comparison](validation/compact/mac-linux-side-by-side.png) was opened in
Preview. Both columns are actual native 480 × 160 renders. The local install matches the
controller and both native renderers, has the overlay enabled, and restarted ready without
startup errors. This comparison supersedes the earlier wider layouts.

## Slim centered layout

The user rejected the 80-pixel panel and asked for a vertically centered waveform, then
requested a further small height/font reduction. The final panel is **224 × 50 logical pixels**.
The waveform/processing line and 40-pixel icon share the vertical center at y=25. A 10-pixel
status sits directly beneath the signal. Its 20-pixel carrier geometry bounds full-scale peaks
above the label. Decorative drift, microphone sensitivity, and processing bead remain intact.

**44 macOS** and **46 Linux/X11** focused controller, native-renderer and daemon checks passed.
Both native previews were inspected; independent review found no concrete clipping or alignment
defect. Ruff, whitespace and strict OpenSpec checks passed. The
[latest Mac/Linux comparison](validation/slim-50/mac-linux-side-by-side.png), opened in Preview,
supersedes the taller layouts above; both native bitmaps are 448 × 100 pixels.

The final slim build was installed locally; the controller and both renderer files match the
checkout. The overlay is enabled and the service restarted ready without startup errors.

## Status alignment refinement (superseded)

The user found the status off-center. Both native renderers now center the 10-pixel label
across the whole 224 × 50 panel rather than the waveform column, and raise it slightly
to provide bottom spacing. The rim is softened to 65% opacity and 0.75-pixel width.
The waveform remains vertically centered at y=25.

Native rendering tests passed: **4 macOS** and **6 Linux/X11**. Independent review of code,
specifications and native previews found no concrete centering, clipping or spacing defect.
The [updated Mac/Linux comparison](validation/aligned/mac-linux-side-by-side.png) was opened
in Preview. This comparison supersedes the preceding status alignment.

The local installation matches the controller and both renderer files. The overlay remains
enabled, and the service restarted ready with no new startup errors.

## Waveform-centered status

The user clarified that the status must be centered with the waveform. Both renderers
now place the measured text at the signal’s horizontal center, x=132. The raised
vertical position and 224 × 50 panel remain. Native Mac/Linux renders were regenerated,
visually inspected and independently reviewed with no concrete alignment or clipping
findings. The [corrected comparison](validation/wave-centered/mac-linux-side-by-side.png)
is open in Preview. Ruff, whitespace and strict OpenSpec checks passed.

The local installation matches both corrected native renderers and the shared controller,
retains overlay enablement and restarted ready without new startup errors.

## Integrated microphone, smaller text and delivery timing (current build)

The panel is now 224 × 44 logical pixels with 8 px text centered under the vertically
centered signal. The microphone glyph sits directly on the pill without its square plate.
Recording uses red and processing currently uses blue in both the overlay and menu bar.
After comparing alternative colors, the user chose to keep sky blue (#50BEFF).

Normal and partial-result delivery await native dismissal before paste, bounded to 300 ms.
A review caught a lock reacquisition that could outlast the timeout; it is fixed and tested
with a deliberately stalled native hide and an already-held UI lock. History saving cannot
keep the panel visible. Empty/no-speech results retain their brief fade.

The broader macOS run passed 165 tests with 1 skip and one obsolete height assertion.
After updating that assertion, all 51 controller/icon tests passed. The final Linux/X11
controller/daemon/native/icon suite passed 71 tests. Native renders were inspected and
independently reviewed. Ruff, whitespace and strict OpenSpec checks passed.

The [refined comparison](validation/slim-integrated/mac-linux-side-by-side.png) was opened
in Preview. The installed controller, renderers, icon generator and daemon match the checkout;
the overlay is enabled and the service restarted ready with no new startup errors.

The user also requested waveform color options. The
[waveform palette comparison](validation/waveform-options/waveform-color-options.png) shows
current brass, sky blue, mint, violet and silver through both native renderers at the same
amplitude and phase. It was opened in Preview; these are preview-only palettes, with the
installed waveform colors unchanged pending a selection.

## Selected local silver palette

The user selected silver for local feedback: waveform layers #D6DCE5/#9BA5B5/#606979,
with a pale #F5F7FF bead highlight and silver status. Cloud feedback remains brass.
Actual macOS and Linux local listening/processing renders were inspected and independently
reviewed; Ruff, whitespace and strict OpenSpec checks passed. The
[local silver comparison](validation/local-silver/local-silver-comparison.png) is open in Preview,
including blue versus silver microphone grilles and menu-bar icons. Silver microphone
highlights are preview-only; the selected sky blue processing grille remains installed.

The local silver palette is installed, its renderer/icon files match the checkout, and the
service restarted ready without new startup errors.
