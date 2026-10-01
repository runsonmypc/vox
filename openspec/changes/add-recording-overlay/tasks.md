## 1. Settings and platform boundary

- [x] 1.1 Add default-on `overlay_enabled` and strict `[overlay] enabled` loading, with persistence through the shared flag mutation path; verify default/missing config, true/false round trips, invalid types, and preservation of unrelated TOML content in config tests.
- [x] 1.2 Add the shared SettingsModel preference setter and macOS General toggle, plus available Linux/X11 GTK toggle and an X11 availability note on unsupported displays; verify save errors restore persisted state and native settings use the shared model.
- [x] 1.3 Apply the preference through daemon config reload and Settings-close handling, including deletion resetting defaults and enable waiting for the next recording; verify live-disable and reload tests.

## 2. Audio levels and overlay controller

- [x] 2.1 Add opt-in latest-level measurement from existing int16 callback blocks with no audio/UI queue or extra stream; verify silence, loud/quiet blocks, multichannel input, overflow safety, reset, stale levels, and metering failure preserving recorded/streamed audio.
- [x] 2.2 Implement a platform-neutral controller and lazy native/no-op backend boundary with generation/revision guards and one pending UI dispatch; verify delayed dispatch, old fades, cancellation followed by restart, disable/re-enable, and Linux/headless imports without AppKit.
- [x] 2.3 Implement smoothing and visible-only timer ownership plus reduced-motion behavior; verify attack/release, stale-data decay, no timer work while hidden, and observer/timer cleanup using deterministic clock/backend tests.

## 3. Native macOS presentation

- [x] 3.1 Build the non-activating click-through panel and reference-inspired icon/status/layered paths using the existing native stack and packaged icon; verify main-thread ownership and panel flags and visually compare all three statuses to the website reference.
- [x] 3.2 Resolve target-window display and place the panel within the visible frame; verify geometry tests for primary/secondary displays, negative origins, mixed scales, spanning windows, missing geometry, small screens, and display removal.
- [x] 3.3 Add restrained indeterminate processing animation and completion fade, respecting reduced motion and generation invalidation; verify no invented completion progress, no late fade affecting a newer panel, and no animation after dismissal.

## 4. Dictation and context integration

- [x] 4.1 Show after microphone startup succeeds and integrate stop/post-roll, streaming finish/fallback, captured local mode, completion, empty/no-speech output, and failed start/stop/digital silence; verify daemon lifecycle tests and unchanged transcription/paste calls.
- [x] 4.2 Dismiss before cancellation awaits and on errors, recovery, disable, settings-triggered cancellation, and shutdown; verify recording/processing cancellation, rapid consecutive operations, failure feedback, resource cleanup, and no history-retry overlay.
- [x] 4.3 Add capture exclusion and a bounded acknowledged main-thread hide guard around screenshot acquisition for initial OCR and streaming fallback; verify hide-before-capture ordering, finally restoration, overlap ownership, timeout skipping optional context, and cancel/disable/new-generation races without resurrecting stale panels.
- [x] 4.4 Contain optional measurement/backend/dispatch/render/teardown errors; inject failures and verify audio, transcription, clipboard handling, and shutdown remain usable with bounded logs and resources.

## 5. System verification and documentation

- [x] 5.1 Run focused config, settings, audio, overlay, daemon, tray, window, injector, streaming, cancellation, and retry regression tests plus repository-required checks; record commands/results and confirm unsupported/headless no-op behavior.
- [x] 5.2 Review on an available macOS desktop with real microphone input: silence, quiet/loud speech, cloud/local processing, cancellation, fast restart, off/on settings, reduced motion, and quit; record visual/performance observations and explicitly identify unavailable checks rather than marking them passed.
- [x] 5.3 Verify actual app focus, cursor, click-through, hotkeys, paste destination, and clipboard restoration in editor and terminal, including app switches and full-screen Spaces; compare with overlay off and record any unavailable desktop checks.
- [x] 5.4 Verify actual screenshot pixels/OCR exclude the visible overlay during initial capture and cloud fallback, and verify placement on multiple displays with differing layouts/scales where available; record evidence or the precise hardware/permission limitation.
- [x] 5.5 Update README, settings/config documentation, and relevant UI documentation with enablement instructions, default-on behavior, macOS and Linux/X11 coverage, unsupported/headless limits, and reduced-motion behavior; verify documentation agrees with implemented settings and delivered coverage.
- [x] 5.6 Review implementation against every delta scenario and deliver a validation record with automated results, performed manual checks, unavailable checks, remaining risks, and how to enable; complete tasks only with evidence matching their acceptance criteria.

