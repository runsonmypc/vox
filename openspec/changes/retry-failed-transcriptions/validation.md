# Implementation validation

Validated on macOS and Ubuntu 24.04, 2026-09-30.

## Native desktop integration

The repeatable harness in `scripts/integration/recovery_desktop.py` passed all ten scenarios on both platforms:

| Platform | Desktop | Result | Report |
| --- | --- | --- | --- |
| macOS 15.5 | Logged-in AppKit desktop, Accessibility enabled | 10/10 passed | [macOS](validation/macos.json) |
| Ubuntu 24.04 | GTK 4, Openbox and Xvfb in Docker | 10/10 passed | [Linux](validation/linux.json) |

The tests open real History and external text windows in separate processes. They exercise the production daemon, Unix control socket, recording store, provider wrappers and converters, focus detection, clipboard handling and paste keystrokes. The final Linux run used `--network none`, with provider requests confined to loopback.

Scenarios cover failed capture and restart without automatic retry; instant Local retry after History releases focus; full-recording Batch upload; History reopening and paste suppression; switching external target apps; cancellation while hotkeys are paused; deletion during retry; separately copying partial attempts; live retention opt-out/re-enable; and clearing indexed and unindexed audio. GTK's narrow layout was also checked. Successful retries paste exactly once and restore the target clipboard. The macOS harness restores the original foreground application and original clipboard types after finishing.

This is automated native desktop validation. Audio capture, hotkey and sound hardware are replaced. A controlled whisper CLI and an OpenAI-compatible loopback HTTP endpoint supply provider responses; no request reaches OpenAI. Synthetic WAV input bypasses VAD, partial attempts are seeded, native buttons are activated programmatically, and Clear confirmation is accepted by the fixture. These tests establish native recovery, focus and paste behavior. Microphone capture, acoustic transcription accuracy and human mouse interaction require separate validation. Repeatable commands are documented in [development.md](../../../docs/development.md#native-recovery-integration).

The previous statement that no logged-in macOS GUI was available was incorrect. Native tests succeeded when run outside the filesystem sandbox with desktop access.

## Required checks

- Full macOS repository suite: **1215 passed, 60 skipped**.
- Linux non-GTK suite, run as an unprivileged user: **1117 passed, 105 skipped, 53 deselected**.
- Linux native GTK suite with `VOX_REQUIRE_GTK=1`: **53 passed, 1222 deselected**.
- Native macOS History/Settings subset: **88 passed, 53 skipped**.
- `ruff check scripts/integration vox tests`: passed.
- `scripts/lock.sh --check`: passed; dependencies were unchanged by desktop validation.
- `git diff --check`: passed.
- `openspec validate retry-failed-transcriptions --strict`: passed.

## Implementation review and coverage

Implementation and native integration were reviewed against the planning artifacts through the review-implementation skill. Identified transaction races, publication faults, cancellation boundaries and provider-log privacy issues were fixed and covered with regression tests. The final review found no implementation gap that invalidates the exercised focus, paste, clipboard or recovery-control results.

Repository tests also cover migration, conditional revisions, original WAV round trips and permissions, corrupt/symlink rejection, storage/index/publication faults, reconciliation, cleanup tombstones, opt-out during publication, repeat partial attempts, provider attribution, configuration gates, missing focus acknowledgements, stale/deleted completions, cancellation around transcript commit and paste, clipboard restoration, interrupted retry reset, protocol bounds and offline locking.

All **20/20 implementation tasks are complete**. The change is ready for archive.
