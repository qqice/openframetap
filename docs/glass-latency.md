# Glass-to-glass latency measurement

This workflow measures the complete visible delay between a Gray Code state on the Windows source display and the same state shown by the Pocket 3 stream on the ROCK 4D DSI. It is separate from the GStreamer source-to-sink tracer, which measures only a small part of the receiver pipeline.

## Method boundary

- 【实机事实】 The Windows pattern enumerates the selected monitor after enabling per-monitor-v2 DPI awareness. On the current primary monitor it reports 3072x1440 at a Windows mode rate of 120 Hz and centers an undistorted 2560x1440 16:9 pattern viewport.
- 【实机事实】 The pattern uses two complete 16-bit Gray Code banks. A rolling-refresh mixture is detected when the upper and lower banks decode to different IDs; a one-bit Gray difference is identified as an adjacent partial refresh.
- 【实机事实】 Every submitted state is written immediately to JSONL with `time.perf_counter_ns`, its scheduled display slot, Gray value, UTC time, display index, and mode refresh rate. Missed slots are skipped and counted; the tool never rapidly submits catch-up frames.
- 【实机事实】 Tk does not expose a compositor/display present timestamp. `actual_present_time_ns` is therefore always `null`, and the config explicitly labels `monotonic_ns` as application submission rather than scanout.
- 【捕获推断】 Mapping decoded IDs through actual JSONL submission timestamps is more reliable than multiplying an ID difference by an assumed 60 or 120 Hz. Source scanout uncertainty of roughly one display refresh remains.

## Windows pattern

```powershell
.\.venv\Scripts\python.exe -m openframetap tools latency-pattern `
  --duration 60 `
  --display 0 `
  --fullscreen `
  --refresh-hz auto `
  --gray-code `
  --bits 16 `
  --warmup 5
```

The command writes a new ignored private directory containing `pattern-timing.jsonl`, `pattern-config.json`, `runtime.log`, `screenshot.png`, and `checksums.sha256`. `Esc`, `q`, Ctrl+C, duration expiry, and window close all finalize the log and checksum manifest. It makes no persistent Windows display change. `--windowed`, `--invert`, a numeric refresh override, a different active display index, and a specific `--log` path are also supported.

## Physical recording

Run the source pattern and `./scripts/remote.sh live-preview 60` in separate terminals. Point Pocket 3 at the green-bordered Windows pattern. A phone in 240 fps mode must see both the original Windows pattern and the ROCK 4D DSI preview for 10–15 seconds. Keep both screens in focus, disable stabilization/automatic zoom if it crops either ROI, and avoid clipped white cells.

Store the unmodified recording under:

```text
C:\Workspace\openframetap\artifacts\private\latency-recordings\
```

The first recording is only for the current `low-latency` profile. Do not change queue or sink synchronization until it reaches at least 90% decoding success and 100 distinct DSI frame runs.

## Analysis

For one-time visual ROI selection:

```powershell
.\.venv\Scripts\python.exe -m openframetap analyze glass-latency `
  artifacts\private\latency-recordings\phone-default.mp4 `
  --pattern-log artifacts\private\latency-pattern-YYYYMMDDTHHMMSSZ\pattern-timing.jsonl `
  --interactive-roi `
  --phone-fps auto `
  --pipeline-profile low-latency
```

For reproducible non-interactive reruns, replace `--interactive-roi` with both `--source-roi x,y,w,h` and `--dsi-roi x,y,w,h`. Four-corner absolute perspective coordinates can be supplied with `--source-transform` and `--dsi-transform` in top-left, top-right, bottom-right, bottom-left order.

The analyzer:

1. hashes the original video and probes codec, geometry, frame rates, time base, per-frame timestamps, duration, and VFR behavior;
2. rectifies both ROIs to the canonical 16:9 layout;
3. derives a local black/white threshold from fixed reference cells;
4. decodes both Gray banks, rejects low contrast and ambiguous bits, and isolates mixed-refresh samples;
5. handles 16-bit wraparound without searching for a convenient global offset;
6. maps SOURCE and displayed IDs to the pattern submission log;
7. calculates per-phone-frame latency, run-lengths, percentiles, MAD, long tail, and transition statistics;
8. re-hashes the input video and fails if it changed;
9. writes private evidence plus a path-free sanitized summary and six independent plots.

The main latency statistics include all valid phone sampling instants. Consecutive phone frames that see the same DSI ID are separately represented by DSI run-length encoding and are not automatically called decoder drops. Pattern IDs skipped between DSI transitions include normal approximately 30 fps camera sampling and also cannot by themselves prove RTMP/decode loss.

## Validation status

- 【实机事实】 Synthetic pattern images, inversion, perspective distortion, low contrast, one-bit mixed refresh, VFR metadata, wraparound, timing-map constraints, input immutability, sanitization, and a compressed synthetic phone video are covered by offline tests.
- 【实机事实】 The synthetic video recovered its injected 100 ms delay with greater than 90% Gray decoding success.
- 【待验证假设】 No physical phone recording has been analyzed yet, so median, p95, p99, rolling-shutter rate, and the final stable-versus-low-latency comparison remain unknown.

Only after the first method-valid recording should `stable` receive its own independent phone recording. `aggressive-low-latency` remains optional because receiver testing already showed more active drops and a larger long tail.
