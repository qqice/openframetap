# Execution location — owner-requested handoff, 2026-09-06

After `runtime/board-canonical` is sealed, the canonical Git checkout and runtime
are `/home/qqice/Workspace/openframetap` on ROCK 4D. Edit, commit, test, decode and
analyze there. The Windows checkout is a frozen historical copy, not a deploy
source. Never overwrite newer board edits from Windows. The old
`/home/qqice/openframetap-runtime` path is a compatibility symlink, not another
source tree. See `docs/board-handoff.md` before continuing development.

Native entry points: `./scripts/board-app.sh start`, `stop`, `status`, `test`.
Desktop launch defaults to normal mode with live joystick control and no time
limit. `--duration 0` disables continuous packet/video capture and rotates logs;
use bounded diagnostic sessions when raw captures are needed.

During the one-time migration before that seal, Windows remains the editing/Git
source and all execution stays on ROCK 4D through `scripts/remote.sh`.
Do not run tests, builds, media decoding or analysis on Windows.

Preserve the fixed vendor kernel, boot chain, DSI/touch configuration and wired
management route. Normal-mode Wi-Fi uses only its owned temporary profile and
restores the previous WLAN on exit.
