## Context

`vox` operates as an `asyncio` event loop daemon orchestrating audio recording, VAD speech detection, WebSocket streaming transcription, and simulated keyboard paste. Currently, it has no desktop GUI, running headlessly in the background.

See `proposal.md` for motivation.

## Goals / Non-Goals

**Goals:**
- Provide real-time visual tray feedback (Idle vs. Recording) with negligible CPU and RAM (<15MB added footprint).
- Support audio input device selection and pause/mute toggling directly from the tray context menu without restarting the daemon.
- Maintain an append-only, searchable local SQLite database of dictation history (`~/.local/share/vox/history.db`).
- Provide an on-demand, fast-opening popover or window for searching past dictations with 1-click clipboard copy.
- Provide a visual manager for custom vocabulary (`config.dictionary`) and text expansion snippets (`config.snippets`), persisting to `config.toml`.
- Run smoothly on both macOS (menu bar) and Linux (AppIndicator / StatusNotifierItem tray), and keep any GUI-less session running headless without regressions.
- Install with one self-contained per-user script on both platforms.

**Non-Goals:**
- Floating HUD pill or transparent on-screen overlay (explicitly excluded).
- Hold-to-talk key release mechanics (retains toggle-only mode).
- Heavy Chromium/Electron webview processes.
- AI command mode or secondary LLM processing.
- Native distro packages (.deb, .app bundles) and non-apt package managers; the installer names the missing packages instead.

## Decisions

### 1. System Tray Framework: `pystray` with Dynamic `Pillow` Icons
- **Choice**: Use `pystray` and `Pillow` to draw and display procedural icons. The glyph is a microphone whose cradle is half a cog, a nod to the Warhammer 40k vox that names the app. Idle and paused are monochrome template images; recording and transcribing light the capsule's grille slots red or amber on a neutral grey body. The launcher icon uses the same glyph in brass and bone on a gunmetal plate.
- **Rationale**: `pystray` bridges natively to macOS `NSStatusItem` and Linux `AppIndicator` / `StatusNotifierItem` through a single cross-platform API. Dynamic Pillow generation avoids shipping separate static asset packs for different desktop themes and resolutions.
- **Alternative Considered**: Writing custom PyObjC `NSStatusItem` for macOS and PyGObject for Linux. Rejected to avoid duplicating tray menu management, microphone device enumeration, and event handling logic across platforms.

### 2. Concurrency Architecture: Thread-Isolated Tray with Asyncio Callbacks
- **Choice**: Cocoa requires `NSStatusItem` work on the main thread and GTK wants its loop on the thread that initialised it, so on both platforms the `pystray.Icon` loop (`TrayManager`) owns the main thread and the `asyncio` daemon runs in a dedicated `vox-daemon` thread. Tray menu actions reach the daemon via `loop.call_soon_threadsafe()`; daemon state changes reach the tray via `PyObjCTools.AppHelper.callAfter()` on macOS and `GLib.idle_add()` on Linux. Without a tray, `asyncio.run()` stays on the main thread exactly as before.
- **Rationale**: GUI/tray loops require OS-level event polling (e.g. Cocoa `CFRunLoop` on macOS). Running the tray in a separate thread prevents it from stalling high-priority audio capture or WebSocket streaming pipelines.
- **Alternative Considered**: Running tray inside the asyncio loop via periodic non-blocking ticks. Rejected because platform tray event loops frequently block during menu open/hover states, which would cause audio buffer drops.

### 3. History Storage: Built-in `sqlite3`
- **Choice**: Persist dictations into a dedicated SQLite database located at `~/.local/share/vox/history.db` (creating parent directories if needed). Schema:
  ```sql
  CREATE TABLE IF NOT EXISTS history (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      text TEXT NOT NULL,
      app_type TEXT,
      duration_seconds REAL,
      transcription_mode TEXT
  );
  CREATE INDEX IF NOT EXISTS idx_history_created_at ON history(created_at DESC);
  ```
