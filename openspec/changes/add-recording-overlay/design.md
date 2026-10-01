## Context

See proposal.md for motivation and the delta specs for behavior. The daemon owns IDLE/RECORDING/PROCESSING state and `_Session` resources. It sets RECORDING before `Recorder.start()` succeeds and sets PROCESSING before post-roll and microphone close. `_process()` finishes streaming, can fall back to batch and launch another screen capture, pastes text, and records history. Cancellation and error paths release resources through several separate methods. History retries also use PROCESSING, so state alone cannot identify a live dictation.

`Recorder` already receives int16 audio in 50 ms blocks. Tray updates use injected dispatch, with `AppHelper.callAfter` on macOS. Native settings use a shared `SettingsModel` and TOML mutation helpers that retain unrelated settings. macOS focused-window detection uses the frontmost application PID and layer-zero windows; OCR uses `screencapture -l` for a specific target window.

The reference is `/Users/alex/Developer/personal-site/src/components/showcase/vox.ts` (`draw()`) and `VoxShowcase.astro` (`.vox-signal`, `.vox-wave`, `.vox-recording-status`). It draws a baseline/ruler and three enveloped carriers in brass `#c9a24a`, `#8f7436`, `#5c4c30`; local silver uses `#d6dce5`, `#9ba5b5`, `#606979`. Its simulated amplitude, scene timing, and completion progress cannot drive desktop state.

## Goals / Non-Goals

**Goals:** Keep the overlay optional, main-thread owned, bounded in work, and independent of transcription success. Make race handling and capture isolation testable without a desktop.

**Non-Goals:** Native Wayland overlay rendering, history retry indicators, transcript previews, controls other than cancellation, new audio capture, or changes to paste semantics. Linux/X11 has a native panel; unsupported/headless environments use a no-op backend.

## Decisions

### Shared preference and platform boundary

Add `Config.overlay_enabled = False`, load strict boolean `[overlay] enabled`, and persist through `update_flag` plus a shared SettingsModel setter. Add the General toggle on macOS and Linux/X11; GTK disables it on non-X11 displays with an X11 availability note. Extend `_ConfigApplier` for live changes, including config deletion resetting the default. Disable tears down immediately when applied; enable during an operation waits for the next recording. Use the existing polling and Settings-close reload cadence.

Place a small platform-neutral overlay controller/interface under `vox/ui`, with lazy platform backend imports. Instantiate only when a supported GUI/main-loop dispatch is available. Unsupported environments use a no-op. Reuse tray dispatch without opening a second event loop or settings subprocess. An always-imported AppKit renderer would break headless/Linux imports; a web view adds unnecessary runtime and rendering overhead.

### Explicit dictation identity and transitions

Give each live dictation a monotonically increasing generation, carried through controller calls and queued UI snapshots. The controller maintains hidden/listening/processing/fading/closed state. Native callbacks, timer ticks, fade completions, and capture restoration validate generation and lifecycle revision; disable/quit invalidate all pending work. Coalesce state delivery to one pending dispatch whose drain reads the latest snapshot. Audio never dispatches UI calls.

Show only after successful microphone start and target-window lookup; do not hook blindly into `set_state(RECORDING)`. Continue microphone startup first so overlay work does not delay capture. Attach placement to the recording-start context and keep that display for the operation; a newly focused app at stop still follows existing paste/context behavior. At stop request clear listening and show processing, detach live level consumption even while post-roll continues. Use `_Session.mode` for local/cloud text. Streaming fallback remains cloud processing. Dismiss without a fade before text injection, including partial-result fallback. Await native hide acknowledgment on a worker for at most 300 ms; a missing or failed UI must not prevent delivery. History saving cannot keep the panel visible. No-speech and empty results retain a brief 190 ms fade.

Dismiss at the beginning of cancellation, before awaiting stream/backend cleanup. Explicitly handle failed starts/stops, digital silence, no speech, empty text, processing failures, recovery, settings-triggered cancellation, disable, and shutdown. Pass outcome/identity through the live operation completion path so failure dismisses immediately, text delivery hides before paste, and empty completion fades. Keep the existing task identity safeguards for `process_done`; overlay generation guards supplement them. History retries cannot create an overlay by merely setting PROCESSING.

### Latest microphone level and visible-only drawing

