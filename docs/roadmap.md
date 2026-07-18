# Roadmap

1. **Current milestone:** local/remote workflow, ROCK 4D wireless/media/display audit, passive BLE capture, and metadata-only GATT enumeration.
2. Research Pocket 3 pairing, device info, battery, and gimbal telemetry with explicit write authorization and captured validation.
3. Join ROCK 4D and Pocket 3 to the same external 5 GHz router; provision a local RTMP destination without ROCK 4D AP/P2P.
4. Validate streams with `ffprobe` and short samples, then test RK3576 MPP/V4L2 decode on the existing DSI display.
5. Add tightly bounded gimbal telemetry/recenter/low-speed control with zeroing and heartbeat fail-safe.
6. Add separate Pocket 4 and Pocket 4P profiles for identity, pairing, live payloads, and controls.
7. Consider ROCK 4D 5 GHz AP or Wi-Fi Direct only after advertised driver capabilities and long-duration stability are proven.
8. Compare original FrameTap local transport with local RTMP for latency, frame rate, bitrate, power, and recovery.

No roadmap item after step 1 is automatically entered by the current tooling.
