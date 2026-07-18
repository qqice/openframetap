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

## Next B-class proposal boundary

- 【实机事实】No remote `~/.config/openframetap/secrets.env` file existed at the
  post-prepare audit. OpenFrameTap did not inspect NetworkManager, a keyring, or
  any connection profile to obtain credentials.
- 【参考实现结论】node-osmo, Moblin, and djictl independently encode the `07/47`
  payload as one-byte UTF-8 SSID length plus SSID bytes, followed by one-byte
  PSK length plus PSK bytes. They agree on sender `0x02`, receiver `0x07`,
  flags `0x40`, and reference sequence `0x8C19`.
- 【待验证假设】A same-sequence `C0/07/47` response reports the provisioning
  result. Reviewed implementations disagree whether successful payload is two
  or three zero bytes, so neither form will be assumed until captured locally.

`pocket3-rtmp-configure-wifi-secrets` prompts with terminal echo disabled and
stores only the user-provided test SSID/PSK in a remote `0600` file. It performs
no BLE operation. `pocket3-rtmp-propose-wifi` then produces a private raw frame
and a sanitized structure containing only the SSID mask/hash, PSK length/hash,
field lengths, frame hash and CRC results. Proposal generation performs no BLE
connection or write and cannot authorize itself.

## Wi-Fi proposal and authorization point

- 【实机事实】The owner-created remote secrets file is a regular non-symlink
  file owned by `qqice` with mode `0600`. Terminal capture contains prompts and
  completion status only; it does not contain the entered SSID or PSK.
- 【实机事实】The proposed SSID SHA-256 equals the live ROCK `wlan0` association
  SSID SHA-256. The current ROCK link frequency is `5745 MHz`, confirming that
  this is the existing external 5GHz network without changing NetworkManager.
- 【实机事实】The private proposal is 45 bytes with a 32-byte payload: a
  15-byte SSID and 15-byte PSK, each preceded by its one-byte length. No secret
  value, private payload, or full frame appears in the sanitized proposal.
- 【实机事实】The candidate decodes as sender `0x02`, receiver `0x07`, sequence
  `0x8C19`, flags `0x40`, command `07/47`. CRC8, CRC16 and byte-for-byte
  round-trip all validate. Its frame SHA-256 is
  `8751117a3022d1a0057a4d6ea1ef38505314d1d75995fd4d46fc8d85e25ed72e`.
- 【实机事实】The workflow is `wifi_proposed`; `locally_sent=false`, maximum
  send count is one, automatic retry is false, and automatic follow-up is
  false. Proposal creation did not start a BLE connection or FFF5 sender.
- 【待验证假设】The request will instruct Pocket to join that external network.
  A matching `C0/07/47` response must be captured and reported verbatim; the
  current public two-byte/three-byte success-payload conflict prevents an
  automatic success transition.

Private proposal evidence is under `artifacts/private/proposals/` with stem
`wifi-20260718T173350Z`; the corresponding sanitized proposal uses the same
stem. This B-class command is still unsent and requires its own owner approval.
Approval would cover only this exact frame SHA-256, not RTMP configuration,
start, `02/8E`, stop, retry, or any other command.

## B-class owner execution boundary

- 【实机事实】On 2026-07-19 the owner approved the first, single send of the
  private `wifi_connect` frame whose SHA-256 is
  `8751117a3022d1a0057a4d6ea1ef38505314d1d75995fd4d46fc8d85e25ed72e`.
  It remains unsent pending owner execution.
- 【实机事实】The fixed owner entry point is
  `./scripts/remote.sh pocket3-rtmp-send-approved-wifi`. It accepts no frame,
  SSID, PSK, command, sequence, or arbitrary hex argument.
- 【实机事实】Before opening the remote TTY, the wrapper checks the exact private
  binary SHA-256, proposal command, and `wifi_proposed` workflow phase. The
  runtime then revalidates target address, secret fingerprints, wire fields,
  CRCs, one-send policy, and full SHA confirmation.
- 【实机事实】The capture subscribes FFF4 before the one FFF5 write, listens for
  30 seconds, and records private btmon/notification/DUML evidence. It sends no
  response, retry, RTMP configuration, `02/8E`, start, or stop command.

## Wi-Fi send result

- 【实机事实】The owner executed the fixed B-class wrapper once. OpenFrameTap
  recorded one approved application frame, SHA-256
  `8751117a3022d1a0057a4d6ea1ef38505314d1d75995fd4d46fc8d85e25ed72e`.
  No retry or automatic follow-up application frame was sent.
