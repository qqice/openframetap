# Passive Pocket 3 telemetry experiment

This workflow records actions performed manually by the device owner and FFF4 notifications received on the ROCK 4D. It never sends a DJI application command, query, gimbal action, camera action, or FFF5 byte. Its only BLE writes are the standard CCCD operations used by BlueZ to enable and disable FFF4 notifications.

## Before starting

- Close DJI Mimo.
- Put the Pocket and ROCK 4D in a safe, stable location.
- Use Git Bash on the Windows host so the local wrapper has a real TTY.
- Allow roughly 180–240 seconds without extending the session to force a battery transition.
- The Pocket address defaults to the validated profile address and can be overridden once with `POCKET3_BLE_ADDRESS`.

Run only after the physical experiment is explicitly approved:

```bash
./scripts/remote.sh pocket3-experiment 180
```

The command deploys the current local Git source, opens one interactive SSH TTY, starts `btmon`, and runs:

```bash
python -m openframetap pocket3 experiment \
  --duration 180 \
  --output artifacts/pocket3-experiment-YYYYMMDD-HHMMSS
```

## Keys and sequence

```text
r  ready; start the timed procedure
0  baseline/static
1  body yaw left start
2  body yaw right start
3  body pitch up start
4  body pitch down start
5  body roll clockwise start
6  body roll counter-clockwise start
7  return to neutral
8  local recenter action
9  record displayed battery
m  mark custom event
s  mark stable interval
e  end current action
q  stop and save
```

The 180-second countdown begins only after `r`, so reading the guide and positioning the Pocket does not consume the controlled-action interval.

For a 240-second body-motion session, use this exact schedule after `r`:

```text
00:02  9, enter starting battery
00:07  0, stationary 10 s
00:17  s, stationary another 10 s
00:27  e
00:30  1, yaw left: move 5 s / s / hold 5 s / 7 / return 5 s / s / neutral 3 s / e
00:50  2, repeat for yaw right
01:10  3, repeat for pitch up
01:30  4, repeat for pitch down
01:50  5, repeat for clockwise roll
02:10  6, repeat for counter-clockwise roll
02:30  9, enter ending battery
02:35  q, save and stop
```

All six actions move the complete Pocket body. Do not use the joystick, touch-screen rotation mode, key `8`, or direct force on the gimbal head.

Start and end with key `9` and enter the integer displayed on the Pocket screen. Use `0`, wait at least 10 seconds, `s`, wait another 10 seconds, then `e` for the baseline. For each yaw, pitch, and roll direction, mark the start, move slowly by hand, mark `s` while holding, press `7` before returning, mark `s` after reaching neutral, and press `e`. Key `8` only marks an action the owner performs on the Pocket itself. Key `m` records a skipped step or irregularity.

Pressing `q` or Ctrl+C stops the notification subscription, disconnects BLE, stops `btmon`, flushes JSONL and session files, and generates checksums. The shell trap also removes the recorded `btmon` PID. A session with any FFF5 write count is a safety failure and must not be analyzed.

## Evidence and analysis

The remote directory and pulled local directory contain:

```text
capture.btsnoop
btmon.txt
notifications.jsonl
duml-frames.jsonl
events.jsonl
session.json
message-counts.json
unknown-frames.jsonl
checksums.sha256
```

Analyze the completed remote session and pull the results with:

```bash
./scripts/remote.sh analyze-telemetry pocket3-experiment-YYYYMMDD-HHMMSS
```

The direct offline CLI is:

```bash
python -m openframetap analyze telemetry \
  artifacts/pocket3-experiment-YYYYMMDD-HHMMSS
```

Analysis outputs are isolated below `analysis/`. They contain bounded field candidates, alignment unmatched ratios for 50/100/250 ms windows, correlations, message-pair relationships, a provenance-preserving candidate state model, a Markdown report, and separate PNG plots. Raw checksums are checked before and after analysis.

Statistical correlation does not confirm a protocol semantic. Candidate confidence uses `confirmed`, `high`, `medium`, `low`, `unknown`, or `rejected`; every state value retains its command, payload offset, raw value, encoding, timestamp, confidence, and evidence session. A constant battery value matching the screen is reported as `screen_correlated_but_no_transition_observed`, not as a fully confirmed battery protocol.
