#!/usr/bin/env bash
set -uo pipefail

for plugin in \
  gtk4paintablesink \
  gtkwaylandsink \
  gtksink \
  gtkglsink \
  qml6glsink \
  glimagesink \
  waylandsink \
  cairooverlay \
  textoverlay
do
  printf '===== %s =====\n' "$plugin"
  gst-inspect-1.0 "$plugin" 2>&1 || true
done

python3 - <<'PY'
mods = ["gi", "PySide6", "PyQt6", "pygame"]
for name in mods:
    try:
        mod = __import__(name)
        print(name, "available", getattr(mod, "__version__", ""))
    except Exception as exc:
        print(name, "unavailable", type(exc).__name__, str(exc))
PY

printf '%s\n' '===== GI namespaces ====='
python3 - <<'PY'
try:
    import gi
    for namespace, version in (("Gtk", "3.0"), ("Gst", "1.0")):
        try:
            gi.require_version(namespace, version)
            module = __import__(f"gi.repository.{namespace}", fromlist=[namespace])
            print(namespace, version, "available", getattr(module, "MAJOR_VERSION", ""))
        except Exception as exc:
            print(namespace, version, "unavailable", type(exc).__name__, str(exc))
except Exception as exc:
    print("gi unavailable", type(exc).__name__, str(exc))
PY

python3 - <<'PY'
try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    print("Gtk", "4.0", "available", Gtk.MAJOR_VERSION)
except Exception as exc:
    print("Gtk", "4.0", "unavailable", type(exc).__name__, str(exc))
PY

printf '%s\n' '===== project venv GI ====='
if [[ -x .venv/bin/python ]]; then
  .venv/bin/python - <<'PY'
try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gst", "1.0")
    from gi.repository import Gtk, Gst
    print("venv gi/Gtk3/Gst available", Gtk.get_major_version(), Gst.version_string())
except Exception as exc:
    print("venv gi/Gtk3/Gst unavailable", type(exc).__name__, str(exc))
PY
else
  printf '%s\n' 'project venv unavailable'
fi
