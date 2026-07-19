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
| 8 | 2 | peer sequence | latest/near-latest peer transport sequence; precise acknowledgement semantics remain provisional |
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

## Command boundary

The only outbound DUML command allowed by the current Wi-Fi control profile is
`04/01` constructed from `Pocket3StickCommand`. In particular:

- `04/50` can be parsed offline but is rejected by the send policy;
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
