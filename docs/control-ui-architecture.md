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

## Persistent window and stream lifecycle

The mode selector is a two-position sliding control: **常规** on the left and
**直播** on the right. A single `AppWindowHost` owns a full-screen transition
surface for the whole application lifetime. Its progress page remains visible during
control shutdown, WLAN rollback, BLE preparation and RTMP establishment. Those
blocking operations run off the GTK thread; the spinner, elapsed time and exit
button continue updating. The native video windows are recreated underneath the
cover and revealed when compressed video starts arriving. This preserves the
validated gtkwaylandsink native-surface lifecycle; trying to reparent/rebuild its
subsurfaces in one shared video window produced a Wayland protocol error during
testing. The transition surface stays mapped across cleanup and reconnection,
so the desktop is not exposed between video windows.

The displayed bitrate is measured by a lightweight buffer probe on the encoded
input of `app_decoder`. It is H.264 video bitrate, independent of whether packets
arrive over `wlan0`, wired `end0`, or a local MediaMTX relay. CPU/network-interface
metrics remain separate diagnostic measurements.

Any completed livestream session, including normal exit, duration expiry, signal
shutdown and a switch back to normal mode, first centers its controller and closes
the status connection. It then opens the existing authenticated BLE session and
sends the previously validated fixed `normal_stop_livestream` command once,
requiring Pocket's `00` response. The application window closes only after this
cleanup. Failed stop attempts are recorded as failures and are not automatically
repeated. The owned MediaMTX server may remain idle for the next connection.

Publisher discovery excludes all local IPv4 addresses so the GUI's own RTMP reader
cannot be mistaken for a second camera. UDP reconnect ignores old-session
telemetry while waiting within the original timeout for the new handshake ACK.

STOP now leaves the control socket and its keepalive/ACK tasks alive in a paused
state. Explicit re-arm reuses that socket; only exit/mode shutdown closes it.
This avoids an unnecessary new camera handshake when video is already running.

### Validation, 2026-09-06

- Remote suite: 315 passed, 1 skipped (optional NumPy absent). No tests ran on Windows.
- Physical live session `control-session-gui-polish-live-20260906-0240` passed:
  STOP/re-arm, nonzero encoded bitrate (mean 3.99 Mbps; screenshot 4.7 Mbps),
  progressing connection UI, and final Pocket stop response `00`.
- Final transition-cover implementation passed the three-mode GTK/MPP replay in
  `control-session-gui-polish-replay-20260906-0253`, using saved video on ROCK 4D
  with BLE and network operations replaced by test doubles. The same transition
  surface survived all three native video windows.
- Earlier live-window reuse caused a Wayland client protocol error; the final
  implementation uses the covering-window approach described above. Its owned
  capture processes were stopped and temporary WLAN state restored.
- A later full wireless retry could not discover Pocket 3 over BLE (BlueZ itself
  remained powered). Final-version full wireless round-trip validation therefore
  remains pending device availability; replay evidence is not claimed as that proof.
