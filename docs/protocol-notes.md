# Protocol notes

## Milestone-one boundary

BLE observation uses BlueZ through Bleak and retains manufacturer data, service data, UUIDs, platform fields, address type where exposed, RSSI, TX power, and an accompanying raw `btmon` capture. Names are only one profile signal; random addresses, abbreviated names, and unnamed devices are expected.

GATT probing connects without requesting pairing and enumerates services, characteristics, descriptors, handles, properties, and MTU. It performs no characteristic value reads or writes. If `FFF0`, `FFF4`, or `FFF5` exists, only metadata is recorded. `FFF5` is never written.

DJI company/model interpretation is isolated in `profiles.py`. A profile match is a hypothesis until validated against a real device and captured bytes. Unknown payloads stay intact rather than being rejected or assigned a Pocket model.

## Later research inputs

Future Pocket 3 research may compare `lib-osmo-ble`, `djictl`, `node-osmo`, and Moblin, but constants and packet layouts must be verified on the target device before use. Pocket 4 and Pocket 4P require separate capability profiles and cannot inherit Pocket 3 behavior by assumption.
