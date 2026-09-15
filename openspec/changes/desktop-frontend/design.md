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
- Run smoothly on both macOS (menu bar) and Linux (Wayland/X11 system tray).

**Non-Goals:**
- Floating HUD pill or transparent on-screen overlay (explicitly excluded).
- Hold-to-talk key release mechanics (retains toggle-only mode).
- Heavy Chromium/Electron webview processes.
- AI command mode or secondary LLM processing.

## Decisions

### 1. System Tray Framework: `pystray` with Dynamic `Pillow` Icons
- **Choice**: Use `pystray` and `Pillow` to draw and display procedural monochrome (Idle) and highlighted/red (Recording) icons.
- **Rationale**: `pystray` bridges natively to macOS `NSStatusItem` and Linux `AppIndicator` / `StatusNotifierItem` through a single cross-platform API. Dynamic Pillow generation avoids shipping separate static asset packs for different desktop themes and resolutions.
- **Alternative Considered**: Writing custom PyObjC `NSStatusItem` for macOS and PyGObject for Linux. Rejected to avoid duplicating tray menu management, microphone device enumeration, and event handling logic across platforms.

### 2. Concurrency Architecture: Thread-Isolated Tray with Asyncio Callbacks
- **Choice**: Run the `pystray.Icon` main loop in a dedicated background daemon thread (`TrayManager`). Inter-thread communication uses `loop.call_soon_threadsafe()` to dispatch state changes to and from the primary `asyncio` loop.
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

### 5. Configuration Mutation and Hot-Reload Integration
- **Choice**: Implement helper routines in `vox.config` that parse and write back dictionary items and snippet key-value pairs to `~/.config/vox/config.toml`.
- **Rationale**: Vox already includes an automatic mtime-polling config reloader (`_config_reloader` in [`vox/daemon.py`](file:///Users/alex/Developer/vox/vox/daemon.py#L268)). Writing valid TOML back to disk causes immediate hot-reloading into the running daemon without restart.

## Risks / Trade-offs

- **[Risk]** macOS main-thread UI constraints: Certain Cocoa tray interactions on macOS require the Cocoa event loop on the main thread.
  - *Mitigation*: Run the tray on the main thread if on Darwin while spinning up the asyncio loop in a secondary thread, or use `pystray`'s built-in Darwin dispatch adapter.
- **[Risk]** Wayland system tray disparity: Some minimal Wayland compositors (e.g. bare Sway or Hyprland) require `sniproxy` or a StatusNotifierItem bar (like Waybar) to display tray icons.
  - *Mitigation*: Gracefully handle missing tray hosts without crashing the daemon; log an informational notice if no tray server is active.
- **[Risk]** Concurrent writes to `config.toml`: User editing TOML manually while modifying via the UI.
  - *Mitigation*: Use atomic file writes (`tempfile` + `os.replace`) to prevent corruption.
