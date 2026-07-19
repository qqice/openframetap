from openframetap.app.capabilities import parse_capability_audit


AUDIT = """===== gtkwaylandsink =====
Factory Details:
  Long-name Gtk Wayland Video Sink
===== gtksink =====
No such element or plugin 'gtksink'
===== waylandsink =====
Factory Details:
  Long-name wayland video sink
gi available 3.48.2
PySide6 unavailable ModuleNotFoundError
PyQt6 unavailable ModuleNotFoundError
pygame unavailable ModuleNotFoundError
Gtk 3.0 available 3
Gtk 4.0 available 4
venv gi/Gtk3/Gst unavailable ModuleNotFoundError
"""


def test_gui_capability_parser_selects_gtk_wayland() -> None:
    parsed = parse_capability_audit(AUDIT)
    assert parsed.plugins["gtkwaylandsink"]
    assert not parsed.plugins["gtksink"]
    assert parsed.python_modules["gi"]
    assert parsed.gtk3 and parsed.gtk4
    assert not parsed.venv_gi_gtk3_gst
    assert parsed.selected_path == "gtk3-gtkwaylandsink"


def test_gui_capability_parser_falls_back_without_gtk_sink() -> None:
    parsed = parse_capability_audit(AUDIT.replace("Factory Details:\n  Long-name Gtk Wayland Video Sink", "No such element or plugin 'gtkwaylandsink'"))
    assert parsed.selected_path == "gtk3-waylandsink-video-overlay"

