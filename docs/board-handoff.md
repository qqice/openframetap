# ROCK 4D development handoff — 2026-09-06

## Authoritative workspace

After `runtime/board-canonical` is present, use
`/home/qqice/Workspace/openframetap` for **all editing, Git and execution**.
Windows `C:\Workspace\openframetap` is a frozen historical copy. The old
`/home/qqice/openframetap-runtime` becomes a symlink to this checkout; its original
contents remain at `/home/qqice/openframetap-runtime-pre-migration` as rollback.
Do not resume Windows deployments: `deploy.sh` refuses a sealed board workspace.

The migration includes Git history, tracked/untracked project files, references,
fixtures, scripts, configuration and local artifacts. The newer remote evidence
is merged in with checksums; differing Windows copies are retained under
`artifacts/migration/windows-conflicts`. Windows runtime files are archived as
`artifacts/migration/windows-runtime`; Windows virtualenv and disposable Python
caches are not used on Linux. Existing Linux dependencies are copied/relocated
offline into `.venv`; no kernel/system package is installed. Final Git HEAD,
checksums and migration details are in `artifacts/migration/`.

## Native operation

```bash
cd /home/qqice/Workspace/openframetap
./scripts/board-app.sh start   # normal mode, touch control, unlimited duration
./scripts/board-app.sh status
./scripts/board-app.sh stop
./scripts/board-app.sh test
```

The per-user OpenFrameTap desktop/menu shortcut invokes `board-app.sh start`.
It is not a system service or boot autostart. Quit through the GUI or the stop
command; repeated desktop launches are serialized and do not start a second app.

`app --duration 0` means no session deadline, not an arbitrarily large timeout.
For unlimited normal monitoring, continuous btmon/tcpdump/H.264-file capture is
disabled, JSONL logs rotate at 8 MiB with two backups, and metrics retain a bounded
one-hour sample window. Bounded diagnostic runs retain existing raw evidence.
The independent joystick watchdog, release-to-stop and failure shutdown remain.

## Corrections preceding migration

### Livestream stutter while moving

【实机事实】User sessions showed frozen/zero-frame-rate intervals in livestream
mode while normal mode remained usable. The RTMP pipeline contained a three-buffer
**leaky compressed-H.264 queue before decoding**, unlike normal mode.

【捕获推断】Discarding those buffers can remove reference pictures when changing
scenes produce bursts. The corrected live graph preserves the bounded compressed
queue with backpressure and drops only decoded display pictures:

`rtmpsrc → flvdemux → bounded non-leaky queue → h264parse → mppvideodec → bounded leaky display queue → gtkwaylandsink`

[GStreamer's queue contract](https://gstreamer.freedesktop.org/documentation/coreelements/queue.html)
defines backpressure versus leaking. This change keeps MPP and avoids Python
pixel conversion; it does not claim to eliminate camera/network stalls generally.

【实机事实】`control-session-board-ready-20260906-0543` completed normal →
livestream → normal in unlimited mode, exiting only through the explicit test
stop. Measured render rates were 29.81 / 28.59 / 27.23 fps. Livestream included
two balanced one-second low-speed yaw inputs; longest sampled no-new-frame gap
was 0.202 s. This is a bounded physical improvement check, not an all-day soak.

### First connection after exit

【实机事实】The previously failed first launches had connected BLE and received
`07/45 = 00 01` (already paired); the timeout was **07/07 SSID read**, not pairing.
Two failed/successful pairs are retained in sessions ending `212128Z/212154Z` and
`200628Z/200655Z`.

【参考实现结论】OpenPocketCine starts application presence before reading Wi-Fi
credentials. Our old code started heartbeat only after credentials, and held its
single writer lock through the response wait. It now runs the existing 00/2B
presence heartbeat while reading; the writer lock covers writes, not waiting.
Only SSID reading has a single bounded timeout retry; no pairing or state-changing
command is retried by this path. Errors name the failed request.

Unsubscribe errors/timeouts now still run the owned client's disconnect in
`finally`, and disconnect completion checks the actual client state. Reconnecting
resets intentional-disconnect bookkeeping. No BlueZ remove/bond/reset/restart is
performed.

【实机事实】Both normal connections in the physical cycle received credentials
on the first request, **zero SSID retries**, with existing paired status. This
addresses the observed first-launch symptom without asking the owner to relaunch.
Future distinct errors must be diagnosed from named events, not assumed pairing.

## State and boundaries for the next board-side Codex

- Working Pocket 3: BLE pairing, normal SoftAP/UDP AVC, external-router RTMP,
  MPP/DSI GUI, mode switch, bitrate/status, joystick, recenter/flip, video tap focus.
- New focus acceptance is physical near/far/near image reversal, not merely ACK.
  See `docs/touch-camera-actions.md`. Recording-capability button/auto-query removed.
- RTMP presets: H.264 480/720/1080. Normal AVC 720 only; do not invent HEVC/MJPEG
  monitor enumeration from recording-format tables or another Pocket model.
- Preserve vendor 6.1.115 kernel, boot chain, DSI/touch, wired management route,
  and project-owned temporary WLAN rollback. No global service/network resets.
- No automatic shooting, calibration, raw PWM, new guessed private commands,
  pairing deletion or irreversible actions. Existing reversible user controls
  remain authorized; use bounded hardware tests and stop on unexpected responses.
- Private evidence/credentials remain ignored by Git. Do not paste stream keys,
  Wi-Fi passwords or provisioning payloads into public reports.
- Tests before migration: **331 passed, 1 skipped**, all on ROCK 4D; optional NumPy
  glass-latency test skipped. Finalizer repeats the suite in the new workspace.
- Migration does not install or sign into Codex; open this directory from the
  owner's board-side Codex environment and follow this AGENTS.md.
