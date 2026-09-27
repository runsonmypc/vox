## Context

See proposal.md for why this round exists, and the delta specs for the resulting behavior.

The round started from commit 2d928cf on `production-ready`. A full review produced verified findings in five areas: the dictation pipeline, platform code, configuration and persistence, the desktop UI, and release. Two waves of parallel groups fixed them. Each group owned a fixed set of files and worked in its own worktree.

- Wave 1 had five groups: A (pipeline), B (platform), C (config and persistence), D (desktop UI) and E (release). Its merged result is commit 1a05778, with 727 tests passing on macOS, and 670 plus 26 GTK tests passing on Linux under Xvfb.
- Wave 2 implements the decisions the orchestrator took on wave 1's open questions (DECISIONS2.md). It has six groups: G1 (audio), G2 (core), G3 (UI), G4 (release and docs), G5 (these specs) and G6 (read-only verification on the Linux test PC).

Constraints that shaped every decision (DECISIONS.md, "Hard rules"):
- Tests never call OpenAI or any paid API, and never touch the real keychain, config, history or installed Vox.
- Billed work is never discarded. A hung *local* subprocess may be bounded by a timeout, but an in-flight OpenAI request or live transcription may not.
- No new default and no new user setting beyond the owner's decisions. Anything that needs one goes back to the owner.
- Vox runs as a launchd agent on macOS, with uv's CPython 3.12, PyObjC and permissions granted to that exact binary. On Linux it runs as a systemd user service on X11, with Ubuntu 24.04 as the reference, and the tray and windows use the system `python3-gi`.

## Goals / Non-Goals

**Goals:**
- No dictation the user spoke or paid for is lost silently: a failed paste, a partial upload and a slow stream all keep the text.
- Nothing private leaves the computer, or lands in a file others can read, unless the user turned it on: screen text is filtered or off, logs hold no dictated text, and history, config and logs are owner-only.
- Vox installs, updates and uninstalls from a published release without a compiler or a checkout, and no setting can make it crash-loop under its login service.
- The specs match the code, so a later verification pass does not "fix" correct behavior.

**Non-Goals** (owner decision 9 and DECISIONS2 decision 12):
- A delay before uploading, so that a double-tap during a batch recording could never send audio. The first tap stops the recording at once. The second cancels the processing, which may already have started uploading.
- A resident whisper-server. whisper.cpp still runs one process per dictation, with DEBUG timing logs only.
- A history retention setting, and HTTP keep-alive tuning for the OpenAI client.
- A Wayland backend. Vox only detects Wayland and warns.
- An AVFoundation microphone-permission check. Vox detects all-zero audio instead.
- A signed `.app` or `.dmg`, a lossless multi-target X11 clipboard restore, and package names for non-apt distributions. Each of these is left for the owner after 1.0.

## Decisions

### Owner decisions (DECISIONS.md, 2026-09-26)

**1. v1.0.0, GPL-3.0-only, public on GitHub.** The license goes in `pyproject.toml` as an SPDX expression with `license-files`, which needs setuptools 77 or later. The repository is made public by the owner. CI assumes it is public, where the standard macOS and arm64 Linux runners are free.

**2. Release artifacts: a tarball with `install.sh`, plus a `.deb`.** The `.deb` targets Ubuntu 24.04 on amd64 and arm64, and is enabled for every user with `systemctl --global enable`.
- Rejected: a signed `.app` or `.dmg`. It would need a Developer ID, notarization and a bundled interpreter. Until then, macOS permissions stay attached to the uv Python binary, so the installer pins the exact patch version and warns when it changes.
- Rejected: PyPI or pipx as the main channel. On Linux the tray needs the system PyGObject, which pip cannot build without a compiler and development headers.
- The `.deb` builds its virtualenv at `/opt/vox/venv`, where it will run, because a virtualenv cannot be moved. It links the system `gi` into the virtualenv, and pins `python3 (>= 3.12), python3 (<< 3.13)` to match.
- Files under `/etc` are conffiles, so an administrator's edits survive upgrades and only a purge removes them.

