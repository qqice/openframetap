# Pocket 3 external-Wi-Fi RTMP protocol plan

This document covers the preflight and independently authorized prepare
boundary. The one approved prepare frame has now been sent and acknowledged;
no Wi-Fi, RTMP configuration, camera, or gimbal command has been sent.

## Verified LAN and receiver baseline

- 【实机事实】The ROCK 4D `wlan0` address is `192.168.1.229/24`, with gateway
  `192.168.1.1`. The Codex SSH control route remains on `tailscale0` at
  `100.125.223.67/32`.
- 【实机事实】The Windows source host is on the same LAN at `192.168.1.154` and
  reached the ROCK directly with that source address.
- 【实机事实】TCP 1935 was unused before the test. UFW was inactive. No
  firewall, route, NetworkManager, AP, P2P, or interface setting was changed.
- 【实机事实】The existing ROCK image has GStreamer 1.24.2 but no FFmpeg,
  FFprobe, MediaMTX, nginx, Docker, or tcpdump executable.

## RTMP receiver

- 【实机事实】OpenFrameTap uses the official MediaMTX `v1.18.2` Linux arm64
  release in the user-owned runtime directory. The downloaded archive SHA-256
  matched the official `checksums.sha256` value
  `c78aa7a1bdab94b2b02be364661f17802143215dba37e1fa67c3e0849248b485`.
- 【实机事实】The deployed binary SHA-256 is
  `b499c82f596762b0d7d2a02aa62f30495923bb0508121c7c7c76fde5f8ee0e73`.
  It was not installed system-wide and no systemd service was created.
- 【实机事实】The generated configuration binds RTMP only to
  `192.168.1.229:1935` and disables RTSP, HLS, WebRTC, SRT, API, metrics,
  pprof, and playback.
- 【实机事实】`video server doctor` started the listener, verified reachability,
  stopped it, and removed the PID file. The final status was `stopped`.

## LAN publish self-test

- 【实机事实】Windows FFmpeg published over the physical LAN to the ROCK
  MediaMTX listener. MediaMTX recorded the publisher and reader connections.
- 【实机事实】FFprobe detected FLV with H.264 Constrained Baseline, level 2.2,
  YUV420p, 640x360, 20 fps, and AAC-LC 48 kHz mono.
- 【实机事实】The read-back FLV sample was 652,772 bytes. Software decoding of
  100 frames returned zero errors and a PNG frame was extracted.
- 【实机事实】The compliant evidence is stored under private
  `rtmp-selftest-20260718T162627Z` and sanitized
  `rtmp-selftest-20260718T162627Z`; the complete test stream key appears only
  in private evidence.
- 【统计观察】The first automated attempt raced the publisher before MediaMTX
  created the path. A five-second video-only diagnostic proved publishing, and
  increasing the deterministic publisher warm-up from two to four seconds
  removed the race. No Pocket command or retry was involved.

## Reference-derived minimum state machine

```text
existing paired evidence
  -> 02/E1 prepare_to_live_stream
  -> observe same-sequence response
  -> 07/47 Wi-Fi provisioning (private payload, separately authorized once)
  -> observe Wi-Fi result
  -> 08/78 configure RTMP URL and stream settings (separately authorized)
  -> wait for TCP/RTMP rather than guessing success
```

【参考实现结论】`node-osmo`, Moblin, and `djictl` agree that the initial prepare
request is App `0x02` to VideoTransmission `0x08`, flags `0x40`, command
`02/E1`, payload `1A`. A public Mimo capture records a response from `0x08` to
`0x02` with `C0/02/E1 payload 00`.

【参考实现结论】All three implementations encode Wi-Fi provisioning as
`02 -> 07`, `07/47`, packed SSID followed by packed PSK. The public capture
shows a `C0/07/47` response, but public implementations disagree about whether
two or three zero result bytes are expected.

【参考实现结论】`node-osmo`, Moblin, and `djictl` agree on `02 -> 08`, `08/78`
for the RTMP URL and quality payload. The ordinary Pocket 3 layout uses fixed
header byte `0x2E`, resolution byte, little-endian bitrate, FPS byte, padding,
and a packed URL. No local Pocket frame has validated those fields yet.

【参考实现结论】`djictl` additionally sends `02/8E payload 00011C00` during
prepare and `02/8E payload 01011A000101` after configuration. The first has a
real Mimo capture; Pocket 3 paths in current node-osmo and Moblin omit both.
OpenFrameTap therefore treats each `02/8E` use as a separate, currently denied
command type rather than silently bundling it into prepare or start.

## First proposal and authorization point

The sanitized proposal is stored under
`artifacts/sanitized/proposals/prepare-20260718T163611Z/`.

