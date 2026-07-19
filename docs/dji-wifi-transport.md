# DJI Wi-Fi transport envelope

This document records the bounded transport profile used by OpenFrameTap for
Pocket 3 UDP control. It is based on the immutable local capture
`mimo-gimbal-wifi.pcap` (SHA-256
`8e7c7eb62acd32429bd0d176e3da2b7d1f688854a3ee79c4017eb631139a181c`).
It does not generalize session-specific values into universal DJI constants.

## Evidence classes

- **Local hardware fact:** Mimo sent 494 `02 -> 04`, flags `00`, `04/01`
  DUML frames and 61 `04/50` requests inside the same 20-byte envelope to
  Pocket 3 UDP port 9004.
- **Statistical observation:** all 555 target datagrams round-trip byte for
  byte through the OpenFrameTap parser and encoder. All 555 have a valid
  basic-header XOR checksum and `outer length == 20 + DUML length`.
- **Reference implementation conclusion:** xaionaro-go/djictl independently
  identifies the 12-bit length, 16-byte metadata, WhType byte and UDP port
  9004. Its checked-in metadata is capture-specific and is not reused as an
  OpenFrameTap constant.
- **Unverified hypothesis:** byte 18 (`delivery_flags`) is zero in the dominant
  form and `0x60` in a minority of otherwise valid messages. Its trigger and
  meaning are not yet established.

## Twenty-byte standard envelope

| Offset | Width | OpenFrameTap name | Captured behavior |
|---:|---:|---|---|
| 0 | 2 | length and format | little-endian; low 12 bits are total UDP payload length, high nibble is `8` |
| 2 | 2 | session ID | `0x7055` in this Mimo session; another public capture uses a different value |
| 4 | 2 | transport sequence | little-endian, increments by 8 for each upstream WhType 05 packet |
| 6 | 1 | WhType/channel | `05` for operator-to-device command packets |
| 7 | 1 | basic-header checksum | XOR of bytes 0 through 7 is zero |
| 8 | 2 | peer sequence | latest processed WhType 01 range-3 cumulative receipt value, sometimes lagging it by a small scheduling window |
| 10 | 2 | sequence echo | equals the transport sequence at offset 4 in all target samples |
| 12 | 4 | reserved | zero in all target samples |
| 16 | 2 | message sequence | `0x0100 | counter`; low byte increments and wraps |
| 18 | 1 | delivery flags | `00` dominant; `60` minority; generation is restricted to `00` |
| 19 | 1 | reserved | zero in all target samples |
| 20 | variable | payload | complete DUML frame for WhType 05 commands |

The parser preserves every field, including unknown values, and the encoder
recomputes the length and XOR checksum. Parsed packets may contain either
observed value of byte 18 and still re-encode exactly. New control packets are
fail-closed to the dominant `00` form; callers cannot request `0x60` or alter
reserved bytes.

## Sequence behavior

The full upstream WhType 05 stream uses transport sequence steps of exactly
eight. The local capture does not cross the uint16 boundary, so uint16 wrap is
implemented and covered by synthetic tests rather than labeled a hardware
fact. The message-sequence low byte wraps repeatedly from `ff` to `00`, while
the high byte remains `01`.

The session ID and initial sequence seed are session state, not protocol magic.
They must be established by a structured, capture-derived handshake profile
and recorded in each private test artifact. OpenFrameTap does not copy an
opaque 20-byte header or accept a raw datagram from a caller.

The DUML transaction field remains represented as a big-endian value by the
general DUML codec. Mimo's Wi-Fi sender, however, advances an underlying
little-endian uint16 counter before placing its two bytes into that field. The
captured consecutive bytes are therefore `42 b7`, `43 b7`, `44 b7`, not
`42 b7`, `42 b8`, `42 b9`. The Wi-Fi transport performs this counter-to-wire
mapping explicitly; BLE and offline DUML semantics are unchanged.
Across all 2,570 upstream DUML frames in the immutable Mimo PCAP, interpreting
bytes 6–7 as the underlying little-endian counter yields `+1` for 2,230 of
2,569 adjacent transitions; the remaining gaps are explained by unobserved or
interleaved traffic. The first twelve captured upstream frames are all `+1`.

