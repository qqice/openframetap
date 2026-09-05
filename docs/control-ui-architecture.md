# OpenFrameTap control UI architecture

## Verified ROCK 4D capability matrix

The 2026-07-19 read-only audit ran through `scripts/remote.sh` and made no package or display changes.

| Integration path | Verified availability | Copy/overlay implications | Touch and lifecycle | Decision |
|---|---|---|---|---|
| GTK widget + GStreamer sink | GTK 3/4 and PyGObject available; `gtkwaylandsink` 1.24.2 available with SystemMemory and DMABuf caps | MPP output can remain in GStreamer and negotiate DMABuf; Python never receives pixels | GTK owns the Wayland widget, focus, key, pointer, and touch events | **Selected: GTK3 + non-modal `Gtk.Popover` OSD + `gtkwaylandsink`** |
| Application-owned surface + `waylandsink` | `waylandsink` and `GstVideoOverlay` available | Preserves current MPP path and DMABuf | Extracting and coordinating a GTK Wayland native handle is more fragile; overlay widgets and lifecycle ordering are application responsibilities | Kept as fallback |
| GL sink/toolkit integration | `glimagesink` available; `gtkglsink`, `gtksink`, and `qml6glsink` absent | DMABuf/GLMemory possible, but would need explicit GL context/window integration | No installed toolkit-specific GL sink; greater context and shutdown complexity | Rejected for first prototype |
| GStreamer internal overlay | `cairooverlay` available; `textoverlay` absent | `cairooverlay` requires BGRx/BGRA/RGB16 and can force conversion/copy after MPP | Drawing is possible but interactive touch hit-testing still needs a separate UI surface | Rejected as default |
| `appsink` to Python GUI | Technically implementable | Pulls decoded pixels into Python and typically adds CPU copies/conversion | Straightforward widgets but violates the zero-copy preference until benchmarked | Last-resort fallback only |

## Selected composition

```text
RTMP -> flvdemux -> bounded leaky queue -> h264parse
     -> mppvideodec -> gtkwaylandsink (DMABuf when negotiated)
                                      |
Gtk.Window fullscreen -> video widget + anchored non-modal Gtk.Popover joystick
                      -> fixed status header and STOP / exit row
```

- 【实机事实】 `mppvideodec`, `gtkwaylandsink`, and its DMABuf caps are present on the fixed ROCK image.
- 【实机事实】 the project venv does not directly expose `gi`; the existing GStreamer player already adds the fixed system `dist-packages` directory at runtime. The app reuses that bounded loader and does not install or replace system PyGObject.
- 【捕获推断】 DMABuf negotiation avoids an unnecessary Python-visible frame copy. Runtime caps and instantiated element names must still be recorded in each app session before calling it confirmed zero-copy.

The GTK main thread only updates widgets from coalesced immutable state snapshots. BLE callbacks, media bus polling, and metrics collection publish state; they never perform expensive GUI work in a streaming callback. The control writer remains a separate single-writer task and is the only component permitted to reach FFF5 in future live mode.

## Live joystick composition and mapping

The live control surface uses a 340×340 logical-pixel `Gtk.DrawingArea` inside
a non-modal `Gtk.Popover` anchored to the bottom-left of the video widget. It
copies no video frames and exposes no fixed-offset slider. The status label and
STOP/exit buttons use a separate fixed-height row, so changing status text
cannot move the joystick or buttons.

A direct `Gtk.Overlay` composition was tested on the real Wayland session. The
application ran, but a desktop screenshot contained only the
`gtkwaylandsink` native surface: all ordinary GTK overlay children were below
that surface. The popover uses its own compositor-managed popup surface; the
next real screenshot showed the complete 340×340 joystick above the video
while the pipeline still negotiated NV12 through `mppvideodec` and
`gtkwaylandsink`. This is why the selected design uses a popover rather than
claiming that the direct overlay worked.

Touch displacement is normalized to a unit circle after a `0.12` deadzone.
The radial value is linear rather than cubic. Pocket 3 profile mapping is:

```text
center/deadzone       -> exact 1024/1024 center command
first nonzero demand -> radial protocol magnitude 32
unit-circle edge     -> radial protocol magnitude 188
between              -> 32 + demand * (188 - 32)
```

Diagonal input is vector-normalized before protocol conversion, so diagonal
motion cannot exceed the radial maximum. Keyboard input uses normalized demand
`0.41`, which maps close to the removed fixed default of 96 without giving a
debug key the full 188-unit range.

There is no arbitrary two-second hold limit. A held touch remains valid through
the GTK heartbeat. Touch-up/cancel, key-up, focus loss, STOP, exit, BLE or RTMP
loss, write failure, and a missing input heartbeat still produce center through
the single writer.

## Camera mode integration

The original GTK window now selects normal SoftAP/UDP or external-WLAN RTMP.
Both use its named MPP decoder, gtkwaylandsink and the same joystick controller.
Mode changes complete center/transport/network cleanup before the next session.
STOP retains video, and the explicit enable-control button re-arms from neutral.
See [normal-mode session](normal-mode-session.md#gui-integration-2026-09-06)
for physical mode-cycle evidence and the exact RTMP stop profile.
