# Pocket 3 gimbal-control safety matrix

This document records the command research used for OpenFrameTap's bounded
active-control prototype. Cross-product DUML similarities are evidence for
where to look, not proof of Pocket 3 behavior. Raw PWM, calibration, recenter,
mode, camera, Wi-Fi provisioning, and livestream commands remain outside the
gimbal-control allowlist. The sole active-control exception is the structured,
capture-verified Wi-Fi `04/01` profile described below; arbitrary raw payloads
and every other raw-PWM form remain prohibited.

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
| 04/01 structured Pocket 3 stick | Public sources describe 3×uint16, range 363–1685; the Pocket 3 Mimo Wi-Fi capture proves a 10-byte Pocket-specific form | no ACK in the capture | Mimo centers pitch/yaw at 1024 and sends a centered frame on every release | **Narrowly allowlisted over UDP only.** Callers provide normalized yaw/pitch, while the encoder fixes roll=0, offset 6=`0x8000`, offset 8=`0x0042`, endpoints and flags. Raw bytes and other variants are rejected. |
| 04/0A external angle | 10 bytes; absolute target candidate | request in public Pocket code | no continuous zero-rate semantic | Denied. Persistent target movement is a worse match for a dead-man joystick. |
| 04/0B control status | empty query candidate | unknown | none | Not sent; this phase does not introduce a separate query. |
| 04/0C external accel/speed | three int16 LE values ×0.1 degree/s candidate plus flags | request/ACK in RS 3 evidence; no Pocket ACK observed by public WIP | zero rates with same control flag was the local stop candidate; RS 3 also documents all-zero flag 0 as release | The current BLE profile was rejected by the bounded Pocket test. Wi-Fi acceptance is unverified and it remains denied. |
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

## Pocket 3 one-shot result: 2026-07-19

Evidence session: private capture `gimbal-test-20260719-220259` (not committed).
All files named by its manifest passed SHA-256 verification after being pulled
back from the ROCK 4D runtime.

【实机事实】The owner observed no visible gimbal movement during the yaw-positive
test. OpenFrameTap sent exactly one non-zero `04/0C` frame and two zero frames:

| Relative action | Sequence | Payload | SHA-256 |
| --- | ---: | --- | --- |
| yaw-positive candidate | 0 | `000000000f0001` | `9512fd68a073189abc6dbd5fcdf5bee6037519ff9a887b1bd2c3e496c52a410b` |
| release zero | 1 | `00000000000001` | `4621e5eee5b189ba453b0092de3645950b5b57e046093e9f7309006ffce8e439` |
| redundant zero | 2 | `00000000000001` | `eedc9801d884d408c71737e1a6ce2438d269f13a60b9d864962b343fdc0e47e2` |

The first zero write completed about 207.4 ms after the non-zero attempt; the
redundant zero completed about 512.6 ms after it. ATT MTU was 517, every frame
was one ATT write, FFF5 write count was three, FFF4 CCCD operation count was
two, and there were no disconnect, CRC, or reassembly failures. The final
control state was `disabled` and the final output was zero.

【统计观察】The bounded session received 45 DUML frames, including 25 gimbal
frames. No incoming `04/0C` response or ACK was observed. Eleven `04/05`
samples covered roughly -562 to +436 ms around the non-zero write. The existing
yaw candidate at payload offset 16 remained exactly `-3755`; offset 20 varied
only from 6 to 7, offset 22 stayed zero, and the earlier raw fields at offsets
0 and 8 were constant. This does not show a motion-correlated response.

【捕获推断】The tested `pitch, roll, yaw, flag=0x01` form was accepted by the BLE
transport but was probably ignored by Pocket firmware at the application layer.
Lack of an ACK alone is not proof because the public Pocket implementation also
reports no ACKs; the independent human observation and unchanged telemetry make
“movement occurred but was merely too small to see” less likely.

【参考实现结论】The public Pocket-specific implementation explicitly labels its
gimbal controller WIP and reports that more than twenty BLE command variations
were ignored. Public issue #2 independently reports the same symptom: telemetry
works but control has no effect. The RS 3 capture proves `04/0C` on RS 3, not on
Pocket 3, and its axis order and flag conflict with the Pocket-specific code.

【已否定假设】`pocket3_speed_04_0c_v0` is not a hardware-validated Pocket 3
control profile. This single bounded test does not prove that every `04/0C`
variant is unsupported, but it does reject promoting the current payload to
the live allowlist.

Safety decision: do not increase output, do not try the opposite direction or
pitch, and do not substitute the RS 3 field order/flag. Full touch live control
remains fail-closed. The next useful evidence is an Android Bluetooth HCI snoop
and, if BLE contains no joystick frames, a simultaneous owned-LAN packet capture
while DJI Mimo visibly moves Pocket 3 from its preview screen. That capture must
be analyzed before proposing another command.

OpenFrameTap now provides an immutable-capture analyzer for that evidence:

```bash
python -m openframetap analyze hci-gimbal android-btsnoop_hci.log \
  --output artifacts/local/android-mimo-gimbal-analysis.json
```

It supports both Android/HCI-UART BTSnoop datalink 1002 and BlueZ btmon Linux
Monitor datalink 2001, extracts ATT write/notification payloads, performs DUML
stream reassembly and CRC validation, and reports every app-to-device gimbal
frame without modifying the source capture. Replaying the one-shot session's
btmon capture found exactly the three expected `04/0C` writes and zero
reassembly errors, providing a fixture-independent validation of the extractor.

## DJI Mimo Wi-Fi joystick capture: 2026-07-19

