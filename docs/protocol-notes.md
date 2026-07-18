# Protocol notes

## Milestone-one boundary

BLE observation uses BlueZ through Bleak and retains manufacturer data, service data, UUIDs, platform fields, address type where exposed, RSSI, TX power, and an accompanying raw `btmon` capture. Names are only one profile signal; random addresses, abbreviated names, and unnamed devices are expected.

GATT probing connects without requesting pairing and enumerates services, characteristics, descriptors, handles, properties, and MTU. It performs no characteristic value reads or writes. If `FFF0`, `FFF4`, or `FFF5` exists, only metadata is recorded. `FFF5` is never written.

DJI company/model interpretation is isolated in `profiles.py`. A profile match is a hypothesis until validated against a real device and captured bytes. Unknown payloads stay intact rather than being rejected or assigned a Pocket model.

## 2026-07-18 live evidence

A clean 30-second capture is archived under `artifacts/remote/ble-20260718-095101/`: 17 devices, a 236,657-byte btsnoop file, and no residual `btmon` process after exit. `OsmoPocket3-DCFB` appeared at public address `E4:7A:2C:36:DC:FC`, RSSI -55 dBm, TX power 0, advertising Battery (`180F`), HID (`1812`), and vendor `FFF0` services. Its manufacturer entry is retained exactly as key `0x08aa` with payload `200080e47a2c36dcfc`; the key and payload are not yet assigned semantics.

A metadata-only GATT enumeration, archived at `artifacts/remote/gatt-20260718T015851Z/`, connected without pairing and then disconnected normally. It found Generic Attribute service plus `FFF0` at handle 40. Within `FFF0`, it found `FFF3` handle 41, `FFF4` handle 44, and `FFF5` handle 47, each with its observed properties and CCCD. The probe records `pair_requested=false`, `value_reads_attempted=false`, and `writes_attempted=false`; it also did not subscribe. BlueZ/Bleak reports its default MTU value of 23, but the probe deliberately did not call a private active-MTU acquisition path, so `negotiated_mtu` remains unknown.

The raw monitor channel requires elevated capture permission on this image. `capture-ble.sh` uses already-verified `sudo -n` only for `btmon`, records the PID, and stops it on success, error, SIGINT, or SIGTERM. BLE scanning itself remains under the unprivileged project virtualenv.

## Later research inputs

Future Pocket 3 research may compare `lib-osmo-ble`, `djictl`, `node-osmo`, and Moblin, but constants and packet layouts must be verified on the target device before use. Pocket 4 and Pocket 4P require separate capability profiles and cannot inherit Pocket 3 behavior by assumption.
