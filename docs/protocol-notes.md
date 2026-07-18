# Protocol notes

## Milestone-one boundary

BLE observation uses BlueZ through Bleak and retains manufacturer data, service data, UUIDs, platform fields, address type where exposed, RSSI, TX power, and an accompanying raw `btmon` capture. Names are only one profile signal; random addresses, abbreviated names, and unnamed devices are expected.

GATT probing connects without requesting pairing and enumerates services, characteristics, descriptors, handles, properties, and MTU. It performs no characteristic value reads or writes. If `FFF0`, `FFF4`, or `FFF5` exists, only metadata is recorded. `FFF5` is never written.

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

Pairing is DJI application state, not BlueZ bonding. The state machine distinguishes an ordinary BLE connection, a DJI pairing-status response, a Pocket-screen approval, and stage-one/stage-two responses. It permits at most two explicitly initiated attempts, safely ignores an exact duplicate status after the state has advanced, stops on an unexpected payload, timeout, disconnect, or cancellation, and does not guess alternate IDs or payloads.

See `reference-matrix.md` for public-source agreement, contradictions, and confidence labels.

## Research inputs

Future Pocket 3 research may compare `lib-osmo-ble`, `djictl`, `node-osmo`, and Moblin, but constants and packet layouts must be verified on the target device before use. Pocket 4 and Pocket 4P require separate capability profiles and cannot inherit Pocket 3 behavior by assumption.