- **Rationale**: Zero third-party dependencies, ACID transactions, negligible disk footprint, and sub-millisecond full-text or prefix filtering.
- **Alternative Considered**: Plain-text JSONL log. Rejected because JSONL lacks indexing, requires full-file parsing on search, and risks file corruption on abrupt shutdowns.

### 4. History & Settings UI: On-Demand Lightweight Popover
- **Choice**: Spawn the History Search and Vocabulary/Snippet Editor window only when invoked via tray menu click or secondary hotkey, destroying or hiding it when closed.
- **Rationale**: Keeps Vox's idle memory footprint under 30MB total. When the window is closed, zero CPU or rendering resources are consumed.
- **Implementation**: Each window runs as its own process (`python -m vox.ui.history_window` / `vox.ui.vocab_window`), so the daemon stays light and closing a window frees all its memory. A toolkit-independent model (`vox.ui.history_model`, `vox.ui.vocab_model`) holds the logic. Thin native views sit on top: AppKit through PyObjC on macOS (`vox.ui.mac`: a sidebar split view for history, a Settings-style tabbed window for vocabulary) and GTK 4 with libadwaita 1.5 on Linux (`vox.ui.gtk`: an adaptive navigation split view, a view-switched preferences window, and undo toasts). Each follows its platform's conventions, so a snippet delete asks for confirmation on macOS and offers Undo on Linux. The history window reads `history.db` directly and picks up new dictations while open. The vocabulary window writes `config.toml`, which the daemon hot-reloads.
- **Focus**: Since macOS 14, a freshly spawned process can't bring itself to the front. The tray, which the user just clicked, therefore activates itself and hands activation to the window process once it has finished launching. On Linux each window is a single-instance `Adw.Application`, so opening it again presents the open window.

