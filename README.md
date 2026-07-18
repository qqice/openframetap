# OpenFrameTap

OpenFrameTap is an open-source remote monitoring and control terminal prototype for DJI Osmo Pocket cameras. This first milestone is deliberately read-only: it audits the ROCK 4D, records the existing display/touch baseline, passively captures BLE advertisements, and can enumerate GATT metadata without pairing, reads, subscriptions, or writes.

The Git repository on the local computer is the only source of truth. Files are deployed through SSH to `~/openframetap-runtime` on the ROCK 4D, while all remote evidence is copied back to ignored local `artifacts/remote/` directories.

## Safety boundary

- The validated runtime baseline is Armbian with `6.1.115-vendor-rk35xx`; these tools never update the kernel, bootloader, DTB, initramfs, firmware, or `/boot`.
- Existing MIPI-DSI display, touch, networking, and wireless drivers are observed but never reconfigured.
- BLE scan is passive. GATT probe only enumerates service metadata and never pairs, reads values, subscribes, or writes—including DJI `FFF5`.
- No stage in this milestone enables AP/P2P, restarts services, reboots the board, or starts video/gimbal/camera control.

## Local workflow

Run POSIX scripts from Git Bash on Windows. Set `ROCK4D_SSH_HOST` when the default task target is not appropriate.

```bash
./scripts/remote.sh command 'uname -a'
./scripts/remote.sh deploy
./scripts/remote.sh doctor
./scripts/remote.sh display-audit
./scripts/remote.sh setup-python
./scripts/remote.sh ble-scan 60
./scripts/remote.sh ble-probe AA:BB:CC:DD:EE:FF
```

`setup-python` creates only `~/openframetap-runtime/.venv` and installs this project plus Bleak there. It never uses `sudo pip` or changes the system Python environment.

## Development

```bash
python -m pip install -e '.[test]'
python -m pytest
python -m openframetap ble scan --replay tests/fixtures/advertisements.json --json /tmp/replay.json
```

See [architecture](docs/architecture.md), [hardware audit](docs/hardware-audit.md), [display baseline](docs/display-baseline.md), [protocol notes](docs/protocol-notes.md), and [roadmap](docs/roadmap.md).