**3. CI must be free.** Only the standard GitHub-hosted runners are used: `ubuntu-24.04`, `ubuntu-24.04-arm` and `macos-latest`.
- Rejected: larger or paid runners, and self-hosted runners.
- Third-party actions are pinned to commit SHAs, and only the job that creates a release can write to the repository.
- uv is pinned to 0.12.19, the version that wrote `uv.lock`, because lock output depends on the uv version.

**4. Screen hints: `[context] screen`, on by default.** Only the focused window is captured, secret-looking tokens are stripped before sending, and streaming gets the same filtered keyword list as batch. When the setting is off, nothing is captured and no window words are sent.
- Rejected: capturing the whole display, or every window of the focused app. Both read content the user is not looking at.
- Rejected: keeping streaming's raw `Window:` / `Screen:` prompt lines. They sent the window title verbatim, with no filtering, while the OCR that streaming ran never reached the session.
- The filter works on whole tokens before words are split. Otherwise a base64 key cut at `/`, `+` and `=` would leave pieces too short to look random.
- The filter errs toward dropping. Long identifiers that contain digits are never sent as hints, since nobody dictates them.
- Streaming starts no capture. Its keywords are sent when the socket opens, before any OCR could finish, so a capture at that point only cost time and Screen Recording exposure. A fallback to batch captures when it starts instead. It reads the window that was focused when the recording started, found again by the window ID and process detected then, and does not look up which window has focus at fallback time.

**5. The recording limit auto-stops, at a new default of 900 s.** The tray offers 5 to 60 minutes. Uploads are 16 kHz mono PCM16. A recording over about 24 MB is split at pauses.
- Rejected: the old behavior, where the recording ran on and the audio after the limit was silently dropped at stop. The user lost the end of what they said.
- Rejected: 48 kHz uploads. OpenAI's file limit is 25 MB, and 16 kHz mono fits about 13.6 minutes in one file.
- Rejected: cutting parts at fixed times, which splits words. Each part is cut instead in the quietest 300 ms of its last quarter.
- Rejected: uploading parts in parallel. Parallel uploads would be faster, but they would also bill later parts after an earlier one had failed. Parts go up one after another, and a failure stops the rest.
- The recorder reads the limit when a recording starts and stops buffering exactly there, so memory is bounded. The limit then reaches the daemon as an event, which handles it like a stop toggle. A stale event from a cancelled recording finds nothing to stop.

**6. Logs contain no dictated text.** Transcript and snippet text is logged only at DEBUG. The macOS log moves to `~/Library/Logs/Vox/vox.log`, with the directory at 0700 and the file at 0600, and `install.sh` renders its path into the plist as an absolute path.
- Rejected: `/tmp/vox.std*.log`. Every account could read it, and verbose logs hold dictations.
- Rejected: keeping text at INFO with redaction. Lengths are enough to debug, and `vox -v` exists for the rest.
- launchd expands neither `~` nor `$HOME`, so the plist is a template with placeholders, filled with `plistlib` by `install.sh`.

**7. Clipboard: restore everything, and keep Vox's own write out of clipboard history.**
- On macOS, every item and type is snapshotted and written back. The dictation write is current-host-only and carries the `org.nspasteboard` transient and auto-generated markers. The user's own Copy (from the tray or the history window) stays a normal copy.
- macOS gives no notice when the target has read the pasteboard, so the restore waits 0.5 s, and skips the restore if `changeCount` moved in the meantime. 50 ms was too short for a busy app, which then pasted the old clipboard.
- The Command+V keycode is looked up in the current keyboard layout on every paste. A non-Latin layout used to send keycode 0, which is Cmd+A. When a layout has no "v", Vox falls back to the ANSI V key, as macOS does.
- X11 restores one target. xclip can own only one, so the order is: copied files (`text/uri-list` of `file://` URIs), then plain text, then PNG, then HTML, then other images. Text beats HTML because restoring HTML alone would leave terminals and plain text fields with nothing to paste.
  - Rejected: owning the X selection directly through python-xlib, including INCR transfers, to restore every target losslessly. It is a larger change that could not be checked on X11 during this round.
