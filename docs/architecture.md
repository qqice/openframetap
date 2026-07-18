# Architecture

The local computer owns the Git repository, tests, documentation, and evidence archive. The ROCK 4D is a deployment and hardware-validation target only.

```text
Computer-side Codex / Git repository
        ↓ SSH + rsync (tar-over-SSH fallback where local rsync is absent)
ROCK 4D runtime
        ↓
BlueZ BLE transport
        ↓
DJI DUML framing / pairing / state
        ↓
Wi-Fi provisioning and session control
        ↓
Local RTMP proof-of-concept receiver
        ↓
RK3576 MPP/V4L2 hardware decoder
        ↓
Existing 5-inch 720p DSI display
        ↓
Existing five-point touch input
        ↓
Future joystick/buttons and gimbal controller
```

The repository/runtime boundary, platform audit, BLE discovery/GATT enumeration, DUML codec, stream reassembly, application-layer pairing, passive telemetry experiments, and immutable-evidence analysis exist. The validated fixed Pocket 3 five-stage workflow now provisions the existing external WLAN and starts a private local RTMP publisher. The ROCK 4D receives it through MediaMTX, explicitly decodes H.264 with RK3576 MPP, and presents a bounded low-latency fullscreen surface through the existing GNOME Wayland/DSI stack. Gimbal, capture control, new DJI commands, Wi-Fi Direct/AP, and a touch UI remain outside this milestone.

## Layer boundaries

- `transport`: BlueZ/Bleak discovery, connection lifecycle, FFF4 subscription, timestamped bytes, human-gated single-frame FFF5 transmission, advertisement preservation, and GATT metadata enumeration.
- `protocol`: DUML CRC, structured framing, stream reassembly, command metadata, transaction sequence, and deny/allow policy; it has no Bleak dependency and does not own model selection.
- `device profile`: model fingerprints and capability tables for Pocket 3 versus Pocket 4/4P. Unknown identifiers remain raw and tolerated.
- `video`: MediaMTX lifecycle, RTMP demux, explicit software/MPP decoder selection, bounded queue profiles, private evidence, runtime metrics, and process ownership. It has no BLE dependency after the publisher is established.
- `display`: active Wayland session recovery, immutable DSI geometry audit, transient fullscreen presentation, and restoration of prior GNOME Overview state.
- `input`: existing touch plus future joystick/button events and fail-safe normalization.
- `application state`: connection and DJI application-layer pairing state now; telemetry, video, recording, and control state later.
- `experiments`: interactive ROCK 4D TTY events, synchronized monotonic timestamps, passive FFF4 capture, session safety counters, and raw evidence checksums.
- `analysis`: bounded payload field enumeration, event/message alignment, correlation and lag statistics, candidate confidence, reports, and plots. It never changes raw evidence.
- `glass latency`: Windows Gray Code submission evidence, phone-video metadata/timestamps, perspective-corrected SOURCE/DSI decoding, mixed-refresh exclusion, wrap-safe timing mapping, distribution statistics, and path-free sanitized reports. It never controls Pocket or ROCK hardware.
- `telemetry state`: candidate values plus command, offset, encoding, raw value, evidence session, update time, and confidence provenance. Names retain `_candidate` until evidence supports promotion.

Model checks belong only in the device-profile/capability layer. BLE transport, video, and UI code consume capabilities and must never scatter Pocket-specific constants. Raw advertisements remain available even when no profile matches.

## Safety invariants

Passive listen performs only BLE connection and FFF4 CCCD subscription. Fixed livestream frames remain hash-pinned, ordered, bounded, and fail closed on response divergence; no new command ID or modified payload is inferred. Notification callbacks only record/enqueue data and cannot send. AP/P2P setup, camera capture, and gimbal movement remain prohibited. Video receive has no FFF5 write path: it pulls an already-established MediaMTX stream and manages only its own user-space PIDs. A later control layer must enforce command zeroing, maximum angular velocity/duration, and a 300–500 ms heartbeat-loss stop before motion is enabled.

The experiment session calls only connect, ATT-MTU acquisition, FFF4 subscribe, and disconnect. It records CCCD and FFF5 counters separately and treats any nonzero FFF5 counter as a safety failure. Event input is local to the ROCK 4D TTY and cannot select a DUML command. Analysis checks the raw SHA-256 manifest before producing output and again afterward.
