# Pocket 3 gimbal-control safety matrix

This document records the command research used for OpenFrameTap's bounded
active-control prototype. Cross-product DUML similarities are evidence for
where to look, not proof of Pocket 3 behavior. Raw PWM, calibration, recenter,
mode, camera, Wi-Fi, and livestream commands remain outside this phase's
control allowlist.

## Read-only reference snapshots

| Source | Snapshot | Relevant evidence |
| --- | --- | --- |
| `yigitkonur/lib-osmo-ble` | `021e96c2bec7e9a2a81296292545bc1ee432af49` | Pocket 3 `src/controllers/gimbal.mjs` and `PROTOCOL.md`; code exists for 04/0C but is explicitly WIP and reports no Pocket movement or ACK over BLE-only tests. |
| `o-gs/dji-firmware-tools` | `3b440f8b45264f48e86cdc304aa5d7470e5b7a8b` | DUMLv1 dissector names 04/0C “Ext Ctrl Accel / Speed Control” and independently confirms three little-endian int16 values plus one flag byte; axes and flag bits are unknown. |
| `jdesbonnet/dji_rs3_control` | `689884f2249e721c5b65c9c74804caddc1e7a68c` | RS 3 BLE HCI-derived specification reports real positive/negative pan motion from 04/0C and an ACK; it is another product and its field order/flag differ from the Pocket-specific implementation. |
| `xaionaro-go/djictl` | `ddeced5422fe3a27075602d41b49e61ca60c99d8` | Current snapshot names only observed Pocket 3 04/05 and 04/27 traffic; it has no 04/0C encoder. |
| `xaionaro/reverse-engineering-dji` | `5c9278ff0b53bbe0d03ea6c830aab13ac775871e` | Provides Pocket 3 BLE/DUML framing evidence but no continuous gimbal-control payload. |
| `datagutt/node-osmo`, `eerimoq/moblin` | `cec92aec...`, `4eaf8dd...` | No Pocket 3 gimbal motion implementation in the reviewed snapshots. |

The RS 3 document was reviewed as an independent, real-device capture source,
not as a Pocket 3 implementation. Its warnings and unresolved fields are
retained below.

## Candidate matrix

| Command | Schema and state | ACK | Zero/stop semantics | Risk and decision |
| --- | --- | --- | --- | --- |
| 04/01 raw PWM | 3×uint16; older range 363–1685 | product-dependent | neutral 1024 is not a proven stop on Pocket 3 | Explicitly prohibited. It bypasses a calibrated rate abstraction and can command raw actuator input. |
| 04/0A external angle | 10 bytes; absolute target candidate | request in public Pocket code | no continuous zero-rate semantic | Denied. Persistent target movement is a worse match for a dead-man joystick. |
| 04/0B control status | empty query candidate | unknown | none | Not sent; this phase does not introduce a separate query. |
| **04/0C external accel/speed** | three int16 LE values ×0.1 degree/s candidate plus flags | request/ACK in RS 3 evidence; no Pocket ACK observed by public WIP | zero rates with same control flag is the local stop candidate; RS 3 also documents all-zero flag 0 as release | **Selected for offline/mock and bounded one-shot validation only.** It provides rate limiting and a zero-rate frame. Pocket axis/flag semantics remain unverified. |
| 04/14 absolute angle | three int16 + flags + duration | ACK in RS 3 | target completes rather than dead-man zero | Denied for joystick use. |
| 04/15 movement | 20-byte incremental candidate | unknown | no proven cancel | Denied. |
| 04/4C recenter/mode | product-specific payloads conflict | unknown | autonomous movement | Denied in this phase. |

## Selected experimental profile: `pocket3_speed_04_0c_v0`

The offline profile uses sender app `0x02`, receiver gimbal `0x04`, request flags
`0x40`, command `04/0C`, and a seven-byte payload:

```text
offset  size  encoding  current Pocket 3 hypothesis
0       2     int16le   pitch rate × 10
2       2     int16le   roll rate × 10; always zero in this phase
4       2     int16le   yaw rate × 10
6       1     uint8     control/enable candidate 0x01
```

【参考实现结论】The Pocket-specific WIP source uses this ordering and flag.
Its interactive default is 30 degree/s, so OpenFrameTap treats 30 degree/s as
the *reference* range rather than protocol maximum. A normalized one-shot
output of `0.05` therefore encodes 1.5 degree/s (`15` protocol units), and the
initial live limit of `0.15` would encode at most 4.5 degree/s.

【捕获推断】The independent RS 3 capture confirms the command ID, payload width,
signed little-endian rate values, successful positive/negative pan motion, and
that zero/release frames are accepted. It instead places yaw at offset 0,
pitch at offset 4, and uses flag `0x80` for takeover. This conflict is not
silently reconciled.

【待验证假设】Pocket 3 uses the Pocket-specific offset order and `0x01` flag,
and `00000000000001` means enabled zero rate. Offline CRC and round-trip tests
prove only wire correctness, not motor semantics. The first one-shot test is
designed to resolve exactly one axis at very low output; if yaw manifests
mainly as pitch, ACK/state is unexpected, or zero does not stop motion, active
testing stops rather than trying another field arrangement.

## Stop and safety policy

- One control writer owns FFF5; GUI and input sources never write it directly.
- Initial rate is 10 Hz, watchdog is 300 ms, and the first non-zero interval is
  at most 200 ms with unconditional zero again before 500 ms.
- The candidate zero-rate frame is re-encoded with a fresh sequence and both
  CRCs checked before it can be queued.
- A bounded send failure or inability to confirm that the writer attempted the
  zero frame enters `fault`; no alternate cmdId, flag, or axis order is tried.
- Raw PWM and unknown command IDs are rejected in the sending layer, not just
  hidden from the UI.
- Full live control cannot be enabled merely because mock tests pass. The four
  one-direction Pocket tests must first establish axis direction and stopping.