- 【实机事实】Bleak exposed its conservative default MTU 23 during this session,
  so the 45-byte application frame was emitted as three ATT Write Commands.
  This is transport fragmentation of one approved DUML frame, not three
  application commands.
- 【实机事实】The 33.070-second session received 1,187 notifications and 1,187
  valid DUML frames with zero CRC, reassembly, or active-session interruption.
  Three setup disconnect callbacks occurred before the final connection; the
  connection remained stable after subscription and the Wi-Fi write.
- 【实机事实】No frame used sequence `0x8C19`; no frame came from component
  `0x07`; and no ACK-flagged frame was captured. Consequently there is no
  matching `C0/07/47` response or explicit result payload to interpret.
- 【统计观察】The ordinary telemetry families continued at their normal rates
  for 30 seconds after the write. No high-confidence Wi-Fi association field
  has yet been identified in those messages.
- 【捕获推断】Continued BLE telemetry proves that the Pocket remained available
  over BLE; it does not prove or disprove association with the external Wi-Fi.
- 【待验证假设】The Pocket may have joined the requested network without a DUML
  response, or it may have ignored/failed the request. No current evidence
  distinguishes these cases.
- 【实机事实】The persisted workflow is `wifi_connected_or_unknown`. OpenFrameTap
  will not retry `07/47` and will not generate `08/78` or `02/8E` from this
  ambiguous result.

Private evidence is under `pocket3-rtmp-wifi-20260719-014412`; sanitized
offline analysis uses the same-named directory under `artifacts/sanitized`.
The analyzer verified the original checksum manifest and confirmed that raw
credential-bearing evidence was unchanged.

## Missing livestream-preparation diagnosis

- 【实机事实】The owner reported that Pocket did not appear in the router client
  list after OpenFrameTap's `07/47`, while DJI Mimo caused it to associate when
  the Pocket screen displayed “preparing livestream”. This is independent
  device/router evidence that the earlier association attempt did not succeed.
- 【参考实现结论】djictl implements prepare as two requests in order: `02/E1
  payload 1A`, exact success response, then `02/8E payload 00011C00`.
- 【参考实现结论】The public Pocket 3 Mimo capture contains exact request
  `551104920208ffab40028e00011c003bc8`, followed shortly by a same-sequence
  `80/02/8E` notification whose payload begins `0000011C00`. The capture labels
  this exchange as occurring while preparing to stream.
- 【参考实现结论】node-osmo and Moblin omit that stage2 but send a stop/cleanup
  `02/8E` before `02/E1`. OpenFrameTap previously followed their direct
  `02/E1 -> 07/47` path and performed neither stage2 nor cleanup.
- 【捕获推断】The locally validated `02/E1` ACK proves stage1 was accepted, but
  the missing `02/8E 00011C00`, absent `07/47` response, absent router client,
  and Mimo screen behavior together make missing prepare stage2 the strongest
  current cause.
- 【待验证假设】The stop/cleanup command may also reset stale state, but it is a
  broader state-changing operation and is not included in the first recovery
  experiment. It remains separately denied.

## Prepare-recovery proposal

The offline proposal `prepare-recovery-20260718T181349Z` contains two fixed,
separately confirmed frames for one BLE connection. It contains no Wi-Fi frame
or credential.

```text
Stage 1: prepare re-entry
wire:     02 -> 08, 40/02/E1, sequence FEAB, payload 1A
frame:    550e04660208feab4002e11abb3c
SHA-256: a9c619ff04901974b5c30d255730e9a807d4d1919ab05ae89504777df11a2ee5
gate:     require same-sequence C0/02/E1 payload 00

Stage 2: missing prepare transport step
wire:     02 -> 08, 40/02/8E, sequence FFAB, payload 00011C00
frame:    551104920208ffab40028e00011c003bc8
SHA-256: 624c92dc2ce9364346e1b5e548f8be260b9f21e35fca20503b38315c90999286
gate:     sent once only after exact Stage 1 ACK in the owner-invoked fixed command
```

- 【实机事实】Both frames pass CRC8, CRC16, structured decode and byte-for-byte
  round-trip. Stage 2 matches the public Pocket 3 Mimo request exactly.
- 【实机事实】Both remain `locally_sent=false`; maximum send count is one per
  frame, with no automatic retry and no unapproved follow-up.
- 【实机事实】The owner approved both exact frame hashes. Under the current
  operator boundary, invoking the fixed wrapper is the authorization event; no
  second SHA prompt is required. The wrapper creates a local consumed marker
  before deployment so the same fixed authorization cannot be invoked twice.
