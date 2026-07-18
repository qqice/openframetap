# Protocol notes

## Milestone-one boundary

BLE observation uses BlueZ through Bleak and retains manufacturer data, service data, UUIDs, platform fields, address type where exposed, RSSI, TX power, and an accompanying raw `btmon` capture. Names are only one profile signal; random addresses, abbreviated names, and unnamed devices are expected.

GATT probing connects without requesting pairing and enumerates services, characteristics, descriptors, handles, properties, and MTU. It performs no characteristic value reads or writes. If `FFF0`, `FFF4`, or `FFF5` exists, only metadata is recorded. The milestone-one probe path never writes `FFF5`; later pairing writes use a separate human-confirmed path.

DJI company/model interpretation is isolated in `profiles.py`. A profile match is a hypothesis until validated against a real device and captured bytes. Unknown payloads stay intact rather than being rejected or assigned a Pocket model.

## 2026-07-18 live evidence

A clean 30-second capture is archived under `artifacts/remote/ble-20260718-095101/`: 17 devices, a 236,657-byte btsnoop file, and no residual `btmon` process after exit. `OsmoPocket3-DCFB` appeared at public address `E4:7A:2C:36:DC:FC`, RSSI -55 dBm, TX power 0, advertising Battery (`180F`), HID (`1812`), and vendor `FFF0` services. Its manufacturer entry is retained exactly as key `0x08aa` with payload `200080e47a2c36dcfc`; the key and payload are not yet assigned semantics.

A metadata-only GATT enumeration, archived at `artifacts/remote/gatt-20260718T015851Z/`, connected without pairing and then disconnected normally. It found Generic Attribute service plus `FFF0` at handle 40. Within `FFF0`, it found `FFF3` handle 41, `FFF4` handle 44, and `FFF5` handle 47, each with its observed properties and CCCD. The probe records `pair_requested=false`, `value_reads_attempted=false`, and `writes_attempted=false`; it also did not subscribe. BlueZ/Bleak reports its default MTU value of 23, but the probe deliberately did not call a private active-MTU acquisition path, so `negotiated_mtu` remains unknown.

The raw monitor channel requires elevated capture permission on this image. `capture-ble.sh` uses already-verified `sudo -n` only for `btmon`, records the PID, and stops it on success, error, SIGINT, or SIGTERM. BLE scanning itself remains under the unprivileged project virtualenv.

## Stage-two protocol implementation

`protocol/` is independent of Bleak. It implements DJI CRC8/CRC16, structured DUML encoding and decoding, big-endian transaction IDs, and a streaming reassembler that accepts fragments or multiple frames and resynchronizes after invalid length or CRC. The fixture metadata records the source repository commit, file SHA-256, source frame number, direction, and the fact that upstream packet timestamps were unavailable.

The read-only `ble listen` and `pocket3 telemetry` paths subscribe only to `FFF4`. They record wall and monotonic timestamps, every raw notification, every valid DUML frame, decode candidates, unknown frames, CRC/reassembly statistics, and connection events. Incoming ACK-required messages are recorded but never automatically acknowledged.

## 2026-07-18 live FFF4 evidence

Two independent read-only sessions are archived at `artifacts/remote/pocket3-listen-20260718-182046/` and `artifacts/remote/pocket3-listen-20260718-182651/`. They listened for 61.59 and 61.86 seconds and captured 2,358 plus 2,360 notifications. Every notification was one complete CRC-valid DUML frame in these sessions, but the reassembler does not depend on that observation. Across both sessions there were zero CRC8 failures, CRC16 failures, invalid lengths, discarded bytes, or truncated fragments.

The second session contained 60 `00/81` device-info candidates with stable ASCII prefix `hg212`, 60 `0D/02` status candidates with a stable value 100 at reference-derived payload offset 20, 602 `04/05` gimbal-status candidates with changing raw payloads, and six still-unknown command types. These are capture facts; `hg212` as a Pocket 3 product identifier and offset 20 as battery percentage remain protocol interpretations, and gimbal angles are deliberately not decoded yet.