Private evidence: `artifacts/private/mimo-gimbal-wifi.pcap`, SHA-256
`8e7c7eb62acd32429bd0d176e3da2b7d1f688854a3ee79c4017eb631139a181c`.
The raw capture is not committed. Its immutable offline result is generated by:

```bash
python -m openframetap analyze mimo-wifi-gimbal \
  artifacts/private/mimo-gimbal-wifi.pcap \
  --output artifacts/local/mimo-gimbal-wifi-analysis.json
```

【实机事实】The 98.841-second PCAP contains 52,461 packets. DJI Mimo used
`192.168.2.224:54232 -> 192.168.2.1:9004/UDP`; every one of the 494 joystick
DUML frames began at UDP payload offset 20, passed both CRCs, and decoded as
app `02` -> gimbal `04`, flags `00`, command `04/01`, ten-byte payload. There
was no `04/01` response or ACK.

【实机事实】Four groups exactly matched the owner's recorded order: yaw right,
yaw left, pitch up, pitch down. The payload's five little-endian uint16 values
behaved as follows:

```text
offset  observed role                 neutral  observed action range
0       pitch stick                   1024     836..1256
2       roll/unused sentinel          0        always 0
4       yaw stick                     1024     810..1295
6       Pocket-specific constant      32768    always 32768
8       Pocket-specific constant      66       always 66
```

Yaw right raised offset 4 to 1295; yaw left lowered it to 810. Pitch up raised
offset 0 to 1256; pitch down lowered it to 836. Cross-axis changes were small.
The exact centered payload was `00040000000400804200`; it occurred 18 times,
including at both the beginning and end of every action group. Mimo then sent
no more `04/01` frames during the 10–11 second gaps.

【统计观察】The action groups contained 110, 116, 118, and 150 frames and ran at
observed group-average rates of 62.3, 43.0, 59.5, and 47.8 Hz. The existing
`04/05` yaw candidate at offset 16 changed by +3747 and -4583 with a 36000-unit
wrap for right and left. The pitch candidate at offset 20 changed by -348 and
+424 for up and down. This independently strengthens the axis mapping, but no
new physical-unit scale is promoted solely from this capture.

【实机事实】Mimo also sent `04/50 payload 010405` once per second, 61 times. The
Pocket returned same-sequence `04/50 payload 0001040100050101` 61 times with a
median 9.991 ms response. Because it begins before the first action and
continues after the last, it is a session/keepalive candidate rather than the
continuous stick value; its semantics remain unknown.

【参考实现结论】The `o-gs/dji-firmware-tools` dissector names `04/01` “Gimbal
Control” and describes three uint16 inputs in range 363–1685. `lib-osmo-ble`
labels it raw PWM with center 1024. Those references agree with the command ID,
center and changing axes, but neither documents this Pocket 3 ten-byte Wi-Fi
layout or its trailing constants.

【已否定假设】DJI Mimo does not use BLE `04/0C` for the four recorded joystick
actions. It uses Wi-Fi `04/01`. This explains why the bounded BLE `04/0C` test
produced no motion, but does not prove whether `04/0C` could work over Wi-Fi.

That safety decision was subsequently replaced by the explicit constrained
policy below after the 20-byte envelope was reconstructed and all 555 target
datagrams round-tripped byte-for-byte. `04/50` remains prohibited.

## Structured Wi-Fi center validation: 2026-07-20

Private evidence session: `wifi-gimbal-test-20260720-010538`. The raw capture
and device/network identifiers are not committed. The source manifest and all
13 listed evidence files passed SHA-256 verification after the directory was
pulled back from ROCK 4D.

【实机事实】The wrapper first restored the previously interrupted, already
validated RTMP session. It then discovered the current Pocket publisher rather
than using the older PCAP address, bound the Mimo-observed local UDP port 54232,
completed the structured transport handshake, and sent exactly eight `04/01`
center commands. No non-center command or `04/50` was sent.

【实机事实】The restricted LINUX_SLL2 packet capture contains nine outbound
control-session datagrams: one 48-byte transport handshake followed by eight
43-byte enveloped DUML center frames. They match `sent-datagrams.jsonl` in exact
byte order. The transport sequences advance by eight, the DUML sequences and
message counters advance by one, every payload is
`00040000000400804200`, and the final controller state is `centered`.

【实机事实】The owner observed no visible gimbal movement, no Pocket error, no
RTMP interruption and no need for restart or manual recovery. BlueZ remained
connected. BTSnoop contains zero app-to-device gimbal writes, confirming that
FFF5 was not used; only the permitted notification subscription lifecycle was
performed.

【统计观察】The session received 25 valid DUML frames with zero CRC or
reassembly failures. Six locally recorded `04/05` candidates kept yaw offset
16 fixed at 17048, roll offset 22 fixed at 8, and pitch offset 20 within 94–95.
The one-unit pitch variation is consistent with the earlier static baseline;
there is no telemetry evidence of center-induced motion.

【捕获推断】The structured zero/center form is safe enough to unblock one
separate, bounded yaw-positive test at offset 16. This does not yet establish
that Pocket accepts non-center control, that the UI sign is correct, or that
the center sequence stops an actual motion. Those claims require visual and
telemetry evidence from the next one-direction pulse.

Current limits remain: two non-center frames at 10 Hz, offset exactly 16,
250 ms watchdog, 500 ms absolute hard limit, then five centers at 20 Hz,
300 ms wait, one final center and a two-second observation interval. Any
unexpected direction, continued motion, Pocket error, BLE/RTMP loss or manual
recovery requirement stops active testing.
