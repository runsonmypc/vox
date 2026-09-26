"""Shared GTK 4 and libadwaita setup for the Linux windows."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

APP_ICONS = {
    "TERMINAL": "utilities-terminal-symbolic",
    "EDITOR": "text-editor-symbolic",
    "CHAT": "chat-message-new-symbolic",
    "EMAIL": "mail-unread-symbolic",
    "BROWSER": "web-browser-symbolic",
}
DEFAULT_APP_ICON = "audio-input-microphone-symbolic"

_CSS = """
.history-row { padding: 6px 4px; }
.history-row .row-copy { opacity: 0; transition: opacity 120ms ease-out; }
.history-row:hover .row-copy, .history-row:selected .row-copy { opacity: 1; }
.day-header { padding: 14px 12px 4px 12px; }
.reading { font-size: 1.1em; line-height: 1.55; }
.snippet-text { padding: 10px 12px; background: none; }
.snippet-box { border-radius: 12px; }
"""

_ready = False


def setup() -> None:
    """Initialise libadwaita and Vox's few style tweaks. Safe to call more than once."""
    global _ready
    if _ready:
        return
    GLib.set_prgname("vox")  # before GTK starts, so the window class names Vox rather than python3
    GLib.set_application_name("Vox")
    Adw.init()
    provider = Gtk.CssProvider()
    provider.load_from_string(_CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _ready = True


def uses_24_hour_clock() -> bool:
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup("org.gnome.desktop.interface", True) is None:
        return True
    return Gio.Settings.new("org.gnome.desktop.interface").get_string("clock-format") == "24h"


def run_app(app_id: str, make_window) -> None:
    """Run a single-instance app: launching it again brings the open window forward."""
    setup()
    app = Adw.Application(application_id=app_id)

    def activate(app: Adw.Application) -> None:
        window = app.get_active_window() or make_window(app)
        window.present()

    app.connect("activate", activate)
    app.run([])


def error_dialog(parent: Gtk.Widget, heading: str, body: str) -> None:
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("ok", "OK")
    dialog.present(parent)


def label(text: str = "", *classes: str, xalign: float = 0, **props) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=xalign, **props)
    for css in classes:
        widget.add_css_class(css)
    return widget
