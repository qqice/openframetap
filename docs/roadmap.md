# Roadmap

1. **Completed:** local/remote workflow, ROCK 4D wireless/media/display audit, passive BLE capture, and metadata-only GATT enumeration.
2. **Completed for the current evidence boundary:** owner-confirmed Pocket 3 application pairing, explicit `already_paired` response, passive state capture, and conservative device/battery/gimbal candidates.
3. **Completed for one controlled session:** a structured FFF4-only body-motion experiment with ROCK 4D event markers, exact screen correlation for the `0D/02` battery candidate, and provenance-preserving `04/05` yaw/pitch/roll candidates. Exact angle scales and remaining status semantics require an independent passive replication.
4. **Completed for Pocket 3:** the fixed same-connection five-stage workflow produced a local 1280x720 H.264/AAC RTMP publisher and an immutable 8-second sample.
5. **Completed:** RK3576 MPP and software decoder comparison, direct-RTMP low-latency profiles, true fullscreen GNOME Wayland presentation on the existing DSI display, and a 600-second stability run.
6. **Tooling complete; physical evidence pending:** refresh-aware 16-bit Gray Code source, immutable timing evidence, perspective/threshold/mixed-refresh phone-video decoder, latency distributions, plots, and sanitized reporting. A simultaneous 240 fps phone recording is still required before any physical median/p95/p99 claim or stable-versus-low-latency selection.
7. Add tightly bounded gimbal telemetry/recenter/low-speed control with zeroing and heartbeat fail-safe.
8. Add separate Pocket 4 and Pocket 4P profiles for identity, pairing, live payloads, and controls.
9. Consider ROCK 4D 5 GHz AP or Wi-Fi Direct only after advertised driver capabilities and long-duration stability are proven.
10. Compare original FrameTap local transport with local RTMP for latency, frame rate, bitrate, power, and recovery.

No roadmap item after step 6 is automatically entered by the current tooling.
