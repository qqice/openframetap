# Pocket 3 controlled passive telemetry findings

This document covers the complete body-motion session
`pocket3-experiment-20260718-231048`. The source evidence remains ignored by
Git under `artifacts/remote/`; this file contains only statistics and
de-identified protocol observations. Analysis ran on the Windows source host at
Git `932041e192c5ec22d3f22a23fa133c83c917e6da` and used ROCK monotonic time for
all event relationships.

## Evidence and safety boundary

- 【实机事实】The BLE session lasted 263.651 seconds; the guided procedure
  lasted 242.722 seconds and contained all six whole-body actions plus two
  screen battery observations.
- 【实机事实】10,220 FFF4 notifications produced 10,220 DUML frames. CRC8,
  CRC16, length/reassembly, and active-disconnect failure counts were all zero.
- 【实机事实】ATT MTU was 517. CCCD operation count was two and FFF5 write count
  was exactly zero. No DJI query or application command was sent.
- 【实机事实】The raw `capture.btsnoop`, `btmon.txt`, `notifications.jsonl`,
  `duml-frames.jsonl`, and `events.jsonl` hashes all matched before and after
  analysis.

## Static baseline

The initial stationary interval contained 265 `04/05` samples. Values below
are raw signed little-endian integers; no physical unit or scale is assigned.

| Candidate | Offset/type | Minimum | Maximum | Mean | Baseline standard deviation |
| --- | --- | ---: | ---: | ---: | ---: |
| yaw candidate | 16 / int16 LE | -1014 | -995 | -1003.03 | 5.32 |
| pitch candidate | 20 / int16 LE | 17 | 18 | 17.85 | 0.35 |
| roll candidate | 22 / int16 LE | 0 | 1 | 0.04 | 0.21 |

【统计观察】The low baseline spread, followed by opposite-signed controlled
action deltas, is the evidence used for candidate ranking. It does not by
itself prove an angle, motor angle, target, error, or angular-rate semantic.

## `04/05` controlled-motion candidates

| Axis label | Offset/type | Whole-session raw range | First/opposite action median delta | Minimum action/static-noise ratio | Confidence |
| --- | --- | --- | --- | ---: | --- |
| yaw | 16 / int16 LE | -6645..2352 | left -5326.5; right +3294.5 | 79.38 | medium |
| pitch | 20 / int16 LE | -143..178 | up +47; down -34 | 1.98 | low |
| roll | 22 / int16 LE | -386..425 | clockwise -392.5; counter-clockwise +395 | 97.20 | medium |

【捕获推断】Offsets 16 and 22 are strong direction-sensitive yaw and roll
candidates for this 49-byte payload. Offset 20 is a weaker pitch candidate
because its action-to-static-noise margin is much smaller and other actions
also perturb it.

【待验证假设】The bounded fixed scales `1`, `0.1`, `0.01`, `1/16`, `1/100`,
`1/1000`, `180/32768`, and `360/65536` were tested only as range projections.
No scale is selected. A second controlled session with an external angle
reference is required before naming physical degrees or degrees/second.

【参考实现结论】`lib-osmo-ble` decodes the first three int16 LE fields at
offsets 0/2/4 with scale 0.1 as pitch/roll/yaw. The published
`reverse-engineering-dji` notes instead leave the 49-byte `04/05` payload as an
unknown status report, and `djictl` calls the command `MaybeStatus` without a
field decoder.

【已否定假设】The `lib-osmo-ble` offsets 0/2/4 layout is not valid for the
49-byte payload observed in this session: offset 2 was constant zero, and
offsets 0 and 4 did not show the required opposite-direction controlled-action
response. This rejection is scoped to this Pocket 3 payload layout; it is not a
claim about every DJI product or firmware.

## `04/27`, `04/1C`, and `04/38`

- 【统计观察】`04/27` arrived 2,605 times at 9.9997 Hz and had only two
  payloads: `0080000000` 2,154 times and `0000000000` 451 times.
- 【统计观察】Its six transitions cleared the `0x80` byte 1.882 seconds after
  pitch-down start, 2.616 seconds after clockwise-roll start, and 2.743 seconds
  after counter-clockwise-roll start. Each set transition occurred 2.203–3.271
  seconds after the corresponding manual return-to-neutral marker. There was no
  transition during yaw or pitch-up.
- 【捕获推断】`04/27` is a sparse state/threshold candidate, possibly related
  to a non-neutral orientation or stabilization limit. Its exact bit meaning is
  unknown and it is not a continuous three-axis value.
- 【参考实现结论】`djictl` names `04/27` `KeepAlive`; that name does not explain
  the locally correlated bit transitions and remains unverified.
- 【统计观察】All 521 `04/1C` payloads were `48` at approximately 2 Hz. All 261
  `04/38` payloads were `0000646400` at approximately 1 Hz. Neither changed
  during the controlled actions.
- 【已否定假设】This session provides no support for treating `04/1C` or
  `04/38` as a directly changing yaw/pitch/roll value.

## Battery, device identity, and camera-status candidates

- 【实机事实】The owner entered screen battery values 74 and 72 on the Pocket.
  The nearest `0D/02` offset-20 uint8 values were exactly 74 and 72, separated
  from the event markers by +245.699 ms and -121.244 ms respectively.
- 【统计观察】The comparison contains a real 74-to-72 transition rather than a
  single constant value. The local field confidence is therefore high.
- 【参考实现结论】Both `node-osmo` and `djictl` read the 34-byte `0D/02`
  payload at offset 20 as battery capacity; the older `lib-osmo-ble` path uses
  a conflicting offset 0.
- 【捕获推断】For the observed 34-byte Pocket 3 layout, `0D/02` offset 20 is a
  high-confidence battery-percent field. It is not promoted to `confirmed`
  until repeated in another independently controlled session.
- 【实机事实】All 260 `00/81` payloads were identical and began with ASCII
  `hg212`; the physical device was the owner's Pocket 3.
- 【参考实现结论】The public reverse-engineering notes associate `hg212` with
  the Pocket 3 part number. Local software retains it as a medium-confidence
  product-identifier candidate rather than a universal model code.
- 【已否定假设】All 2,602 `02/80` payloads were identical at approximately
  10 Hz during this already-paired, motion-only session. The earlier
  `pairing_started` semantic remains rejected. The conservative name remains
  `camera_status_02_80_candidate`.

## Unknowns and follow-up boundary

【实机事实】The raw unknown-frame file retained 4,493 frames: `00/74` 65,
`00/F1` 391, `02/DC` 650, `04/1C` 521, `04/27` 2,605, and `04/38` 261.

【待验证假设】Target angle, tracking error, angular velocity, gimbal mode,
camera mode, charging state, temperature, storage, and warning flags remain
unnamed. No active query or control command is justified by this experiment.

