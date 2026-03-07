"""AT-SPI text reader — runs as subprocess to isolate potential segfaults."""

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
            if str(app.get_process_id()) == pid:
                chunks: list[str] = []
                _collect_text(app, chunks, 0)
                text = "\n".join(chunks)[:max_chars]
                print(text, end="")
                return
        except Exception:
            continue


def _collect_text(obj: object, chunks: list[str], depth: int) -> None:
    if depth > 20 or len(chunks) > 200:
        return
    try:
        n = obj.get_child_count()
    except Exception:
        return
    for i in range(n):
        try:
            child = obj.get_child_at_index(i)
            if child is None:
                continue
            from gi.repository import Atspi
            ifaces = child.get_interfaces()
            if "Text" in ifaces:
                cc = Atspi.Text.get_character_count(child)
                if cc > 0:
                    text = Atspi.Text.get_text(child, 0, min(cc, 500))
                    cleaned = text.strip().replace("\ufffc", "").replace("\ufffd", "").strip()
                    if cleaned:
                        chunks.append(cleaned)
            _collect_text(child, chunks, depth + 1)
        except Exception:
            continue


if __name__ == "__main__":
    main()
