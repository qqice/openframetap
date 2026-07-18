# Manual per-frame Pocket 3 pairing workflow

All application-layer BLE writes in this experiment are initiated by the human who physically owns the ROCK 4D and Pocket 3. Codex may generate, validate, document, and analyze a candidate, but it must not invoke the write action.

## Invariants

- One invocation can transmit exactly one complete, allowlisted DUML frame.
- The local wrapper displays the target, command, full frame hex, and SHA-256 before opening SSH.
- The user must type the full 64-character SHA-256 interactively. A mismatch exits before deployment, SSH, or BLE connection.
- The remote runtime independently checks the confirmation hash, parses the frame, verifies CRC8/CRC16, applies the command deny/allow policy, connects, subscribes to FFF4, writes the one confirmed frame, and then only listens.
- It never generates, selects, or sends a follow-up frame. `automatic_follow_up_frames` is fixed to zero in the evidence summary.
- Gimbal, camera, video, Wi-Fi, RTMP, firmware, and other non-pairing commands remain denied.

## First candidate

The current first candidate is stored only in ignored local evidence:

```text
artifacts/local/proposed-pairing-frame.bin
SHA-256 57725c09e6cd6f973161fb3e90fb74f1abc008f089c858df0dd7dc4ac63308a4
```

The owning user may execute it from Git Bash on the Windows computer:

```bash
./scripts/remote.sh pocket3-send-frame \
  artifacts/local/proposed-pairing-frame.bin \
  set_pairing_pin \
  20
```

The wrapper then asks the user to type the complete SHA-256. Do not pipe, pre-fill, or automate that input. Codex must not run this command.

The command starts btmon, sends only the confirmed frame, listens for 20 seconds, stops btmon through the existing trap, and pulls the evidence directory back under. If an exact `00 02` pairing-status response is observed, the terminal prints `USER_ACTION_REQUIRED` and asks the user to inspect and confirm on the Pocket screen. An observed `400746` approval is reported, but still cannot trigger another BLE frame.

```text
artifacts/remote/pocket3-manual-frame-YYYYMMDD-HHMMSS/
```

The directory contains the raw btsnoop, btmon text, raw notifications, parsed DUML frames, unknown frames, transport events, `transmission.json`, and `summary.json`. No stage-one or stage-two candidate may be executed until that directory has been analyzed and the observed response is an exact expected state transition.

## State separation

BlueZ pairing/bonding is not requested. The single frame targets DJI application-layer pairing. A Pocket-screen confirmation is a separate human action. Seeing a BLE connection, continuous telemetry, or an unchanged connection is not by itself evidence that DJI application pairing succeeded.
