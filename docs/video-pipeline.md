# RK3576 video pipeline validation

This phase was executed from the Windows Git repository and deployed to the ROCK 4D runtime. Private video, screenshots, stream credentials, and full logs remain under ignored `artifacts/private/`; only the implementation and this redacted result summary are committed.

## Validated platform

- 【实机事实】 The target remained on Armbian 26.5.1 noble and `6.1.115-vendor-rk35xx`; `/dev/mpp_service`, `/dev/rga`, and `/dev/dri/renderD128` were present.
- 【实机事实】 GStreamer 1.24.2 exposed Rockchip `mppvideodec` plugin 1.14.4, `waylandsink`, `rtmpsrc`, `h264parse`, and software `avdec_h264`.
- 【实机事实】 The active local session was GNOME Wayland session 3 for UID 1000 on `wayland-0`. `card2-DSI-1` remained 720x1280 physical, transform 1, and 1280x720 logical.
- 【实机事实】 No DSI, touch, GNOME monitor, kernel, boot, driver, Wi-Fi, or route configuration was changed.

Evidence: `video-doctor-20260718T210413Z`, `display-doctor-20260718T210202Z`.

## Decoder results

The immutable 8-second sample had SHA-256 `d7c75567108846511450366c5b7655b53e3add0895b0cf3fb6e83ac6ccaab346` and contained 1280x720 H.264 High video at approximately 29.97 fps.

| Framework | Decoder | Result | Frames / wall time | Average CPU | Hardware conclusion |
| --- | --- | --- | --- | --- | --- |
| GStreamer | `mppvideodec` | passed | 228 / 8.217 s, real-time sink | 9.10% | 【实机事实】 confirmed |
| GStreamer | `avdec_h264` | passed | 228 / 8.609 s, real-time sink | 55.46% | 【实机事实】 software baseline |
| FFmpeg | `h264` | passed | 228 / 1.229 s | 328.59% | 【实机事实】 software baseline |
| FFmpeg | `h264_rkmpp` | passed | 228 / 1.019 s | 105.59% | 【实机事实】 confirmed hardware decode |
| FFmpeg | `h264_v4l2m2m` | failed | no valid V4L2 device | 93.45% during failure | 【实机事实】 rejected on this image |
| GStreamer | `v4l2h264dec` | unavailable | plugin absent | n/a | 【实机事实】 excluded |
| GStreamer | `v4l2slh264dec` | unavailable | plugin absent | n/a | 【实机事实】 excluded |

【实机事实】 The selected GStreamer path explicitly instantiated `GstMppVideoDec`, negotiated H.264 through `h264parse`, emitted NV12/raw output, accessed the MPP device, decoded all 228 frames without errors, contained no `avdec_h264`/`decodebin`, and used about one sixth of the real-time GStreamer software CPU. This combination is the hardware-decode proof; successful display alone is not treated as proof.

Evidence: `video-benchmark-20260718T211352Z`.

## Display and live pipeline

The selected path is direct local RTMP pull:

```text
rtmpsrc
→ flvdemux
  ├─ audio → bounded leaky queue → fakesink (no audio device opened)
  └─ video → queue max-size-buffers=3 leaky=downstream
           → h264parse config-interval=-1
           → video/x-h264,stream-format=byte-stream,alignment=au
           → mppvideodec
           → fpsdisplaysink
           → waylandsink sync=false
```

- 【捕获推断】 Converting FLV AVCC input to byte-stream access units is required by this vendor MPP element; the unconverted attempt failed while the explicit parser/caps path decoded correctly.
- 【实机事实】 The demux audio pad is linked to a bounded `fakesink` branch because the publisher can expose AAC before video. No ALSA, PulseAudio, or PipeWire output is opened.
- 【实机事实】 The application connects to the existing GNOME Wayland session. It never creates a compositor or changes `monitors.xml`.
- 【实机事实】 `waylandsink` must receive its fullscreen property after the first frame creates a Wayland surface. The player temporarily hides an already-active GNOME Overview, applies fullscreen, and restores the prior Overview state on exit.
- 【实机事实】 The final 1280x720 screenshots filled the logical DSI output with correct landscape orientation and aspect, without top bar, Dock, Overview, application scaling controls, or forced rotation.
- 【统计观察】 No `videoconvert`, `videoscale`, or RGA stage was needed. The source and logical output are both 1280x720; the screenshot supports one-to-one presentation, although compositor-internal implementation details are not directly measured.

