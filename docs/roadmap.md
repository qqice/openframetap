# Roadmap

1. **Completed:** local/remote workflow, ROCK 4D wireless/media/display audit, passive BLE capture, and metadata-only GATT enumeration.
2. **Completed for the current evidence boundary:** owner-confirmed Pocket 3 application pairing, explicit `already_paired` response, passive state capture, and conservative device/battery/gimbal candidates.
3. **Completed for one controlled session:** a structured FFF4-only body-motion experiment with ROCK 4D event markers, exact screen correlation for the `0D/02` battery candidate, and provenance-preserving `04/05` yaw/pitch/roll candidates. Exact angle scales and remaining status semantics require an independent passive replication.
4. Join ROCK 4D and Pocket 3 to the same external 5 GHz router; provision a local RTMP destination without ROCK 4D AP/P2P.
5. Validate streams with `ffprobe` and short samples, then test RK3576 MPP/V4L2 decode on the existing DSI display.
6. Add tightly bounded gimbal telemetry/recenter/low-speed control with zeroing and heartbeat fail-safe.
7. Add separate Pocket 4 and Pocket 4P profiles for identity, pairing, live payloads, and controls.
8. Consider ROCK 4D 5 GHz AP or Wi-Fi Direct only after advertised driver capabilities and long-duration stability are proven.
9. Compare original FrameTap local transport with local RTMP for latency, frame rate, bitrate, power, and recovery.

No roadmap item after step 3 is automatically entered by the current tooling.
