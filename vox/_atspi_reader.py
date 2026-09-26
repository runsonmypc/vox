"""AT-SPI text reader — runs as subprocess to isolate potential segfaults.

Reads only the application's focused window, and only what is showing in it.
"""

import sys


def main() -> None:
    pid = sys.argv[1]
    max_chars = int(sys.argv[2]) if len(sys.argv) > 2 else 2000

    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
    Atspi.init()

    desktop = Atspi.get_desktop(0)
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is None:
            continue
        try:
            if str(app.get_process_id()) != pid:
                continue
            window = _active_window(app, Atspi)
        except Exception:
            continue
        if window is None:
            return
        chunks: list[str] = []
        _collect_text(window, chunks, 0, Atspi)
        print("\n".join(chunks)[:max_chars], end="")
        return


def _active_window(app, atspi):
    """The app's focused top-level window, so its other windows are never read."""
    for i in range(app.get_child_count()):
        window = app.get_child_at_index(i)
        if window is not None and window.get_state_set().contains(atspi.StateType.ACTIVE):
            return window
    return None


def _collect_text(obj, chunks: list[str], depth: int, atspi) -> None:
    if depth > 20 or len(chunks) > 200:
        return
    try:
        n = obj.get_child_count()
    except Exception:
        return
    for i in range(n):
        try:
            child = obj.get_child_at_index(i)
            # Skip hidden subtrees: background tabs, collapsed panels, off-screen pages
            if child is None or not child.get_state_set().contains(atspi.StateType.SHOWING):
                continue
            if "Text" in child.get_interfaces():
                cc = atspi.Text.get_character_count(child)
                if cc > 0:
                    text = atspi.Text.get_text(child, 0, min(cc, 500))
                    cleaned = text.strip().replace("\ufffc", "").replace("\ufffd", "").strip()
                    if cleaned:
                        chunks.append(cleaned)
            _collect_text(child, chunks, depth + 1, atspi)
        except Exception:
            continue


if __name__ == "__main__":
    main()