- 【实机事实】The dedicated session keeps one BLE connection, subscribes FFF4,
  sends Stage 1 once, and exposes the exact Stage 2 override only after an
  `08 -> 02 C0/02/E1`, sequence `FEAB`, payload `00`, CRC-valid response.
  Timeout, disconnect, or any same-sequence field mismatch stops before Stage 2.
- 【实机事实】Generic authorization still rejects `prepare_stream_transport`.
  The transport override is bound to Stage 2 SHA-256
  `624c92dc2ce9364346e1b5e548f8be260b9f21e35fca20503b38315c90999286`;
  no other denied command or `02/8E` payload can use it.
- 【待验证假设】A valid stage2 response and the Pocket “preparing livestream”
  state will establish the missing prerequisite. The experiment stops there;
  the command contains no Wi-Fi retry, RTMP configuration, or stream start/stop.

## Secret and failure boundary

- 【实机事实】No NetworkManager secret/keyring/connection file was inspected.
  The observed connection name is stored only as mask `Q***G` and SHA-256.
- 【实机事实】Private proposal and workflow files are restricted to
  `artifacts/private`; sanitized structures contain address hashes/masks rather
  than the complete Pocket address.
- 【实机事实】The Wi-Fi proposal generator stored the raw credential-bearing
  frame only in ignored private artifacts. Sanitized output contains the SSID
  mask/hash and PSK length/hash but no credential value, payload, or raw frame.
- 【已否定假设】An early offline proposal state referenced an empty pairing
  evidence SHA because the operator-side evidence filename was wrong. The
  loader now requires both 64-character prerequisite hashes, the invalid
  proposal is un-sendable, and the corrected proposal references the actual
  paired-session summary SHA.

## Prepare-recovery hardware result

- 【实机事实】The owner invoked the fixed prepare-recovery wrapper once. Stage 1
  `550e04660208feab4002e11abb3c` was written once and returned exact frame
  `550e04660802feabc002e1008a61`: `08 -> 02`, sequence `FEAB`, flags `C0`,
  `02/E1`, payload `00`, with valid CRC8 and CRC16.
- 【实机事实】Only after that exact ACK, Stage 2
  `551104920208ffab40028e00011c003bc8` was written once. It returned exact
  same-sequence `80/02/8E` payload `0000011c0009030900000000000020` after
  approximately 113.5 ms; both CRCs are valid.
- 【实机事实】The 15.200-second session retained 563 notifications and 563 DUML
  frames with zero CRC, reassembly, or connection-interruption failure. It
  recorded two application writes, zero Wi-Fi frames, zero RTMP-configuration
  frames, and zero unapproved follow-up frames.
- 【实机事实】The raw evidence manifest under private capture stem
  `pocket3-rtmp-prepare-recovery-20260719-023518` passed SHA-256 verification.
  Its summary SHA-256 is
  `34e0dfd95a52336e620edfa2206a05bd14fad533140fa793b85b89356285e9a1`.
- 【捕获推断】The exact Mimo-shaped Stage 2 response proves that this Pocket 3
  accepted the missing prepare-transport request. It does not by itself prove
  Wi-Fi association or authorize stream configuration.
- 【已否定假设】The Stage 2 frame is no longer merely an unverified Pocket 3
  constant. Its request/response wire exchange is hardware-validated. The
  meanings of the trailing response bytes after `0000011c00` remain unknown.

## Same-connection Wi-Fi recovery boundary

A fresh private Wi-Fi proposal uses sequence `0x8C1A`, retains the previously
verified SSID/PSK fingerprints, and is bound to the successful prepare-recovery
summary above. Its fixed frame SHA-256 is
`8c4de55a03533eba4ef59d038acada497aedcf33a449c67bd433a411d1fd5a3f`;
the credential-bearing payload remains private and ignored by Git.

The fixed owner command creates one BLE connection and permits exactly:

1. Stage 1 `02/E1` once.
2. Stage 2 `02/8E 00011C00` once, only after the exact Stage 1 ACK.
3. Wi-Fi `07/47` once, only after the exact Stage 2 response.

It then listens passively for 30 seconds. A matching `C0/07/47` response is
recorded verbatim but not over-interpreted; absence of such a response still
requires independent router observation. The command contains no `08/78`,
RTMP URL, stream-start, stream-stop, camera, gimbal, or additional Wi-Fi frame.
Its invocation is consumed before deployment, preventing a second run with the
same authorization.
