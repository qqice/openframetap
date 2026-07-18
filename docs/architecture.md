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

The repository/runtime boundary, platform audit, BLE discovery/GATT enumeration, DUML codec, stream reassembly, passive FFF4 notification capture, conservative telemetry recording, application-layer pairing state machine, human event marker, and immutable-evidence analysis now exist. A user-space RTMP receiver, LAN-only address selection, secret-safe evidence split, local RTMP publish/readback self-test, and a persistent fail-closed Pocket 3 livestream proposal workflow now also exist. The first prepare frame was sent once by the owner and received the expected matching-sequence ACK. The approved Wi-Fi frame was also sent once, but its 33-second capture contained no protocol response, so association remains unknown and later livestream commands remain blocked. Pocket-originated video, UI, and control have not been executed.

## Layer boundaries

- `transport`: BlueZ/Bleak discovery, connection lifecycle, FFF4 subscription, timestamped bytes, human-gated single-frame FFF5 transmission, advertisement preservation, and GATT metadata enumeration.
- `protocol`: DUML CRC, structured framing, stream reassembly, command metadata, transaction sequence, and deny/allow policy; it has no Bleak dependency and does not own model selection.
- `device profile`: model fingerprints and capability tables for Pocket 3 versus Pocket 4/4P. Unknown identifiers remain raw and tolerated.
- `video`: MediaMTX lifecycle, LAN publish/readback self-test, ffprobe normalization, sample remux, software decode evidence, and future Pocket-originated RTMP ingestion. It has no BLE dependency.
- `display`: the existing DRM/DSI stack and a future non-destructive preview surface.
- `input`: existing touch plus future joystick/button events and fail-safe normalization.
- `application state`: connection and DJI application-layer pairing state now; telemetry, video, recording, and control state later.
- `experiments`: interactive ROCK 4D TTY events, synchronized monotonic timestamps, passive FFF4 capture, session safety counters, and raw evidence checksums.
- `analysis`: bounded payload field enumeration, event/message alignment, correlation and lag statistics, candidate confidence, reports, and plots. It never changes raw evidence.
- `telemetry state`: candidate values plus command, offset, encoding, raw value, evidence session, update time, and confidence provenance. Names retain `_candidate` until evidence supports promotion.

Model checks belong only in the device-profile/capability layer. BLE transport, video, and UI code consume capabilities and must never scatter Pocket-specific constants. Raw advertisements remain available even when no profile matches.

## Safety invariants

Passive listen performs only BLE connection and FFF4 CCCD subscription. It neither writes FFF5 nor auto-ACKs incoming DUML requests. A frame can reach the FFF5 method only after a fixed proposal passes command/address/SHA/CRC/schema checks and the owning user types its complete SHA-256. Authorization is exactly one command name and cannot authorize a follow-up category. Notification callbacks only record/enqueue data and cannot send. The offline state machine produces proposals rather than transport actions. AP/P2P setup, camera capture, gimbal movement, DSI preview, and hardware decode remain prohibited. A later control layer must enforce command zeroing, maximum angular velocity/duration, and a 300–500 ms heartbeat-loss stop before motion is enabled.

The experiment session calls only connect, ATT-MTU acquisition, FFF4 subscribe, and disconnect. It records CCCD and FFF5 counters separately and treats any nonzero FFF5 counter as a safety failure. Event input is local to the ROCK 4D TTY and cannot select a DUML command. Analysis checks the raw SHA-256 manifest before producing output and again afterward.