Validation evidence and explicitly unavailable desktop checks: [validation.md](validation.md).

## 6. Linux/X11 support (user-requested extension)

- [x] 6.1 Implement lazy GTK 3/Cairo backend using the existing tray loop and shared waveform paths, with non-focusable/click-through native flags, reduced-motion observation, visible-only timers and bounded cleanup; test under X11.
- [x] 6.2 Place within the captured target monitor’s logical work area, preserving monitor identity and handling missing windows, scaling and display changes; add geometry/backend tests.
- [x] 6.3 Guard the actual Linux screenshot acquisition with acknowledged X-server hiding and finally restoration before OCR; test timeout, failure and lifecycle races.
- [x] 6.4 Add explicit Linux Cairo/GTK runtime dependencies, CI and native integration coverage; validate settings, focus, cursor/click-through, clipboard/paste, capture exclusion, cancellation/restart and cleanup.
- [x] 6.5 Capture actual Linux/X11 in-use screenshots, document delivered coverage and limitations, and review the extension against the delta scenarios.

## 7. Distinct transcription animation (user-requested refinement)

- [x] 7.1 Replace the processing waveform on both platforms with a horizontal line and looping glowing bead, preserving local/cloud colors, reduced motion, fade and timer ownership; verify native rendering and controller timing.
- [x] 7.2 Update the visual specification and documentation, render Mac/Linux at matching scale, and open their side-by-side comparison in Preview.

- [x] 7.3 Enlarge the overlay icon on both platforms, rebalance its spacing, verify native rendering, and open a refreshed side-by-side comparison.

- [x] 7.4 Improve waveform response to quiet speech while retaining a steady noise floor, bounded height and stale-level handling; verify and update the local installation for user testing.

- [x] 7.5 Clarify the listening visualization: retain user-approved decorative drift with microphone-driven height and the separate processing bead; verify and update the local installation.

- [x] 7.6 Redesign as a compact 240 × 80 panel with status centered underneath the signal, verify native layout/placement, show the Mac/Linux comparison, and update the local installation.

- [x] 7.7 Replace the rejected tall layout with a slim 224 × 50 panel, vertically centered signal and subordinate status beneath; render/review both platforms and update the local installation.

- [x] 7.8 Center status across the panel, improve its bottom spacing and soften the rim; review native renders and update the local installation.

- [x] 7.9 Correct the status alignment to share the waveform’s horizontal center on both platforms; refresh Preview and the local installation.

- [x] 7.10 Integrate the microphone directly into the pill without the square plate and reduce status text to 8 px; show native Mac/Linux previews.
- [x] 7.11 Hide and acknowledge dismissal before text injection, including partial fallback, without a completion fade or history-save delay; test ordering and bounded failure behavior and update the local build.

- [x] 7.12 Distinguish recording with a red microphone grille and transcription with blue in both overlay and menu bar, and trim the panel to 224 × 44; render, verify and install the refined build. After comparing alternatives, the user chose sky blue (#50BEFF); superseded on 2026-10-01 by transparent slots without glow in the overlay.

- [x] 7.13 Use silver for local waveform/processing feedback and show a silver microphone-grille preview beside the selected sky blue.

## 8. Explicit cancellation (user-requested extension)

- [x] 8.1 Add the top-right cancel button on macOS and X11 with equal visible-icon-to-signal and signal-to-right-edge spacing, preserve focus/body click-through and capture hiding, and guard cancellation against stale operations.
- [x] 8.2 Default double-tap cancellation off with strict opt-in configuration and live reload for both hotkeys; verify default and enabled behavior.
- [x] 8.3 Verify native interaction and lifecycle tests, update documentation, render a Mac/Linux comparison, and install locally for testing after independent implementation review.

- [x] 8.4 Correct optical spacing using the rendered opaque glyph rather than faint alpha bounds; compare native Mac/Linux renders and update the local build.

- [x] 8.5 Match the right gap to the 40-pixel microphone-body gap, verify native pixels on both platforms, refresh Preview and install locally.
