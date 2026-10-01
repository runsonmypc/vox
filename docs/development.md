# Development

[Back to the README](../README.md)

```sh
uv sync                      # Python 3.12 virtualenv with the dev tools
uv run vox -v                # run from the checkout (quit the installed Vox Transfer first: one instance at a time)
uv run pytest                # tests
uv run ruff check vox tests  # lint
```

On Linux, the GTK window tests need GTK 4, libadwaita and a display, and must run in their own
process: `dbus-run-session -- xvfb-run -a uv run pytest -m gtk`, then `uv run pytest -m "not gtk"`.
The virtualenv also needs the system's PyGObject, for example
`ln -s /usr/lib/python3/dist-packages/gi .venv/lib/python3.12/site-packages/`.

## Dependencies

Dependencies are locked in `uv.lock`. After changing them, run `uv lock` and `scripts/lock.sh`,
which regenerates `requirements.lock`, the hash-pinned list the installer and the `.deb` install
from. `scripts/lock.sh --check`, which CI runs, fails when the two disagree or when a locked package
has no wheel for Linux (x86_64, arm64) or macOS with Python 3.12 or 3.13, since installing must never
need a compiler. Lock with the uv version CI pins (`UV_VERSION` in `.github/workflows/ci.yml`);
another version may write a different lock. [RELEASING.md](../RELEASING.md#relock) shows how to run
that version without installing it.

## Testing the installer and the package

CI also runs the installer and the package in clean Ubuntu 24.04 containers, which you can do with
Docker from the checkout:

```sh
docker run --rm -v "$PWD:/src:ro" ubuntu:24.04 /src/scripts/test-install-linux.sh
# on Ubuntu 24.04 with uv and sudo, build the package, then install, upgrade and remove it:
packaging/deb/build-deb.sh 1.0.0 amd64
docker run --rm -v "$PWD:/src:ro" -w /src ubuntu:24.04 packaging/deb/smoke-test.sh dist/vox_1.0.0_amd64.deb
```

## Releasing

[RELEASING.md](../RELEASING.md) describes how to make a release and how to try the release workflow
without publishing anything.

## Native recovery integration

`scripts/integration/recovery_desktop.py` opens real History and text-target windows in
separate processes and runs the production daemon loop, control socket, recording store,
provider converters, focus detection, clipboard and paste keystrokes. Microphone, hotkey
and sound hardware are replaced; a controlled whisper CLI and a loopback HTTP endpoint
supply provider results. The script uses a temporary database and audio directory and
never contacts OpenAI. It checks Local and Batch retry, daemon restart, clipboard
restoration, History reopening, switching target apps, cancellation while paused,
Delete/Clear, partial-attempt copying, live opt-out and GTK's narrow layout.

On macOS, run in a logged-in desktop with Accessibility permission for Python:

```sh
uv run python scripts/integration/recovery_desktop.py --report /tmp/vox-desktop-macos.json
```

The test briefly changes focus and clipboard contents. It uses dedicated target windows,
then restores the original foreground application and all original clipboard types.

On Linux, CI runs the same test under Openbox and Xvfb. A disposable Ubuntu environment
can also run it from macOS or Linux:

```sh
integration_build_dir=$(mktemp -d)
cp requirements.lock scripts/integration/Dockerfile "$integration_build_dir/"
docker build -t vox-recovery-desktop "$integration_build_dir"
docker run --rm --network none -v "$PWD:/src:ro" vox-recovery-desktop
```

The image installs the hash-pinned runtime dependencies and GTK 4, libadwaita, Openbox,
Xvfb, xdotool and xclip. Window activation and paste events go through the real X11
server; no focus or injector functions are mocked. The Linux integration is also a
required step in `.github/workflows/ci.yml`.

This is automated native desktop validation. Buttons are activated programmatically,
synthetic audio bypasses VAD, partial attempts are seeded, and the Clear confirmation
is accepted by the fixture. It tests desktop recovery behavior; actual microphone
capture, provider accuracy and human mouse interaction require separate validation.

## Recording overlay validation

The controller and geometry tests run without AppKit. `tests/test_overlay_mac.py` builds and
draws the real panel when a macOS GUI session is available. To exercise dedicated text targets,
focus/paste/clipboard restoration, actual guarded screenshots and optional microphone levels:

```sh
uv run python scripts/integration/overlay_desktop.py --output /tmp/vox-overlay-validation --microphone
```

This requires existing Accessibility and Screen Recording permissions. It briefly changes
focus and clipboard contents, restores them afterward, and saves synthetic target screenshots,
panel previews and a JSON report. The two-second microphone sample is discarded locally.
Provider responses, human quiet/loud speech, physical hotkeys, editor/terminal applications,
full-screen Spaces and multiple physical displays need separate checks; the report identifies
what was unavailable. The overlay uses the tray's main loop and stops drawing while hidden.

The X11 backend has native GTK 3 tests in `tests/test_overlay_x11.py`; keep these in the
non-`gtk` pytest process because Settings uses GTK 4. Its runtime needs distro packages
`python3-gi-cairo` and `gir1.2-gtk-3.0`, with both `gi` and `cairo` linked into the venv.
CI also runs a real Openbox desktop check with Mousepad and XTerm (with Ctrl+Shift+V
paste configured):

```sh
docker build -t vox-overlay-desktop -f scripts/integration/Dockerfile .
mkdir -p /tmp/vox-overlay-validation
docker run --rm --network none -v "$PWD:/src:ro" \
  -v /tmp/vox-overlay-validation:/evidence vox-overlay-desktop \
  dbus-run-session -- xvfb-run -a -s '-screen 0 1280x900x24' \
  python scripts/integration/overlay_x11.py --output /evidence
```

This saves actual desktop screenshots using controlled waveform levels, verifies production
paste and clipboard restoration, clicks through the panel, and compares visible-panel pixels
with guarded captures and checks OCR while the panel overlaps the target. It checks GTK reduced motion,
restart/cancel and resource cleanup. No microphone or transcription provider is used.
Physical mixed-DPI displays and compositor-dependent opacity still need desktop checks.

For equal-scale native visual previews without microphone or paste activity, run
`python scripts/integration/overlay_preview.py --output /tmp/vox-overlay-previews` with the
project environment on macOS, or under `dbus-run-session -- xvfb-run -a` on Linux. It renders
Listening and both transcription states as 448 × 88 PNGs through the production native views.