When enabled for a live dictation, compute a finite normalized RMS level from the existing callback block using a bounded float calculation that avoids int16 overflow. Publish only the latest scalar and recording identity in a fixed-size slot; no extra PCM retention, UI dispatch, waiting lock, or queue. Reset on start/stop/discard and turn measurement off when disabled. Failure in optional level calculation cannot prevent storing/streaming audio. A main-thread timer reads the newest value at approximately 30 Hz while visible; stale data decays to zero. Map microphone RMS logarithmically from -60 dBFS (flat baseline) to -26 dBFS (full visual height), clamped to 0–1, so low-level speech remains visible. Smooth with a 35 ms attack and 140 ms release to catch short syllables and settle between words. This affects visual feedback only. Tune further using quiet and loud real speech.

Render shared reference-style three layered paths while listening in AppKit and GTK/Cairo views. Let their carrier phase drift decoratively with monotonic time; only smoothed microphone level controls their height, so the drift does not invent speech activity. This is a stylized level visualization, not a raw PCM oscilloscope. During processing, replace the waveform/ruler with a horizontal line and glowing bead that sweeps left-to-right every two seconds, fading at the ends before repeating. Use shared bead geometry, brass for cloud and silver for local, and elapsed time from the stop transition so each transcription starts at the left. The loop indicates activity, never progress toward an assumed completion time. Panel uses `#1b1d20` at approximately 95% opacity, `#51504b` border at 65% opacity and 0.75 px width, 12 px corners, subdued shadow, `#c2c3c5` text, existing Vox microphone/cog glyph on transparency in a 40 px slot, integrated directly into the pill without the launcher icon’s square plate. Both the overlay and menu bar use a red grille while recording and a blue grille while transcribing; native overlay images are cached by state, with the icon at (6,2) and a 153 px signal area beginning at x=50 and ending at x=203. Measure the left gap from the microphone capsule at waveform height, rather than the wider cog below it. In the 2× native raster, the capsule ends at x=59, the signal occupies x=100–405 and the inner rim begins at x=446, leaving 40 clear pixels on both sides. Center the 8 px status beneath the waveform/processing line on their shared horizontal center at x=126.5, with extra bottom breathing room. Fit the local label without increasing the width. The signal is vertically centered at y=22; its 20 px carrier geometry bounds full-scale peaks above the label. The status baseline is approximately 38 px below the top. Start at 224 × 44 points and scale drawing for display backing resolution. A single drawing timer also drives fades; invalidate when hidden. Reduced motion uses a static line/status, no fade or decorative carrier motion; observe changes and redraw once on status updates. Remove timer and accessibility observer on teardown.

### Native focus and display placement

Use a borderless non-activating NSPanel, nonzero floating level, explicit refusal to become key/main, ignored mouse events, and order-front behavior that never activates NSApplication. Do not invoke window helpers that activate regular Settings/History windows. Configure appropriate Space/fullscreen auxiliary behavior and validate it on supported macOS versions. Reuse packaged icons without installing additional assets.

Obtain target window bounds from Quartz using the captured context window ID. Convert Quartz display coordinates to AppKit screen coordinates, including negative origins and mixed backing scales. Select the screen with greatest target-window intersection, center horizontally in its visible frame, and use a modest bottom inset (starting at 24 points). Fall back to application-window display then primary display. Clamp for small screens and recompute if displays disappear; use display geometry, not pointer location. Focus and paste detection continue using existing context logic.

### Linux/X11 native backend

Use GTK 3 in the daemon process, matching pystray’s AppIndicator dependency; GTK 4 Settings
stays in its existing separate process. Reuse GLib dispatch and visible-only timers. Draw the
same icon, status, palette and shared waveform paths with Cairo. Package/probe GTK 3 and the
PyGObject Cairo bridge explicitly. The panel is an undecorated, non-focusable, always-above
window, skipped by task switchers, with an X11 input shape restricted to the cancel button for click-through elsewhere. Rounded
window shaping also works without a compositor; opacity fades are best-effort without one.