```text
command:       prepare_to_live_stream
sender:        0x02
receiver:      0x08
sequence:      0x8C12
flags:         0x40 (ACK required)
cmdSet/cmdId:  0x02/0xE1
payload:       1A
frame:         550e046602088c124002e11a0cfe
SHA-256:       e0286b3d9e63e248f0792c8ae4055587484aba8a05ba154c451c8ae987cc2ad6
```

- 【参考实现结论】The sequence `0x8C12` is retained from node-osmo/Moblin for a
  deterministic proposal; Moblin explicitly states that transaction values do
  not carry fixed semantics. The response must match the actual sequence.
- 【实机事实】OpenFrameTap encoded and decoded this 14-byte proposal identically;
  CRC8 and CRC16 both validate. It contains no SSID, PSK, RTMP URL, or stream
  key.
- 【待验证假设】A successful local Pocket response is expected to be
  same-sequence `C0/02/E1 payload 00`. Any different response stops the workflow
  and is not reinterpreted automatically.
- 【待验证假设】Sending the proposal may move the Pocket application into a
  livestream-preparation state. It must be sent at most once by the owner after
  separate authorization; no subsequent Wi-Fi or stream frame is authorized by
  that approval.

## Owner-operated approved send boundary

- 【实机事实】On 2026-07-19 the device owner approved the first, single send of
  this exact A-class `prepare_to_live_stream` frame. The approval names only the
  frame whose SHA-256 is shown above.
- 【实机事实】After the unexpected Windows restart, the local proposal still
  passed its fixed SHA-256 check and the remote process/listener audit found no
  active capture or RTMP process. No evidence indicates that the approved frame
  was sent before the restart.
- 【实机事实】The owner-only entry point is
  `./scripts/remote.sh pocket3-rtmp-send-approved-prepare`. It refuses a
  non-interactive stdin before SSH, deploys only the fixed private proposal,
  and requires the owner to type the complete approved SHA-256 in the remote
  TTY before one FFF5 write can occur.
- 【待验证假设】The expected response remains a same-sequence `C0/02/E1`
  response with payload `00`. The wrapper captures notifications and btmon
  evidence but sends no automatic response or follow-up command regardless of
  what is received.

The authorization does **not** cover `07/47`, `08/78`, either `02/8E` payload,
stop, retry, or any camera/gimbal command. Each would require a new proposal and
separate owner authorization.

## Prepare response result

- 【实机事实】The owner executed the fixed wrapper once. btmon contains exactly
  one FFF5 Write Command, carrying the approved frame SHA-256
  `e0286b3d9e63e248f0792c8ae4055587484aba8a05ba154c451c8ae987cc2ad6`.
- 【实机事实】Pocket returned exactly one matching transaction response:
  sender `0x08`, receiver `0x02`, sequence `0x8C12`, flags `0xC0`, command
  `02/E1`, payload `00`. Its raw-frame SHA-256 is
  `849bf1fe90cd2b6a8a66fc7bd28e20c8c38972cf8a77c608cb0758c8d6a1e011`;
  CRC8 and CRC16 both validate.
- 【统计观察】The response arrived 3853.420 ms after the local FFF5 write
  completion event. The 16.5-second session recorded 608 notifications and 608
  DUML frames, with zero CRC, reassembly, connection, or automatic-follow-up
  failures.
- 【捕获推断】Reverse direction, ACK flags, matching sequence and command IDs
  identify this frame as the response to the approved request.
- 【参考实现结论】The reviewed projects and public Mimo capture interpret
  `C0/02/E1 payload 00` as successful livestream preparation.
- 【实机事实】The persistent workflow is now `prepare_acknowledged`. This state
  does not authorize or construct the next command.

Private evidence is under `pocket3-rtmp-prepare-20260719-010705`; its sanitized
offline result is under the same-named `artifacts/sanitized` directory. The
analysis verified the capture checksum manifest before parsing and confirmed
that all raw files remained unchanged afterward.

## Secret and failure boundary

- 【实机事实】No NetworkManager secret/keyring/connection file was inspected.
  The observed connection name is stored only as mask `Q***G` and SHA-256.
- 【实机事实】Private proposal and workflow files are restricted to
  `artifacts/private`; sanitized structures contain address hashes/masks rather
  than the complete Pocket address.
- 【实机事实】The Wi-Fi proposal generator is not invoked at this stop point.
  Therefore no SSID or PSK has been requested, encoded, logged, or persisted.
- 【已否定假设】An early offline proposal state referenced an empty pairing
  evidence SHA because the operator-side evidence filename was wrong. The
  loader now requires both 64-character prerequisite hashes, the invalid
  proposal is un-sendable, and the corrected proposal references the actual
  paired-session summary SHA.
