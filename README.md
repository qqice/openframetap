# OpenFrameTap

OpenFrameTap is an open-source remote monitoring and control terminal prototype for DJI Osmo Pocket cameras. The current milestone includes a tested DJI DUML codec, streaming frame reassembly, passive `FFF4` notification capture, conservative telemetry recording, hardware-validated Pocket 3 application pairing, controlled passive telemetry experiments, and a LAN-only user-space RTMP receiver. Pocket livestream commands remain proposal-only and independently user-gated.

The Git repository on the local computer is the only source of truth. Files are deployed through SSH to `~/openframetap-runtime` on the ROCK 4D, while all remote evidence is copied back to ignored local `artifacts/remote/` directories.

## Safety boundary

- The validated runtime baseline is Armbian with `6.1.115-vendor-rk35xx`; these tools never update the kernel, bootloader, DTB, initramfs, firmware, or `/boot`.
- Existing MIPI-DSI display, touch, networking, and wireless drivers are observed but never reconfigured.
- BLE scan is passive. GATT probe only enumerates service metadata. The listen path subscribes to `FFF4` but never calls the `FFF5` sender or auto-ACKs incoming messages.
- The sender is fail-closed and hard-denies gimbal, camera, firmware, and any unapproved streaming or Wi-Fi command. Each new livestream command type requires a fixed proposal and its own one-command authorization.
- External-state BLE writes are human initiated one frame at a time. The local wrapper requires the owner to type the complete frame SHA-256 and the runtime never sends an automatic follow-up frame.
- The telemetry experiment records manual key events and notifications on the ROCK 4D monotonic clock. It fails the session if its FFF5 write counter is nonzero and verifies raw evidence checksums before and after analysis.
- No stage enables AP/P2P, changes NetworkManager/firewall/routes, reboots the board, or starts gimbal/camera control. MediaMTX runs only as an explicitly managed user process bound to `wlan0`.

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
./scripts/remote.sh ble-listen 60
./scripts/remote.sh pocket3-pair-status
./scripts/remote.sh pocket3-pair
./scripts/remote.sh pocket3-telemetry 60
./scripts/remote.sh pocket3-experiment 180
./scripts/remote.sh analyze-telemetry pocket3-experiment-YYYYMMDD-HHMMSS
./scripts/remote.sh rtmp-install
./scripts/remote.sh rtmp-doctor
./scripts/remote.sh rtmp-start
./scripts/remote.sh rtmp-status
./scripts/remote.sh rtmp-stop
./scripts/remote.sh rtmp-self-test
./scripts/remote.sh pocket3-rtmp-send-approved-prepare
./scripts/remote.sh pocket3-rtmp-configure-wifi-secrets
./scripts/remote.sh pocket3-rtmp-propose-wifi
```

The final command is an owner-operated, interactive, single-send wrapper for
the fixed `prepare_to_live_stream` proposal only. It verifies the approved
frame SHA-256 before deployment and still requires the full SHA-256 to be typed
at the terminal. It does not authorize or send Wi-Fi credentials, an RTMP URL,
`02/8E`, stop, or any automatic follow-up frame.

The Wi-Fi secret setup command requires a real TTY and uses hidden-input
prompts. It writes only `~/.config/openframetap/secrets.env` at mode `0600`,
backs up an existing private file, and never prints the SSID or PSK. The Wi-Fi
proposal command is offline with respect to the Pocket: it builds `07/47` under
private artifacts, emits only a sanitized summary, and leaves the proposal
unsent pending a separate B-class authorization.

The manual single-frame writer is documented separately and must only be invoked interactively by the device owner; Codex does not run it:

```bash
./scripts/remote.sh pocket3-send-frame artifacts/local/proposed-pairing-frame.bin set_pairing_pin 20
```

For a transaction whose sequence is valid only inside one BLE connection, the owner may use the TTY-only workflow. It independently prompts for every frame SHA and cannot be invoked through a pipe or non-interactive Codex command:

```bash
./scripts/remote.sh pocket3-manual-pair-session 60
```

The permitted pairing attempts are now complete. The command remains documented for reproducibility but must not be run again in this milestone.

`setup-python` creates only `~/openframetap-runtime/.venv` and installs this project plus Bleak there. It never uses `sudo pip` or changes the system Python environment.

## Development

```bash
python -m pip install -e '.[test]'
python -m pytest
python -m openframetap ble scan --replay tests/fixtures/advertisements.json --json /tmp/replay.json
python -m openframetap duml decode --hex 550e046604026b1300041c48e5e2
python -m openframetap video server doctor
python -m openframetap pocket3 rtmp plan
python -m openframetap pocket3 rtmp status
python -m openframetap pocket3 rtmp analyze-prepare <private-capture-directory> --sanitized-output <sanitized-directory>
python -m openframetap pocket3 rtmp propose wifi --secret-file <private-0600-file> --prepare-result <validated-result>
```

See [architecture](docs/architecture.md), [Pocket 3 RTMP protocol](docs/pocket3-rtmp-protocol.md), [telemetry experiment](docs/telemetry-experiment.md), [manual pairing](docs/manual-pairing.md), [reference matrix](docs/reference-matrix.md), [hardware audit](docs/hardware-audit.md), [display baseline](docs/display-baseline.md), [protocol notes](docs/protocol-notes.md), and [roadmap](docs/roadmap.md).