- On X11 the dictation is served by a foreground `xclip -quiet`, and Vox counts the requests it reports, so the restore happens only after the target has fetched the text. It waits at most about 1 s for that first fetch, then 0.1 s for a second.
  - Rejected: `xclip -l N` loops. A clipboard manager that reads the new contents early would use up the count before the paste.
- Paste runs on a worker thread behind one lock, so a cancelled paste still finishes restoring before the next paste takes its snapshot. Every local tool has a timeout. A transcript that cannot be put on the clipboard sends no keystroke.

**8. Billed work is never discarded.**
- A failed paste still saves the transcript to history and plays the error sound.
- Streaming waits for the server's completed event with no fixed deadline. Keepalive pings (20 s interval and timeout) detect a dead connection, and the batch fallback runs only on an error event or a close before completion.
  - Rejected: the old fixed completion timeout. When a long utterance was still being transcribed, it returned the partial or empty text as final and skipped the batch fallback, so the words were lost silently.
- Batch has no retry loop of its own. The SDK's retries were stacking with Vox's three attempts, which could send up to nine requests for one dictation. Errors the SDK does not retry fail at once, and no read timeout was shortened.

**9. Not doing:** see Non-Goals.

### Orchestrator decisions for wave 2 (DECISIONS2.md)

**1. Band-limited resampling, numpy only.** Every downsampling path low-passes with a windowed-sinc FIR before it decimates: the 16 kHz upload, VAD, whisper.cpp input and the 24 kHz stream.
- Whole-number ratios (48 to 16 kHz, 48 to 24 kHz) use a polyphase or decimation path. 44.1 kHz low-passes, then interpolates.
- The streaming resampler keeps filter state between chunks, so chunk boundaries have no discontinuity. Long batch recordings are processed in blocks, so memory stays bounded.
- Rejected: scipy. It is a large dependency that also needs wheels on every platform `lock.sh --check` covers.
- Rejected: keeping every Nth sample. That aliases everything above the new Nyquist frequency into the speech band.

**2. A config error no longer crash-loops.** If `config.toml` fails to parse or validate at startup, Vox logs the error, starts on defaults with `config_error` set, refuses to record while it is set, and lets the reloader clear it once the file loads. Exit code 78 is kept for failures that really are permanent: missing system tools and an unusable lock directory.
- Rejected: exiting 78, which was wave 1's choice. systemd would then stop the service, and the user would have to start it again by hand after the fix. On macOS, launchd has no `RestartPreventExitStatus`, so it would restart Vox every 10 s.
- Rejected: recording with the defaults. The user's real settings are unknown. If they chose local-only whisper.cpp, recording on the defaults would send their audio to OpenAI.
- Rejected: coercing bad values, such as `"0.5"` to 0.5 or 50 to 1.0. Coercion hides mistakes, and wave 1 chose rejection.
- The status line reports the key problem, then the settings problem, then the mode problem, then a notice.

**3. Custom sounds come from `~/.config/vox/sounds/<name>.wav`,** and the repository's empty `sounds/` directory is deleted.
- Rejected: WAVs next to the package. They worked only from a macOS development checkout, and users could not edit them.
- Rejected: shipping WAVs as package data. Users could not replace them.

**4. History records the provider that produced the text.** A streaming recording that fell back is stored as "batch".
- Rejected: the mode the recording started in, which was wave 1's choice. It misstates how the audio was sent.

**5. The macOS log is cleared at startup once it passes 10 MiB,** only when stderr is a regular file, and Vox logs one INFO line when it does. journald on Linux is untouched.
- Rejected: Python log rotation. launchd, not Python's logging, owns that file descriptor.
- Rejected: an unbounded log. launchd never rotates it.

**6. Device rescans.** Vox rescans on returning to IDLE, and on macOS also every 30 s while idle. There, re-initialising PortAudio costs about 1 ms. Linux keeps only the return-to-IDLE rescan: re-initialising costs about 45 ms there, and the PipeWire or PulseAudio `default` device already follows hotplugged devices.
- The daemon owns PortAudio and re-initialises it only while no stream is open. The tray renders only the snapshot it is sent.
- A hotkey press drops a pending rescan and waits at most for one that is running.
- Rejected: querying devices when the menu opens. That blocks the UI thread, sees a stale list until PortAudio restarts, and a restart would close an open stream.