The btmon trace shows an ATT MTU request and response of 517 in both directions. Bleak's public `mtu_size` property still reported its BlueZ default 23 and emitted a warning, so the JSON calls that value backend-reported rather than negotiated. FFF4 notifications use value handle `0x002d`; host writes were limited to CCCD handle `0x002e` for subscribe/unsubscribe. There was no write to FFF5 value handle `0x0030`, no application pairing request, and no BlueZ pairing request.

The second connection had one short setup-stage disconnect before the stable active connection. The active 60-second interval had no interruption. Future summaries split setup disconnect callbacks from active-listen interruptions; the original immutable session summary retains its earlier combined count of one.

## 2026-07-18 first human-executed pairing frame

The owning user manually executed the reviewed `set_pairing_pin` candidate. Evidence is archived at `artifacts/remote/pocket3-manual-frame-20260718-205833/`. The confirmed and written full-frame SHA-256 is `57725c09e6cd6f973161fb3e90fb74f1abc008f089c858df0dd7dc4ac63308a4`; Bleak's public MTU value caused the 34-byte frame to be emitted as two ordered FFF5 ATT Write Commands. No other application frame was written and `automatic_follow_up_frames` is zero.

The Pocket returned CRC-valid `550f04a2070272aac0074500020069` approximately 175 ms after the completed write. This is an exact `C0/07/45`, sequence `72AA`, payload `00 02` response and is therefore a local hardware fact that confirmation was required. About 9.95 seconds later, after the user's Pocket-screen action, the device sent `40/07/46 payload 01`. It sent one request with sequence bytes `0A 00`, followed by nine one-second repetitions using sequence bytes `01 00`. No protocol mismatch or connection interruption occurred.

The repetition is a capture fact; interpreting it as a request awaiting the Mimo-style `C0/07/46 payload 00` ACK is a capture inference corroborated by the published Mimo trace and `djictl`. The next offline candidate mirrors sequence `01 00`, but the manual runtime additionally requires the complete corresponding incoming approval frame to be reobserved before it can write. If that exact prerequisite is absent, the attempt performs zero FFF5 writes.

The subsequent guarded stage-one invocation is archived at `artifacts/remote/pocket3-manual-frame-20260718-211342/`. A new BLE connection delivered 397 CRC-valid passive frames but no `07/45` or `07/46` pairing traffic. The exact prerequisite was absent, so the confirmed stage-one candidate was not written: `writes_attempted=0`, `command_sent=null`, and no FFF5 ATT Write Command appears. This is a safety success and evidence that the approval transaction was connection/session-bound.

The remaining allowed attempt therefore used an owner-operated interactive TTY while preserving the same BLE connection. Every candidate was independently displayed and required its full SHA-256; response callbacks only enqueue evidence and never write. A mismatch or declined hash stops the state machine.

Pairing is DJI application state, not BlueZ bonding. The offline state machine distinguishes an ordinary BLE connection, a DJI pairing-status response, a Pocket-screen approval, and stage-one/stage-two evidence. It emits `propose_*` actions only; it does not call the transport. Each proposed frame requires a separate interactive full-SHA confirmation by the owning user. It permits at most two explicitly initiated attempts, safely ignores an exact duplicate status after the state has advanced, stops on an unexpected payload, timeout, disconnect, or cancellation, and does not guess alternate IDs or payloads.

## 2026-07-18 completed application pairing and passive telemetry

The second and final owner-operated attempt is archived at `artifacts/remote/pocket3-manual-pair-session-20260718-213000/`. The runtime wrote only the same reviewed `set_pairing_pin` frame, SHA-256 `57725c09e6cd6f973161fb3e90fb74f1abc008f089c858df0dd7dc4ac63308a4`. The 34-byte DUML frame was split into two ordered ATT Write Commands; this is transport fragmentation, not two application commands. No stage-one or stage-two frame was proposed or sent, and `automatic_follow_up_frames` is zero.

The Pocket returned CRC-valid `550f04a2070272aac0074500019b5b`: sender/receiver `07/02`, matching sequence `72AA`, flags `C0`, command `07/45`, and payload `00 01`. The first recorded notification timestamp is about 84.8 ms after the completed frame write. The state machine decoded the explicit response as `already_paired`, entered `paired`, and then listened passively for 60 seconds. `bluez_pairing_requested` is false, so this is strong local evidence of DJI application-layer pairing, not BlueZ bonding.