The RTSP reader elements were present, but a separate RTSP preview was not selected because direct RTMP already avoided a relay/depay stage and passed the stability target. HLS is rejected by code as a low-latency default. KMS was skipped because Wayland succeeded and taking DRM master from GNOME would add recovery risk without evidence of a material benefit.

## Profile comparison

| Profile | Queue / sink | 60-second rendered / dropped | Average CPU | Internal tracer average / maximum |
| --- | --- | --- | --- | --- |
| stable | 8 buffers, non-leaky, sync=true | 1690 / 9 | 15.66% | 80.16 / 208.71 ms |
| low-latency | 3 buffers, downstream-leaky, sync=false | 1719 / 51 | 15.26% | 2.35 / 72.01 ms |
| aggressive-low-latency | 1 buffer, downstream-leaky, sync=false | 1646 / 63 | 15.27% | 3.21 / 1073.01 ms |

【统计观察】 `low-latency` had the best balance: its bounded queue prevents stale-frame accumulation, it rendered more frames than the aggressive profile, and its internal tracer distribution was substantially lower than the synchronized stable profile. It is therefore the default.

## Ten-minute result

- 【实机事实】 Actual run time: 600.084 seconds; 17,311 rendered, 571 deliberately dropped old frames, zero decode errors, and zero late-frame warnings.
- 【统计观察】 Average/peak process CPU: 15.53% / 57.60%; average/peak RSS: 20.43 / 20.96 MB; start/end/peak temperature: 44.384 / 45.307 / 46.230 °C; average network receive rate: about 7.00 Mbit/s.
- 【实机事实】 MediaMTX remained running. Wayland disconnects, decoder resets, pipeline restarts, client reconnects, and BLE dependencies were all zero. The media pull does not require a simultaneous BLE connection.
- 【统计观察】 The internal GStreamer tracer had 37,994 samples, 2.471 ms average, and 178.982 ms maximum. The first 2,000 samples averaged 2.546 ms and the last 2,000 averaged 2.675 ms, a 0.129 ms difference; there was no evidence of steadily accumulating pipeline delay.
- 【实机事实】 Timeout cleanup removed the owned preview PID and left the process registry empty. A prior SSH-reset test exposed an orphan-risk and private-URL registry leak; parent-death signaling and argv redaction were added and revalidated.
- 【待验证假设】 The full camera-to-glass delay remains unknown. GStreamer tracer values begin inside the receiver and exclude camera exposure, Pocket encoding, and upstream buffering.

Evidence: `live-preview-20260718T215731Z`; final offline/true-fullscreen evidence: `preview-file-20260718T223437Z` and `live-preview-20260718T223954Z`.

## User-space installation and security note

- 【实机事实】 After an apt simulation reported 0 upgraded, 87 newly installed, 0 removed, and 3 held back, the phase installed only `ffmpeg` `7:6.1.1-3ubuntu5+git240504.09cd2a2~noble`, `gstreamer1.0-gl` `1.24.2-1+rkrga`, `gstreamer1.0-libav` `1.24.1-1build1`, and `gstreamer1.0-plugins-bad` `1.24.2-1ubuntu4` with `--no-install-recommends`. No reboot or service restart was required.
- 【实机事实】 An early process-status implementation exposed the private stream path once in a local task log. Registry and status argv are now redacted and the child receives only a private pipeline-file path.
- 【捕获推断】 The old stream key should be treated as compromised if those local logs leave the trusted workstation. Rotation requires a separately controlled fixed-proposal update and was not mixed into this media-validation phase.

## Commands

```bash
./scripts/remote.sh video-doctor
./scripts/remote.sh display-doctor
./scripts/remote.sh video-benchmark
./scripts/remote.sh preview-file
./scripts/remote.sh live-preview 120
OPENFRAMETAP_PREVIEW_PROFILE=stable ./scripts/remote.sh live-preview 60
./scripts/remote.sh preview-status
./scripts/remote.sh preview-stop
./scripts/remote.sh media-status
./scripts/remote.sh media-stop-all
python -m openframetap tools latency-pattern --duration 60
```

`preview-stop` and `media-stop-all` operate only on project-owned PIDs; broad `pkill`/`killall` is not used.

## Remaining measurement

【待验证假设】 Glass-to-glass latency requires an external same-frame recording. Point Pocket 3 at the Windows latency pattern and record both the Windows display and ROCK 4D DSI display in the same 240 fps phone video for 10–15 seconds. No protocol or display-stack changes are required.