**7. A split upload that fails partway** saves the transcribed parts to history, plays the error sound, and shows "Last dictation only partly transcribed: see History" until the next successful dictation.
- Rejected: pasting the partial text. The user might not notice the missing end.
- Rejected: discarding it. Those parts are already billed.

**8. `mode_error` keeps wave 1's behavior.** Each toggle retries building the transcriber, so fixing the whisper.cpp setup needs no restart and no menu visit.

**9. Dead API removed:** the `WhisperTranscriber` alias, `SoundPlayer.play(blocking=)`, and `Config.styles` with its reload.
- `[styles]` had no effect since LLM post-processing was removed, and a table that is no longer read is simply ignored.

**10. Windows start with `python -P -m vox.ui.<window>`,** so a `vox/` package planted in the daemon's working directory can never be imported. The AT-SPI helper already starts this way.

**11. Mode rules live in `vox.modes`,** as `LABELS` and `mode_problem`. The tray's enabled state, its labels, the daemon's mode switch and the startup check all use them. The tray and the daemon had duplicate rules, and those had drifted apart.

**12. Out of scope:** HTTP keep-alive, a resident whisper-server, and a pre-upload delay for double-tap cancel.

### Technical choices made while implementing (wave 1)

- **The instance lock is on a per-user directory, taken with `flock`.** It is `$XDG_RUNTIME_DIR/vox`, `/run/user/<uid>/vox` or `~/.local/state/vox`.
  - Rejected: a file in `/tmp`. A tmp cleaner could delete it and admit a second instance, and all users on a machine shared it.
  - A second instance logs at INFO and exits 0, so the service manager does not retry it.
- **The daemon is a small state-machine class with one session per recording.** Hotkey, recorder and tray events arrive on a single queue. Late events (`limit`, `process_done`) are matched to the session they belong to, and cleanup (volume, microphone, stream and capture) runs on every exit path, including quitting mid-recording.
- **History uses `PRAGMA secure_delete=ON`,** and search goes through a `casefold` SQL function with `instr`, which matches literally and folds non-ASCII case.
  - Rejected: `LIKE`, which folds only ASCII and treats `%` and `_` as wildcards.
- **An `OPENAI_API_KEY` override is moved out of `os.environ` into a module variable at startup,** before Vox starts any process. Window processes get only a flag saying that an override is in effect.
- **Echo guard:** a transcript counts as an echo of the hints only if it repeats at least 70% of them. Before, dictating a few on-screen names was sometimes dropped as a hallucination.
- **Updates swap virtualenvs.** The installer moves the working virtualenv aside, builds the new one where the old one was, smoke-tests it, and restores the old one on any failure or interrupt (EXIT, INT and TERM traps). The next run recovers from an update that was interrupted.
- **`requirements.lock` is generated from `uv.lock` by `scripts/lock.sh`, with hashes and without evdev.** evdev ships no wheels, and Vox never uses pynput's uinput backend, which is the only thing that imports it. Vox itself is built with the locked setuptools from a `build` dependency group.
- **Spec organization:** the per-user installation requirement moves out of `desktop-frontend` into the new `release-packaging` capability, under the same name. Installing now spans the release bootstrap, uninstall, the `.deb` and CI, so one capability owns all of it.

## Risks / Trade-offs

