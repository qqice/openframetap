# Pocket 3 normal-mode connection

Local source: Windows `openframetap`; target: ROCK 4D `~/openframetap-runtime`.
This workflow joins Pocket's SoftAP as a station and receives its native video.
It is separate from the external-router RTMP workflow.

```powershell
& 'C:\Program Files\Git\bin\bash.exe' ./scripts/remote.sh normal-preview 120
```

The bounded session reads credentials over BLE, creates a temporary in-memory
NetworkManager profile, waits for DHCP, binds UDP to the assigned `192.168.2.x`
address with an ephemeral port, performs the existing captured handshake, sends
40 Hz three-window ACKs, opens the UDP application session, answers the camera's
`00/81` and `00/82` APP-registration challenges, and sends one native-view enable.
Reassembled AVC goes
through `appsrc -> h264parse -> mppvideodec -> waylandsink`. Python handles only
compressed bytes; decoded pixels stay in GStreamer. The online assembler emits
as soon as all fragments and the declared length agree, without a one-frame wait.

Duration expiry, Ctrl+C, connection failure and pipeline failure run cleanup:
close BLE/UDP/GStreamer, stop the exact owned capture groups, remove the temporary
profile and reconnect the previously active WLAN UUID. `end0` must remain the
best default route; the Pocket profile supplies neither a default route nor DNS.
No pre-existing NetworkManager profile is edited. The password is supplied via a
0600 file in `/run/user/<uid>`, removed after activation; it is not an argv value.
Raw private btmon captures can contain credentials and must never be published.

If a process is forcibly killed before cleanup, recover the saved network state:

```powershell
& 'C:\Program Files\Git\bin\bash.exe' ./scripts/remote.sh normal-cleanup
```

The recovery command takes the same session lock and checks the owned profile
name and UUID. It cannot delete an unrelated profile. This is not a boot service
or unattended infinite reconnection loop.

## Protocol evidence and scope

| Operation | Sender / receiver | Command and payload | Evidence |
|---|---|---|---|
| Open session | 02 / F0 | 00/2B, 04 00 | Pocket 3 Mimo PCAP + Osmosis + OpenPocketCine |
| Existing pairing check | 02 / 07 | 07/45, existing project identifier/PIN | Previous OpenFrameTap physical pairing; only `00 01` accepted |
| Read SSID | 02 / 07 | 07/07, empty | Pocket 3 Mimo PCAP + both references |
| Read password | 02 / 07 | 07/0E, empty | Pocket 3 Mimo PCAP + both references |
| BLE heartbeat | 02 / F0 | 00/2B, 01 01 | Osmosis session flow + OpenPocketCine |
| APP presence | 02 / 28 | 00/88, captured 14-byte APP record | Pocket 3 Mimo PCAP, registration concept in both references |
| Registration response | 02 / 48 | 00/81: captured 64-byte APP record; 00/82: 00; flags 80, request sequence echoed | 69 matching request/response pairs in own Pocket 3 capture |
| Native video enable | 02 / **41** | 09/A8, 00 04 02 00 00 00 00 00 00 00 | Pocket 3 PCAP request and `C0/09/A8 payload 00` response |

Reference snapshots: OpenPocketCine `e8f272eb428248fec48ad4bfda1734a3ce7902d3`,
`Sources/OpenPocketViewCore/Commands.swift`, `docs/live-session.md`; Osmosis
`f1fcfa9dcce423d9295ece27c36043abce213de8`, `MEDIA_PROTOCOL.md` sections 20–21.
Own source: `mimo-gimbal-wifi.pcap`, SHA-256
`8e7c7eb62acd32429bd0d176e3da2b7d1f688854a3ee79c4017eb631139a181c`.

【捕获推断】Pocket 3's native enable receiver is `0x41`. Do not substitute the
Pocket 4 default `0x08` from OpenPocketCine. Credentials use exact-length
`status:u8 + length:u8 + UTF-8`; refused/truncated/unexpected strings stop the flow.

【参考实现结论】Osmosis records `53/10` returning E0 on Pocket 3, whose AP comes
up through `00/2B`; this workflow omits `53/10`. No shooting-format kick, camera
mode change, camera capture, RTMP provisioning or gimbal movement is part of this
session. An absent first picture after one enable is reported, not remedied by
changing recording format or sending speculative commands.

Evidence is one timestamped `artifacts/private/normal-session-*` directory:
events, btmon, UDP PCAP, recovered H.264, summary and SHA-256 manifest. The wrapper
pulls it back to Windows even when the session fails. The network recovery state
is `runtime/normal-network.json` and contains only profile UUIDs.

## Execution and validation

Since 2026-09-06, all tests and analysis execute on ROCK 4D. Windows is used for
source editing, Git, SSH orchestration and local evidence archiving only.
`./scripts/remote.sh remote-test` deploys and runs the suite on the board.

【实机事实】The first attempt joined the hotspot and completed UDP handshake but
received no video: it omitted APP registration while Pocket repeatedly requested
`00/81`. After implementing the captured registration sequence, the next 30-second
run received 7,995 media packets / 917 complete units and rendered 889 frames with
MPP. One enable was sent. Network rollback succeeded. This establishes the full
connection path; final scheduling and cleanup verification is recorded below.
