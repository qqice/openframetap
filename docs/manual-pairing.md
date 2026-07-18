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

## Stage-one candidate after first-frame analysis

The first human-executed request produced exact `00 02` confirmation-required status and repeated `40/07/46 payload 01` approval requests. The modal/latest approval frame was:

```text
550e046607020100400746019767
```

The generated ACK mirrors that sequence and swaps sender/receiver:

```text
550e046602070100c0074600b23c
SHA-256 1e10d392c3d61b8e51610182e6a74418eaa19685ef56fb8e5f9251b5292d46c2
```

Because the sequence may be session-specific, the runtime must reobserve the complete prerequisite frame before writing. The owner-only command therefore supplies both files:

```bash
./scripts/remote.sh pocket3-send-frame \
  artifacts/local/proposed-pairing-stage1-frame.bin \
  pairing_stage1_ack \
  10 \
  artifacts/local/proposed-pairing-stage1-prerequisite.bin
```

If the prerequisite changes or is absent, the command captures evidence and exits with zero FFF5 writes. Even after a successful stage-one ACK, it cannot automatically send stage two.

The owner executed this guarded command after the original connection had closed. The prerequisite was no longer present, so the runtime timed out after capturing 397 valid passive frames and performed zero FFF5 writes. This verifies that an approval transaction cannot safely be carried across BLE sessions.

## Session-bound final attempt

Completing a transaction with a session-specific approval sequence requires keeping one BLE connection open. The owner-only interactive workflow still requires a separate full SHA-256 for every frame:

```bash
./scripts/remote.sh pocket3-manual-pair-session 60
```

Safety properties:

- It refuses non-TTY or piped execution and first requires the owner to type `RUN`.
- The reviewed `set_pairing_pin` frame is displayed and requires its complete SHA-256.
- An exact `C0/07/45` response is required. Any different address, sequence, flags, command, or payload stops the session.
- If confirmation is required, the terminal pauses while the owner confirms on the Pocket screen.
- Stage one is generated from the exact live `40/07/46` approval sequence in that still-open connection, displayed in full, and separately confirmed by SHA-256.
- Stage two is then displayed in full and separately confirmed. Declining or mistyping either hash stops before that frame.
- No notification callback sends data; no frame is selected without a human prompt; no command outside the three-entry pairing allowlist can be sent.
- After the prompts it only records the stage-two response, if any, and passively listens for the requested telemetry duration.

This workflow constitutes the second and final permitted application-pairing attempt. Codex must not invoke it.

## State separation

BlueZ pairing/bonding is not requested. The single frame targets DJI application-layer pairing. A Pocket-screen confirmation is a separate human action. Seeing a BLE connection, continuous telemetry, or an unchanged connection is not by itself evidence that DJI application pairing succeeded.
