# Touch camera actions and monitor output

## Focus correction and query UI removal — 2026-09-06

The recording-capability button, accompanying explanation, and automatic
startup subscription have been removed. Historical protocol fixtures remain
readable, but the GUI no longer queries `camcap_video_format`.

The original focus gesture was attached to the native `gtkwaylandsink` widget
at its default propagation phase. It is now attached to the application window
in **GTK capture phase**, with explicit window-to-video coordinate translation.
It observes without claiming gestures, so joystick and button input still work.
Dragging, long pressing, button rows and the joystick region do not send focus.
Taps on letterboxes show a clear "black border" indication instead of silently
doing nothing. The marker is a native Wayland popover above the video, displayed
for three seconds: yellow pending, red unavailable/unacknowledged, green text
for command acknowledgment. An ACK is not presented as a measured AF lock.

【参考实现结论】GStreamer's
[GTK base widget](https://github.com/GStreamer/gstreamer/blob/1.24/subprojects/gst-plugins-bad/ext/gtk/gtkgstbasewidget.c)
implements its own mouse/touch navigation handling. Capturing at the ancestor
window avoids relying on the native video widget's bubbling behavior.
OpenPocketCine's [tap-focus sequence](https://openpocketcine.app/docs/protocol/commands/)
has additional AE writes; those were **not** introduced, because the existing
02/30 region command demonstrably refocuses this Pocket 3 once clicks reach it.

【实机事实】`control-session-focus-point-20260906-0426` injected three pointer
clicks via GDK's event queue (not direct calls to the protocol/UI action handler):
near at picture x=1/3, far at x=11/12, then near again, all y=1/2. All three
traversed GTK capture, coordinate mapping, marker creation, the single UDP writer,
and received matching 02/30 status-00 replies. No gimbal, AE, recording, or
recording-capability command was sent. The stream stayed on MPP/gtkwaylandsink.

【统计观察】Fixed ROIs in the DSI screenshots showed this Laplacian variance
(a scene-dependent sharpness indicator; higher is sharper):

| Click | Computer text ROI | Distant room ROI |
|---|---:|---:|
| Near | 3724.56 | 33.20 |
| Far | 16.02 | 648.73 |
| Near again | 3709.81 | 32.84 |

Exposure-normalized values reverse in the same direction. Inspection of the
full PNGs confirms the user's criterion: distant room sharp/text blurred, then
text sharp/room blurred. The final focus was returned to the computer screen.
Original screenshots, ROI crops, raw commands and JSON measurements are private
evidence; image processing ran only on ROCK 4D using existing GdkPixbuf and the
Python standard library. No new dependency or AE/metering command was required.

【捕获推断】This supports an input-delivery defect, not an ineffective 02/30
payload for this camera state. The previous ACK-only center test bypassed real
GUI events and could not establish user-visible tap behavior. This new test
uses GDK pointer events; an actual finger on the DSI digitizer is still a
distinct manual acceptance check, although both use the same GTK controller.

Final remote suite: **327 passed, 1 skipped** (existing optional-NumPy latency
test). All **36** private evidence files passed SHA-256 verification on ROCK 4D
and were retrieved locally. The session logged exactly three focus actions and
zero recording-capability queries. The application and camera RTMP publisher
were stopped cleanly; no capture process or owned temporary WLAN state remained.

The sections below retain the preceding implementation/verification history.

## Controls

The joystick starts on a fresh gesture and stops on release/cancel. The old
enable-control and STOP buttons are removed; keyboard Space and focus-loss
stopping remain. A fault is never implicitly cleared by a gesture.

The bottom row provides separate **回中**, **180°**, **图传**, and **退出**
buttons. Explicit buttons avoid confusing a joystick drag with double/triple
taps. Buttons use a dark rounded style and at least 56 logical pixels of height.
Recenter/flip are serialized with stick commands, start from released input,
and have a two-second cooldown. There is no automatic action retry.

Tapping the video sends a normalized focus-region point. Aspect-fit black bars
and the joystick overlay are excluded. Drags and long presses are not focus
taps. A brief focus marker shows the selected point. This does not change AE,
exposure mode, recording resolution, or metering settings.

## Protocol evidence

【参考实现结论】Compared against OpenPocketCine commit
`47762e913ac6297fdf90cf7f643423d2d8274c2b`,
[Commands.swift](https://github.com/erik-sutton95/OpenPocketCine/blob/47762e913ac6297fdf90cf7f643423d2d8274c2b/Sources/OpenPocketViewCore/Commands.swift).
The local reference checkout retains that commit for reproducibility.

| Action | Sender → receiver | cmdSet/cmdId | Payload | Expected reply |
|---|---|---|---|---|
| Recenter | 02 → 04 | 04/4C | FE08 | Same sequence, ACK, status 00 |
| 180-degree flip | 02 → 04 | 04/4C | FE09 | Same sequence, ACK, status 00 |
| Focus point | 02 → 01 | 02/30 | float32 LE x,y; 13 zero bytes | Same sequence, ACK, status 00 |
| Recording-capability subscription | 02 → 28 | 00/99 | Named `camcap_video_format` subscription | Correlated reply; named table is separate |

These are typed actions over the existing single UDP writer. Unknown action
names and invalid focus coordinates are rejected before sending. No arbitrary
payload entry point, raw-PWM control, shooting command, or mode-change command
was added. Focus does not copy OpenPocketCine's additional AE commands.

## Monitor formats versus recording formats

【实机事实】The new RTMP selector has produced H.264 at 854×480,
1280×720, and 1920×1080 through the existing `mppvideodec → gtkwaylandsink`
pipeline. It uses the existing 08/78 configuration schema, a private derived
proposal, and the existing stop/reconnect lifecycle. The approved original
proposal is not edited. Mode/quality transitions keep the progress window up.

【实机事实】Normal mode currently receives H.264 1280×720. A correlated
00/99 subscription reply was received in both modes, but no named
`camcap_video_format` table arrived during these bounded observations.

【参考实现结论】That table and 02/18 concern **recording**, not a monitor
codec capability list. They cannot establish H.265/HEVC or MJPEG availability on
Pocket 3. HEVC support on another Pocket model is not Pocket 3 evidence.

【待验证假设】Additional normal-mode monitor resolutions/codecs may exist,
but there is no validated enumeration or selection command. The UI says so
instead of presenting guessed options. Useful additional evidence would be a
Mimo capture of changing **monitor/live-view quality or codec** in normal mode,
with the exact setting labels and before/after selections. Changing recording
resolution alone is a different experiment.

## Validation and evidence

All execution, tests and physical validation run on ROCK 4D. Windows retains
the source, Git and retrieved private evidence. Captures and stream credentials
are excluded from Git. New actions do not alter the kernel, DSI/touch stack,
Bluetooth service, or persistent network configuration.

Focus and format verification:

- `control-session-monitor-actions-20260906-0338`: normal-mode center focus
  status 00 and 1280×720 output. An intermediate cleanup variable-shadowing bug
  was identified and fixed, with a fast-ACK/receiver-cleanup regression test.
- `control-session-monitor-formats-20260906-0342`: RTMP 720/480/1080 outputs
  and center-focus status 00 at every size. Final BLE connection setup failed
  before any DJI write; a subsequent owned stop received status 00.
- Connection disappearance on stop is now retried only once and only before
  any FFF5 write. Once a write has occurred, errors are surfaced without replay.

【实机事实】Focus commands received successful ACKs. This alone does not
prove optical focus sharpness or all touchscreen hit-testing behavior; those
remain user-visible acceptance checks.

`control-session-camera-actions-20260906-0352` completed normal → livestream
→ exit without error. It sent one recenter and one flip in each mode; all four
received matching status-00 replies. Both controllers ended disabled, with no
control fault. Temporary WLAN rollback and camera RTMP stop succeeded.

【统计观察】Normal flip changed the 04/05 offset-16 int16-LE yaw candidate
from 14245 to -3757 (delta -18002). Live flip changed it from -3764 to 14226
(delta +17990). The video scene reversed and frames continued arriving during
both actions. This supports the flip semantics; it does not independently
calibrate the raw candidate to degrees. Initial recenter tests began near their
existing center, so ACK success alone was not treated as displacement proof.

【实机事实】`control-session-recenter-live-20260906-0401` completed a bounded
800 ms movement using the already-validated joystick controller, followed by
one recenter. The yaw candidate was 14245 initially, 14983 after positioning,
and 14235 after recenter. The command received status 00; no fault occurred,
and final RTMP shutdown was confirmed. This is displacement-and-return evidence,
not merely a successful response while already centered.

【实机事实】Two attempts to perform that additional positioning check in normal
mode (`control-session-recenter-offset-20260906-0357` and
`control-session-recenter-offset-retry-20260906-0358`) timed out reading hotspot
credentials, before any UDP gimbal action or hotspot join. BLE connected and
reported already-paired; three FFF5 attempts were session-open, pairing-status,
and SSID-read. Both exited with network rollback complete. Normal-mode retries
were stopped; the live route above succeeded. The cause of this existing
credential-read path's failure after RTMP shutdown remains unresolved. This is
not hidden as a successful normal-mode reconnection.

Final software validation: **323 passed, 1 skipped**, entirely on ROCK 4D.
The skip is the existing optional-NumPy glass-latency test. No dependencies were
installed. The tests cover exact action frames, focus bounds/letterboxes, unknown
action rejection, early ACK timing, writer cleanup, joystick re-arm, private
resolution-proposal immutability, and no stop-command replay after a write.
Software commit: `0625118` (current touch actions and output presets).

All named evidence directories were checked against their SHA-256 manifests on
ROCK 4D and retrieved into Windows `artifacts/private/`. Images and private
payloads are not committed. Test output remains in local remote-wrapper logs.
Startup uses the existing `app-start-normal` / `app-start-live` commands; no
autostart, display calibration, persistent WLAN change or system restart is added.

Final archive check covered **176 files** across the seven named private
directories. New UDP action totals: recenter **3**, flip **2**, focus **4**,
recording-capability subscription **7**. The displaced check additionally used
the pre-existing bounded stick path; normal/live connection and teardown used
the previously validated BLE/UDP session commands. The app is stopped, no RTMP
connection remains, and no btmon/tcpdump process remains. The wired default route
and previous WLAN were retained. `private-pull` retrieves a single safe-named
private diagnostic directory without relaxing the source/deployment boundary.