Resolve the captured X11 window through GDK’s foreign-window support and select its monitor;
otherwise use the captured application's normal window then primary monitor. Keep the selected
monitor until the next recording, react to monitor/work-area changes, and place within its
logical work area (top-left origin, bottom inset). Use GDK’s scale-aware coordinates rather than
mixing raw X pixels with logical monitor sizes. Observe GTK’s enable-animations setting for
reduced motion and remove every GLib source/signal on teardown. Flush and synchronize the X
server after hiding before acknowledging screenshot acquisition. Apply the existing guard to
`maim` only, restoring before Tesseract OCR. Native Wayland is outside the user-confirmed X11
scope; detect the actual GDK backend before native creation or enabling its GTK Settings control.

### Capture isolation with explicit synchronization

Keep OCR targeted to the captured target window ID and set native window sharing exclusion where available as defense in depth. Do not assume sharing flags alone guarantee exclusion across macOS capture mechanisms. Add a narrow capture guard around the actual screenshot acquisition, covering initial capture and streaming-to-batch fallback. It temporarily orders the overlay out on the main thread, acknowledges that hide before screenshot begins, and restores only after screenshot acquisition finishes if the same generation/revision remains eligible. OCR processing after image acquisition need not keep the panel hidden.

The guard runs in the screenshot worker; it never blocks the main thread or audio callback. Use bounded synchronization with fail-closed capture behavior: if hide cannot be acknowledged, skip optional screenshot context while dictation continues. Pair restoration in a finally path and handle overlapping captures with suppression ownership/counting so one cannot restore while another still captures. Late release cannot resurrect a cancelled/disabled panel or hide a newer recording. Avoid a full-screen capture fallback. Validate pixel exclusion on real macOS; fake capture tests only verify ordering and ownership.

### Failure containment and verification

Wrap optional backend construction, dispatched methods, rendering, and teardown; log one actionable failure without dictated content, invalidate the faulty backend, and continue dictation. Lifecycle state must clear even if native cleanup fails. Never make transcription, paste, or microphone shutdown await an overlay animation. Test with fake dispatch/timers/backend and deterministic audio blocks; real focus, Space behavior, click-through, and OCR pixel exclusion require desktop validation.

## Risks / Trade-offs

- AppKit panel behavior differs across Spaces/fullscreen apps → validate normal windows, full screen, multiple displays, and Dock placement on the available system.
- Capture synchronization introduces a brief visual disappearance → suppress only during screenshot acquisition; skip optional capture on timeout rather than send contaminated context.
- Audio metering adds callback work → operate only when needed, keep data fixed-size, and measure callback cost against 50 ms blocks.
- Quiet microphones and noise vary → clamp and tune smoothing/noise floor with real input without changing recorded audio or VAD.
- Concurrent existing retry work touches daemon/settings → inspect the current branch and preserve retention, retry, cancellation, and notices in regression tests.
- Manual microphone, paste, or multiple-display checks may be unavailable → record each unavailable check explicitly; automated fakes do not establish real desktop behavior.

## Migration Plan

Ship default off with strict optional configuration parsing; existing files need no migration. Document General Settings → “Show recording overlay”, `[overlay] enabled = true`, macOS and Linux/X11 rendering coverage, and unsupported/headless fallback. Roll back by disabling the preference or removing the optional controller integration; retain existing settings and dictation behavior. During implementation, run focused tests and the project's normal checks, then report real manual evidence and limitations before marking validation tasks complete.

### Explicit cancel control

Keep the 224 × 44 layout and add an 18 × 18 hit target at (204,2) with a small neutral ×. The body remains click-through. AppKit uses a transparent nonactivating panel for the button above the click-through drawing panel; both hide for capture, delivery, cancellation and cleanup. X11 restricts its input shape to a transparent native button. Neither can take focus. Both expose a Cancel accessibility label and tooltip. Capture the operation generation on press, validate it in the controller, dismiss immediately, then dispatch generation-checked cancellation to the asyncio loop. Interrupt the process task immediately even if the daemon event consumer is awaiting settings reload; queue the remaining state/resource cleanup. A published cancellation-generation scalar also prevents delivery or failure retention while dispatch is pending, without waiting on the native-work lock. Ignore stale, hidden, fading and capture-suppressed clicks. Retain the existing cancellation cleanup path.

Add strict boolean `[hotkey] double_tap_cancel`, default false, for both primary and fallback keys. True retains the existing timing behavior; false makes quick taps ordinary toggles. Reload this preference with other hotkey settings. Existing explicit true/false values remain authoritative; the timeout alone does not enable cancellation.