- [X11 restores a single clipboard target] Rich text copied from a browser comes back as plain text. → Documented in the README. The lossless python-xlib selection owner is left to the owner.
- [macOS restore delay] An app slower than 0.5 s pastes the old clipboard. → The restore is skipped if the clipboard changed meanwhile. The constant is internal, and a setting would be the owner's call.
- [Password-manager clipboards] Restoring a clipboard marked `ConcealedType` puts back the same bytes and markers, but the new `changeCount` can stop the password manager's auto-clear. → Open question below.
- [Secret filter limits] Short secrets and unfamiliar formats can pass. Long identifiers that contain digits are dropped as hints even when harmless. → `[context] screen = false` sends dictionary words only. The filter drops rather than keeps when unsure.
- [Denied microphone in streaming] All-zero audio is streamed live before the stop-time check abandons the session without a commit. Whether Realtime bills streamed input without a commit is not verified. → The session is abandoned without a commit. Checking permission up front would need the AVFoundation dependency that decision 9 rules out.
- [Double-tap during a batch recording] The first tap starts processing, so audio may be uploaded before the cancel arrives. → An owner decision (Non-Goals). Local mode is not affected.
- [Starting on defaults after a config error] The hotkey from the defaults stays active until a restart, and `[hotkey]` edits need a restart anyway. → While the error is set, Vox never records. The status line names the problem.
- [Screen capture wait] A batch dictation can wait up to 1.5 s after stop for OCR. → Capture is now one window with language correction off, which is much faster. Shortening the wait is an open question.
- [Resampling cost] FIR filtering costs more CPU than taking every Nth sample. → The polyphase path for whole-number ratios, and block-wise processing for long recordings.
- [macOS interpreter changes] A new uv Python patch voids the macOS permission grants. → The installer pins the exact patch, and warns and explains when it changes.
- [Tooling drift] The lock file is revision 3 and needs uv 0.12.x, and CI assumes a public repository for free macOS and arm64 runners. → The uv version is pinned in both workflows and documented. If the repository stays private, the owner checks runner availability before tagging.
- [Unverified Linux paths] GTK windows, xclip timing, AT-SPI state filtering and the `.deb` maintainer scripts need a real X11 session or container. → G6 verifies them under Xvfb on the Linux test PC and in a clean Ubuntu 24.04 container. CI runs the install and `.deb` smoke tests on every pull request.

## Migration Plan

1. Merge the wave-2 branches (`prod2/G1` to `prod2/G5`) into `production-ready`. Run `uv run --frozen pytest -q` and `uv run --frozen ruff check vox tests` on macOS, the Linux suite under Xvfb (without GTK first, then GTK in its own process), and `openspec validate production-release --strict`. Then apply G6's Linux findings.
2. The owner accepts the uv 0.12.19 lock, enables private vulnerability reporting, sets the real date in `CHANGELOG.md`, makes the repository public, and pushes tag `v1.0.0`. The release workflow publishes the tarball, both `.deb` files, `install.sh` and `SHA256SUMS`.
3. For existing installs, run the new `install.sh` (or `curl … | bash` once the release exists). On first start, Vox tightens the history database's permissions. The installer writes the new LaunchAgent and deletes the old `/tmp` logs the user owns. Custom sounds must be copied to `~/.config/vox/sounds/`.
4. Rollback: a failed update keeps the previous virtualenv automatically. To go back a version, run the previous release's `install.sh`, or `apt install` the previous `.deb`. The history schema and `config.toml` format are unchanged, except that `[styles]` is ignored.
5. After the merge is verified, archive this change so that its deltas become the main specs.

## Open Questions

These are owner questions whose answers change neither these specs nor the approach. Each would become its own follow-up change.
- HTTP keep-alive for the OpenAI client (performance-6). A custom `http_client` would need a chosen expiry.
- Whether to leave the clipboard empty after pasting over a concealed or transient clipboard from a password manager, instead of restoring it.
- Whether to shorten the 1.5 s screen-capture wait, and whether the 0.5 s macOS restore delay should be tunable.
- On Linux without a keyring, whether to move a `config.toml` `[api] openai_api_key` into the 0600 fallback file (platform-bugs-16). This would change `api-key`, "Moving Plain-Text Keys".
- Whether the `limit:<seconds>` tray event should accept only `RECORDING_LIMIT_CHOICES`. Today it accepts any positive integer, so hand-edited values keep working.
- Whether deleting a single history entry should ask for confirmation, whether the 200-row cap should become paging, and whether the vocabulary window should keep a selection after removing words.
- On a macOS update, whether to keep the previous interpreter while it exists, and whether to build a signed `.app` after 1.0. Also whether to map package names for dnf, pacman and zypper.
- Confirming the new default window classes for Linux terminals and Telegram (the WM_CLASS strings are unverified), and that Telegram on macOS now counts as Chat.
