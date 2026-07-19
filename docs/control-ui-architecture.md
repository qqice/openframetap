# OpenFrameTap control UI architecture

## Verified ROCK 4D capability matrix

The 2026-07-19 read-only audit ran through `scripts/remote.sh` and made no package or display changes.

| Integration path | Verified availability | Copy/overlay implications | Touch and lifecycle | Decision |
|---|---|---|---|---|
| GTK widget + GStreamer sink | GTK 3/4 and PyGObject available; `gtkwaylandsink` 1.24.2 available with SystemMemory and DMABuf caps | MPP output can remain in GStreamer and negotiate DMABuf; Python never receives pixels | GTK owns the Wayland widget, focus, key, pointer, and touch events | **Selected: GTK3 + `Gtk.Overlay` + `gtkwaylandsink`** |
| Application-owned surface + `waylandsink` | `waylandsink` and `GstVideoOverlay` available | Preserves current MPP path and DMABuf | Extracting and coordinating a GTK Wayland native handle is more fragile; overlay widgets and lifecycle ordering are application responsibilities | Kept as fallback |
| GL sink/toolkit integration | `glimagesink` available; `gtkglsink`, `gtksink`, and `qml6glsink` absent | DMABuf/GLMemory possible, but would need explicit GL context/window integration | No installed toolkit-specific GL sink; greater context and shutdown complexity | Rejected for first prototype |
| GStreamer internal overlay | `cairooverlay` available; `textoverlay` absent | `cairooverlay` requires BGRx/BGRA/RGB16 and can force conversion/copy after MPP | Drawing is possible but interactive touch hit-testing still needs a separate UI surface | Rejected as default |
| `appsink` to Python GUI | Technically implementable | Pulls decoded pixels into Python and typically adds CPU copies/conversion | Straightforward widgets but violates the zero-copy preference until benchmarked | Last-resort fallback only |

## Selected composition

```text
RTMP -> flvdemux -> bounded leaky queue -> h264parse
     -> mppvideodec -> gtkwaylandsink (DMABuf when negotiated)
                                      |
Gtk.Window fullscreen -> Gtk.Overlay -+-> video widget
                                      +-> status header
                                      +-> touch joystick / STOP / exit
```

- 【实机事实】 `mppvideodec`, `gtkwaylandsink`, and its DMABuf caps are present on the fixed ROCK image.
- 【实机事实】 the project venv does not directly expose `gi`; the existing GStreamer player already adds the fixed system `dist-packages` directory at runtime. The app reuses that bounded loader and does not install or replace system PyGObject.
- 【捕获推断】 DMABuf negotiation avoids an unnecessary Python-visible frame copy. Runtime caps and instantiated element names must still be recorded in each app session before calling it confirmed zero-copy.

The GTK main thread only updates widgets from coalesced immutable state snapshots. BLE callbacks, media bus polling, and metrics collection publish state; they never perform expensive GUI work in a streaming callback. The control writer remains a separate single-writer task and is the only component permitted to reach FFF5 in future live mode.