### 5. Configuration Mutation and Hot-Reload Integration
- **Choice**: Implement helper routines in `vox.config` that parse and write back dictionary items and snippet key-value pairs to `~/.config/vox/config.toml` using `tomlkit`, which round-trips the file and preserves the user's comments and formatting.
- **Transcription selection**: The tray sends a mode event to the daemon. While idle, the daemon checks that the API key or local CLI and model are available, updates its active transcriber, and writes `[transcription].mode` with the same atomic TOML writer. The tray shows the selected mode and disables unavailable choices.
- **Rationale**: Vox already includes an automatic mtime-polling config reloader (`_config_reloader` in [`vox/daemon.py`](file:///Users/alex/Developer/vox/vox/daemon.py#L268)). Writing valid TOML back to disk causes immediate hot-reloading into the running daemon without restart.

### 6. Linux Tray: pystray's AppIndicator Backend on the System PyGObject
- **Choice**: On Linux, pystray's `_appindicator` backend exports a StatusNotifierItem with a DBusMenu, so the same `TrayManager` menu works unchanged. It needs PyGObject (`python3-gi`) and the Ayatana AppIndicator typelib, which ship with Debian/Ubuntu desktops. The venv is created from the system `python3` and gets only the system `gi` package symlinked in; everything else comes from pip. Idle and paused glyphs are drawn light because Linux trays have no template images and are usually dark.
- **Rationale**: This is the smallest change that reuses existing, maintained code: no new tray implementation and no new Python dependency.
- **Alternatives Considered**: PyGObject from pip (no wheels; needs a compiler plus GObject/cairo dev headers). A `--system-site-packages` venv (leaks every system and `~/.local` package into Vox and makes pip report unrelated conflicts). A custom StatusNotifierItem over `dbus-fast` (fully pip-installable but roughly 600 lines of protocol code, and no maintained library exists). Qt (`PySide6`, ~80MB).

### 7. Installer: `install.sh`
- **Choice**: One per-user script that installs Vox non-editable into `~/.local/share/vox/venv`, links `~/.local/bin/vox`, and re-runs as an update.
  - **Linux**: probes for and `apt-get install`s only the missing packages (`xdotool`, `xclip`, `libportaudio2`, `python3-venv`, `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`, `gir1.2-ayatanaappindicator3-0.1`). On GNOME without a tray host it asks GNOME Shell to install the "AppIndicator and KStatusNotifierItem Support" extension (`org.gnome.Shell.Extensions.InstallRemoteExtension`). That is GNOME's own consent dialog; it needs no sudo and loads without re-login. It then installs the systemd user unit, bound to `graphical-session.target`.
  - **macOS**: uses `uv` with a uv-managed Python, then loads the LaunchAgent.
  - **Both**: the API key moves to `~/.config/vox/.env`, from `OPENAI_API_KEY` or this checkout's `.env`, because the installed copy no longer sees the checkout. The login service is only enabled once a key exists.
  - **Launcher**: a `Vox.app` in `/Applications`, which admin accounts can write without sudo, or `~/Applications` otherwise (macOS) or a `vox.desktop` entry (Linux) starts Vox again after Quit. It starts the login service (`launchctl kickstart`, bootstrapping the agent if it was unloaded; `systemctl --user start vox`) instead of running `vox`. Vox exits with status 0 when another instance holds its lock, before any permission prompt.
- **Rationale**: The stdlib `venv` from uv's older standalone Python builds produces a broken environment (its prefix resolves to `/install`), so `uv` is the reliable macOS route. GNOME 46's extensions D-Bus helper exits after 2 s idle and can drop the install reply while the dialog is open, so the installer waits for the tray host to appear instead of trusting the reply. Going through the service manager makes the launcher a no-op while Vox runs, and on macOS keeps launchd as the launching process, so the Microphone, Accessibility and Input Monitoring grants made at login still apply. Quit exits 0, which neither `KeepAlive.SuccessfulExit=false` nor `Restart=on-failure` restarts, and a second instance now exits 0 too, so neither service manager retries it in a loop.
- **Alternatives Considered**: A py2app bundle registered with `SMAppService` (needs a build step and code signing). A Linux `~/.config/autostart` entry (no crash restart and no journal).

## Risks / Trade-offs

- **[Risk]** macOS main-thread UI constraints: Cocoa tray interactions require the Cocoa event loop on the main thread.
  - *Mitigation*: The tray runs on the main thread and the asyncio loop in a secondary thread (Decision 2). All AppKit calls from the daemon are marshalled with `AppHelper.callAfter()`.
- **[Risk]** No GUI session (SSH, CI): creating an `NSStatusItem` without a WindowServer connection can abort the process, and GTK cannot start without a display.
  - *Mitigation*: Check for a GUI session (`CGSessionCopyCurrentDictionary` on macOS, `DISPLAY`/`WAYLAND_DISPLAY` on Linux) and a pystray backend with menu support before creating the tray; otherwise log a notice and run headless.
- **[Risk]** No tray host on Linux: stock GNOME shows no StatusNotifierItems, and minimal Wayland compositors (e.g. bare Sway or Hyprland) need a bar such as Waybar.
  - *Mitigation*: The installer enables the GNOME AppIndicator extension. Vox still registers its item, logs a notice when no `org.kde.StatusNotifierWatcher` is on the bus, and the icon appears once a host starts.
- **[Risk]** Light panels: the light Linux idle glyph is faint on a light panel theme.
  - *Mitigation*: Accepted; recording and processing states stay colored and readable on any panel.
- **[Risk]** Linux needs a system Python ≥ 3.12 (Ubuntu 24.04+, Debian 13+) because PyGObject is tied to it.
  - *Mitigation*: The installer checks the version and stops with a clear message.
- **[Risk]** pystray's Linux backend always closes its desktop notification on exit, which raises without a notification server and would make Quit exit non-zero (so systemd would restart Vox).
  - *Mitigation*: A small Linux icon subclass logs and ignores that cleanup error; open windows close in a `finally`.
- **[Risk]** Concurrent writes to `config.toml`: User editing TOML manually while modifying via the UI.
  - *Mitigation*: Use atomic file writes (`tempfile` + `os.replace`) to prevent corruption.
