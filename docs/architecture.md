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

The repository/runtime boundary, platform audit, BLE discovery/GATT enumeration, DUML codec, stream reassembly, passive FFF4 notification capture, conservative telemetry recording, and an offline pairing state machine now exist. Active DJI application-layer pairing remains gated behind explicit first-write authorization. Video, Wi-Fi provisioning, UI, and control remain roadmap items.

## Layer boundaries

- `transport`: BlueZ/Bleak discovery, connection lifecycle, FFF4 subscription, timestamped bytes, policy-gated FFF5 transmission, advertisement preservation, and GATT metadata enumeration.
- `protocol`: DUML CRC, structured framing, stream reassembly, command metadata, transaction sequence, and deny/allow policy; it has no Bleak dependency and does not own model selection.
- `device profile`: model fingerprints and capability tables for Pocket 3 versus Pocket 4/4P. Unknown identifiers remain raw and tolerated.
- `video`: future RTMP/session ingestion and decoder selection.
- `display`: the existing DRM/DSI stack and a future non-destructive preview surface.
- `input`: existing touch plus future joystick/button events and fail-safe normalization.
- `application state`: connection and DJI application-layer pairing state now; telemetry, video, recording, and control state later.

Model checks belong only in the device-profile/capability layer. BLE transport, video, and UI code consume capabilities and must never scatter Pocket-specific constants. Raw advertisements remain available even when no profile matches.

## Safety invariants

Passive listen performs only BLE connection and FFF4 CCCD subscription. It neither writes FFF5 nor auto-ACKs incoming DUML requests. Pairing frames can reach the FFF5 method only with a narrow explicit authorization object, and unexpected responses stop the state machine. Wi-Fi provisioning, AP/P2P setup, video, camera commands, and gimbal movement remain prohibited. A later control layer must enforce command zeroing, maximum angular velocity/duration, and a 300–500 ms heartbeat-loss stop before motion is enabled.
