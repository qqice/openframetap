# Pocket 3 glass-to-glass latency results

This document is the sanitized record of the first three-profile physical capture. Raw phone video, screen images, exact transforms, and per-phone-frame records remain under ignored private artifacts.

## Measurement boundary

- 【实机事实】 The streamed device was DJI Osmo Pocket 3. DJI Pocket 4P was used only as the external 240 fps recorder.
- 【实机事实】 The recorder was set to 4K, 240 fps, 1/240 s locked exposure, ISO 100–6400, 3700 K, tint -25, HDR off, and no digital zoom. It was handheld with possible slight movement; the Pocket 3, Windows screen, and ROCK DSI remained fixed relative to each other.
- 【实机事实】 All files are 3840x2160 HEVC 10-bit. The 240 fps samples are stored as an approximately 29.97 fps slow-motion timeline; analysis rescales timestamps by `29.97003 / 240` and obtains about 31 seconds of physical capture per file.
- 【实机事实】 Windows display 0 was 3072x1440 at a reported 120 Hz, with a 2560x1440 pattern viewport. Application submissions were 93.54–94.22 Hz because missed Tk slots were skipped rather than caught up.
- 【实机事实】 Broad phone-frame ROIs were SOURCE `1200,80,2640,1600` and DSI `180,1080,950,700`. Each frame then used its detected outer green quadrilateral; no fixed perspective offset or latency offset was searched.
- 【捕获推断】 Pattern timestamps are Windows application-submission monotonic times, not measured scanout/present times. Roughly one display-refresh uncertainty remains.

## Method status

【实机事实】 Every input video had identical before/after SHA-256, every final analysis manifest verified, and all three accepted samples span essentially the full approximately 31-second capture.

【统计观察】 None reached the predeclared 90% raw-frame Gray decode requirement:

| Profile | Valid | Invalid | Mixed refresh | Decode success |
|---|---:|---:|---:|---:|
| low-latency | 2417 | 4596 | 357 | 32.80% |
| stable | 1416 | 5695 | 336 | 19.01% |
| aggressive | 819 | 6189 | 447 | 10.99% |

【捕获推断】 Most invalid records came from purple/cyan rolling-shutter bands crossing nominally black or white cells. The decoder was deliberately fail-closed. It normalizes each display against its own colour references, rejects ambiguous bits, and never searches for a global correction that makes latency look plausible.

【统计观察】 Confidence sensitivity supports the center of the low-latency and stable distributions. From minimum bit confidence 0.10 to 0.18, low-latency median moved from 185.04 to 184.04 ms, and stable from 473.77 to 472.80 ms. Aggressive moved from 187.18 to 181.35 ms while losing most samples, so its exact center is less robust.

These results are physical observations but do **not** pass the original method-valid gate. They are reported as exploratory distributions and cannot close the acceptance item requiring greater than 90% decoding success.

## Glass-to-glass distributions

Raw statistics include every accepted phone sample. Steady-state values uniformly exclude the first 1.0 second while leaving the raw evidence unchanged.

| Profile | Median ± MAD | P90 | P95 | P99 | Min / max | Steady median / P95 |
|---|---:|---:|---:|---:|---:|---:|
| low-latency | 185.04 ± 9.84 ms | 211.53 | 227.84 | 259.13 | 126.23 / 290.86 | 184.97 / 225.90 |
| stable | 473.77 ± 8.13 ms | 491.99 | 495.37 | 504.55 | 436.85 / 519.94 | 473.91 / 496.04 |
| aggressive | 187.18 ± 10.31 ms | 207.41 | 216.34 | 973.47 | 154.33 / 1001.05 | 186.51 / 211.64 |

- 【统计观察】 Stable added 288.72 ms to the low-latency median. Its much smaller receiver-drop count did not offset that cost.
- 【统计观察】 Aggressive did not improve the median: its steady-state median was 1.54 ms higher than low-latency, although its steady P95 was 14.26 ms lower.
- 【统计观察】 All aggressive samples above 500 ms occurred in the first 0.5 seconds. After 0.5 seconds its 788 accepted samples had median 186.51 ms, P95 211.62 ms, P99 225.25 ms, and max 249.06 ms.
- 【捕获推断】 The aggressive near-one-second tail is a startup/old-frame drain event rather than sustained jitter. The raw P99 is retained and must not be hidden by the steady-state view.

## Repetition and transitions

| Profile | Independent DSI runs | Phone resample repetitions | Duplicate DSI transitions | Pattern-ID gaps |
|---|---:|---:|---:|---:|
| low-latency | 672 | 1745 | 5 | 3049 |
| stable | 461 | 955 | 5 | 3287 |
| aggressive | 269 | 550 | 3 | 3869 |

【捕获推断】 Phone resample repetitions are expected when a 240 fps recorder observes an approximately 29.97 fps stream and are not decoder drops. Pattern-ID gaps likewise include normal camera sampling and skipped Windows application submissions; they cannot be promoted to transport drop counts.

## Paired ROCK runtime evidence

| Profile | Rendered / reported dropped | CPU avg / peak | Peak temperature | Internal tracer avg / max | Late warnings |
|---|---:|---:|---:|---:|---:|
| low-latency | 1774 / 6 | 15.72% / 31.51% | 45.307 °C | 1.92 / 40.43 ms | 0 |
| stable | 1768 / 1 | 19.44% / 34.91% | 46.230 °C | 120.30 / 313.50 ms | 11 |
| aggressive | 1745 / 8 | 15.57% / 31.51% | 46.230 °C | 2.76 / 1021.14 ms | 0 |

- 【实机事实】 All paired runs reported zero decoder resets, Wayland disconnects, pipeline restarts, and client reconnects; owned-process cleanup completed.
- 【实机事实】 GStreamer tracer latency is receiver-internal source-to-sink processing only. It excludes Pocket exposure/encoding, RTMP transport before the source element, and physical display scanout.
- 【捕获推断】 The roughly 185 ms low-latency glass result being far larger than its roughly 1.9 ms internal mean shows that most end-to-end delay lies outside ordinary receiver element processing. The two statistics use different distributions and must not be subtracted as if they were synchronized samples.

## Default profile decision

【统计观察】 Retain the existing `low-latency` default (`queue=3`, downstream-leaky, `sync=false`). Stable is clearly too latent. Aggressive has no meaningful median advantage, more reported drops than low-latency, and a startup tail in both tracer and glass evidence.

【待验证假设】 This is conservative retention of the already deployed default, not a final profile selection backed by a method-valid recording. A repeat capture that suppresses LCD colour banding must first exceed the 90% Gray decoding gate.

No DJI command, Wi-Fi configuration, RTMP payload, kernel, network, DSI, touch, or boot setting was changed during offline phone-video analysis.