An early live prototype incremented the codec value directly, producing wire
bytes `00 00`, `00 01`, `00 02`; that mismatch has been corrected. A separate
transport-window bug then produced the same user-visible "first gesture only"
failure and is documented below. Fresh session IDs and an experimental
WhType `04` generator did not address that bug. The WhType `04` experiment
generated excessive traffic and has been removed.

## WhType 01 cumulative receipt state

Pocket-to-operator WhType `01` datagrams begin with a 34-byte structure:

| Offset | Width | Observed field |
|---:|---:|---|
| 0 | 8 | basic header (`wh_type == 01`) |
| 8 | 8 | provisional sequence range 1: start, end, four reserved bytes |
| 16 | 8 | provisional sequence range 2: start, end, four reserved bytes |
| 24 | 8 | provisional sequence range 3: start, end, four reserved bytes |
| 32 | 2 | trailing payload length |
| 34 | variable | optional DUML payload |

Range 3 is the cumulative receipt source for operator WhType `05` packets.
This conclusion is supported by both independent captures:

- **Mimo capture:** 6,895 valid WhType `01` status packets contain 1,486
  range-3 transitions and no unequal range-3 start/end pair. Of 2,581
  comparable outbound WhType `05` packets, 1,823 use the latest value exactly;
  all remaining packets lag it by only 1–10 eight-byte sequence steps. Zero
  outbound packets use the most recently observed WhType `03` basic-header
  sequence.
- **OpenFrameTap failure capture:** Pocket advanced range 3 from `42744` to
  `42952`, while the old implementation advanced its outbound `peer_sequence`
  only from sparse WhType `03` responses and stopped at `42808`. The stale gap
  reached 18–20 steps; later control keepalives were ignored and the UI entered
  FAULT even though Pocket had already reported receipt of the control burst.

The parser now validates the embedded payload length and preserves all three
ranges. The sender advances only from an equal range-3 start/end pair; an
unequal pair is logged and ignored rather than assigning an unverified range
semantic. WhType `03` remains recorded as individual response provenance and
is no longer used as cumulative acknowledgement state.

### Operator WhType 04 flow acknowledgement

【实机事实】After the range-3 fix, the owner could repeatedly control the gimbal.
The 122.927-second evidence session kept the outbound receipt gap at one step,
but Pocket's WhType `01` range 2 grew from `56360` to `57160`: exactly 100
eight-byte response steps. Pocket then stopped returning `04/50`; 49 of 51
keepalives received a response and the old timeout correctly centered control.
This separates the fixed operator-command receipt bug from a second long-lived
response-window exhaustion.

【统计观察】Mimo emitted 2,944 WhType `04` status packets with a median interval
of 22.825 ms. In the dominant structure it collapses WhType `01` ranges 1 and
2 to their processed end values, then reports range 3 from Pocket's cumulative
operator receipt value through the latest locally sent WhType `05` sequence.
For example, an incoming range-2 value `33448..33480`, range-3 receipt `33496`,
and latest send `33536` produces block 2 `33480..33480` and block 3
`33496..33536`; the reconstructed 34 bytes match the Mimo packet exactly.

【捕获推断】Range 2 is Pocket's pending response window, and failing to collapse
it in an operator WhType `04` packet exhausts a 100-packet queue. OpenFrameTap
now generates only this exact structured status form at no more than 40 Hz.
It is emitted by the same serialized transport writer as commands; no UI or
input callback can send it directly. This long-session correction remains to
be confirmed by the next live hardware run.

## Command boundary

The only outbound DUML command families allowed by the current Wi-Fi control
profile are `04/01` constructed from `Pocket3StickCommand` and the exact
capture-verified `04/50` session keepalive payload `01 04 05`. In particular:

- caller-supplied or altered `04/50` payloads are rejected;
- arbitrary hexadecimal datagrams and caller-supplied ten-byte payloads have
  no public send API;
- WhType other than `05`, nonzero delivery flags, changed reserved fields,
  changed endpoints, roll values and out-of-range stick values are rejected;
- UDP send success is evidence only that the local kernel accepted a datagram,
  not that Pocket accepted or acted on it.

Reproduce the envelope audit with:

```bash
python -m openframetap analyze dji-wifi-envelope \
  artifacts/private/mimo-gimbal-wifi.pcap \
  --output artifacts/local/mimo-dji-wifi-envelope-analysis.json
```