The session ran for 65.78 seconds and captured 2,531 notifications and 2,531 DUML frames. All 2,531 passed CRC8 and CRC16; invalid lengths, discarded bytes, truncated fragments, reassembly failures, and connection interruptions were all zero. The command counts were `00/74` 16, `00/81` 64, `00/F1` 97, `02/80` 643, `02/DC` 161, `04/05` 645, `04/1C` 129, `04/27` 645, `04/38` 65, `07/45` 1, and `0D/02` 65.

The 65 `0D/02` payloads all had the plausible value 100 at reference-derived offset 20, but this remains a medium-confidence battery candidate until correlated with the Pocket display. The 64 `00/81` frames retained stable ASCII `hg212`; mapping that token to Pocket 3 remains medium-confidence. All 645 `04/05` raw payloads were distinct, supporting a live gimbal-status family classification, but no pitch/roll/yaw field is decoded without a controlled physical correlation.

The immutable session artifact used the then-current label `pairing_started` for 643 `02/80` frames. Because those frames continued at roughly 10 Hz before and throughout the 60-second interval after the explicit `already_paired` response, the local capture disproves that semantic label. The decoder now reports `camera_status_02_80_candidate` at low confidence, preserves the raw payload, and explicitly records that the prior pairing-started interpretation was rejected. The source fixture's historical label remains only as provenance.

See `reference-matrix.md` for public-source agreement, contradictions, and confidence labels.

## Controlled telemetry experiment implementation

The new `pocket3 experiment` entrypoint is a passive FFF4 session and is not a continuation of pairing. It uses the ROCK 4D wall and monotonic clocks for both key events and notifications, records ATT-MTU/CCCD/FFF5 counters, and fails closed if `fff5_write_count` is not exactly zero. The CLI refuses a non-TTY invocation before opening BLE. The local wrapper uses a TTY only for this subcommand and still uses the existing deployment and evidence-pull boundary.

The capture wrapper starts `btmon`, guarantees cleanup through its existing trap, and writes SHA-256 entries for `capture.btsnoop`, `btmon.txt`, `notifications.jsonl`, `duml-frames.jsonl`, and `events.jsonl` only after `btmon` stops. Ctrl+C and normal `q` exit both run the disconnect/finalize path. No new third-party Python dependency is used.

Offline analysis groups each command by payload length before enumerating bounded aligned 1-, 2-, 3-, and 4-byte candidates, changing bit fields, reasonable float32 values, and printable ASCII runs. It retains fixed scaling candidates rather than fitting arbitrary scales. Event-direction scores, static variance, nearest-neighbor unmatched ratios, Pearson/Spearman relationships, first differences, fixed ratios, sign inversions, and lag candidates remain statistics rather than field names.

The analyzer refuses a session whose FFF5 counter is nonzero or whose raw checksum manifest is incomplete or changed. Results are written only below `analysis/`; a second checksum verification proves the raw evidence remained unchanged. `02/80` remains a low-confidence camera-status family and its old `pairing_started` interpretation remains rejected.

The complete controlled body-motion session is archived at `artifacts/remote/pocket3-experiment-20260718-231048/`; detailed statistics and rejected layouts are in `telemetry-findings.md`. It recorded 10,220 CRC-valid frames with zero reassembly failures, zero active disconnects, two CCCD operations, and exactly zero FFF5 writes. Screen observations 74 and 72 matched `0D/02` offset 20 exactly, promoting that candidate to high confidence for the observed 34-byte Pocket 3 layout. Controlled direction changes support medium-confidence yaw and roll candidates at `04/05` int16 LE offsets 16 and 22 and a low-confidence pitch candidate at offset 20, but no physical scale is selected. `04/27` behaved as a sparse state flag, while `04/1C` and `04/38` stayed constant.

Analysis was performed on the Windows source host with ROCK monotonic timestamps at Git `932041e192c5ec22d3f22a23fa133c83c917e6da`. The five raw evidence hashes matched both before and after the analysis. No raw artifact or device address is committed.

## Research inputs

Future Pocket 3 research may compare `lib-osmo-ble`, `djictl`, `node-osmo`, and Moblin, but constants and packet layouts must be verified on the target device before use. Pocket 4 and Pocket 4P require separate capability profiles and cannot inherit Pocket 3 behavior by assumption.
