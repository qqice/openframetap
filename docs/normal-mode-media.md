# Pocket 3 normal-mode media framing

This phase is deliberately offline. It does not connect to Pocket 3, change a
Wi-Fi profile, send a DJI command, or infer the future hotspot association
procedure. The immutable input is the previously captured Mimo Wi-Fi session;
the only remote operation is playback of the recovered elementary stream.

## Product mode boundary

OpenFrameTap now treats these as separate future session profiles:

| Capability | Live mode | Normal mode (planned) |
| --- | --- | --- |
| Network role | Pocket joins an external WLAN | Pocket exposes its own WLAN; ROCK 4D joins it |
| Media source | Pocket pushes RTMP to OpenFrameTap | OpenFrameTap receives DJI UDP media |
| Video decoder/display | RK3576 MPP and the existing DSI application | Reuse the same MPP and DSI path |
| Gimbal input/control | Shared controller | Shared controller |
| Camera photo/record | May be constrained by live mode | Planned, not implemented in this phase |

Network association, camera control, and a live UDP receiver remain outside
this offline extraction phase.

## Capture-confirmed packet layout

The dominant downstream flow is `WhType 02`. Its first 20 bytes contain the
DJI Wi-Fi basic header and a media fragment header. The capture supports this
fragment interpretation:

```text
byte 16      frame identifier (uint8, wraps)
byte 17 bit7 fragment index bit 0
byte 17 0:6  fragment count
byte 18 0:4  fragment index divided by two
byte 18 5:7  preserved frame flags
byte 19      preserved reserved byte
byte 20...   fragment bytes
```

Therefore `fragment_index = 2 * (byte18 & 0x1f) + (byte17 >> 7)`.
The low three bits of the basic-header transport sequence were `0`, `2`, `4`,
or `6`; the nonzero generations were byte-identical retransmissions in this
capture. OpenFrameTap uses this only to prefer the earliest copy and does not
claim a general protocol semantic yet.

Fragment zero of a genuine access unit begins with:

```text
00 00 01 ff                  observed media magic
uint32_le                    elementary access-unit byte length
4 bytes                      preserved unknown metadata
uint32_le                    capture-inferred millisecond timestamp
H.264 Annex-B bytes...
```

The timestamp candidate advances by about 33/34 per picture and follows PCAP
arrival time with sub-millisecond median residual in this evidence. It remains
labelled as capture-inferred rather than protocol-confirmed.

## Fail-closed reconstruction

The extractor groups by frame identifier, fragment count, capture time, and
fragment index. It merges byte-identical delayed retries, survives identifier
wrap, and accepts a unit only when every fragment is present and the final byte
length exactly matches the declared length. Missing, conflicting, or
length-mismatched units are recorded but excluded from `video.h264`.

Commands:

```bash
python -m openframetap analyze dji-wifi-media \
  artifacts/private/mimo-gimbal-wifi.pcap \
  --output-dir artifacts/private/normal-mode-pcap-analysis-<timestamp>

./scripts/remote.sh analyze-wifi-media \
  artifacts/private/mimo-gimbal-wifi.pcap

./scripts/remote.sh preview-wifi-capture \
  artifacts/private/mimo-gimbal-wifi.pcap 30
```

The preview wrapper transfers only the recovered H.264 stream to the ROCK 4D.
It uses `filesrc -> h264parse -> mppvideodec -> waylandsink`, preserving the
existing hardware decoder and DSI display path without Python frame copies.

## Current real-capture result

- 【实机事实】The immutable PCAP contains 33,435 selected WhType 02 datagrams.
- 【捕获推断】After retransmission de-duplication there are 1,934 access-unit
  starts; 1,933 reconstruct with exact declared length.
- 【捕获推断】The exact units contain 1,888 picture units plus 45 SPS/PPS
  configuration units. One length-mismatched unit is excluded.
- 【实机事实】Local FFmpeg identifies the result as H.264 High Profile,
  1280x720, `yuv420p`, and decodes 1,884 pictures from the imperfect capture.
- 【实机事实】ROCK 4D played the recovered stream for 30 seconds through the
  explicit `mppvideodec` element and the existing Wayland/DSI path: 896 frames
  rendered, zero dropped, zero decode errors, 29.93 fps average, and no decoder
  reset or pipeline restart. The preview registry was empty after cleanup.
- 【实机事实】The remote playback evidence and screenshot were pulled to
  `artifacts/private/normal-mode-preview-20260719T235251Z`; its redacted summary
  is under `artifacts/sanitized/normal-mode-preview-20260719T235251Z`.
- 【待验证假设】A future live normal-mode receiver can feed reconstructed
  access units directly into an app-owned GStreamer `appsrc` while reusing MPP
  decode and the existing UI surface.
