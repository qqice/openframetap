from __future__ import annotations

from dataclasses import asdict, dataclass
import re


PLUGIN_NAMES = (
    "gtk4paintablesink",
    "gtkwaylandsink",
    "gtksink",
    "gtkglsink",
    "qml6glsink",
    "glimagesink",
    "waylandsink",
    "cairooverlay",
    "textoverlay",
)


@dataclass(frozen=True, slots=True)
class GuiCapabilities:
    plugins: dict[str, bool]
    python_modules: dict[str, bool]
    gtk3: bool
    gtk4: bool
    venv_gi_gtk3_gst: bool

    @property
    def selected_path(self) -> str | None:
        if self.plugins.get("gtkwaylandsink") and self.gtk3:
            return "gtk3-gtkwaylandsink"
        if self.plugins.get("waylandsink") and self.gtk3:
            return "gtk3-waylandsink-video-overlay"
        return None

    def to_dict(self) -> dict:
        return asdict(self) | {"selected_path": self.selected_path}


def parse_capability_audit(text: str) -> GuiCapabilities:
    sections: dict[str, str] = {}
    matches = list(re.finditer(r"^===== ([^=]+?) =====$", text, re.MULTILINE))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1).strip()] = text[match.end() : end]
    plugins = {
        name: name in sections
        and "No such element or plugin" not in sections[name]
        and "Factory Details:" in sections[name]
        for name in PLUGIN_NAMES
    }
    modules = {}
    for name in ("gi", "PySide6", "PyQt6", "pygame"):
        match = re.search(rf"^{re.escape(name)} (available|unavailable)\b", text, re.MULTILINE)
        modules[name] = bool(match and match.group(1) == "available")
    return GuiCapabilities(
        plugins=plugins,
        python_modules=modules,
        gtk3=bool(re.search(r"^Gtk 3\.0 available\b", text, re.MULTILINE)),
        gtk4=bool(re.search(r"^Gtk 4\.0 available\b", text, re.MULTILINE)),
        venv_gi_gtk3_gst=bool(
            re.search(r"^venv gi/Gtk3/Gst available\b", text, re.MULTILINE)
        ),
    )

