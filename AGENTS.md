# Execution location

The Windows checkout is the sole source of truth for editing and Git.
Do not execute pytest, tests, builds, media decoding, or analysis jobs on Windows.
The owner observed a correlation between local pytest and desktop-client crashes.
Deploy and execute these jobs on ROCK 4D through `scripts/remote.sh`; archive the
results locally. Read-only file inspection and Git operations stay local.
Use `./scripts/remote.sh remote-test` for the project test suite.

Preserve the fixed vendor kernel, boot chain, DSI/touch configuration and wired
management route. Normal-mode Wi-Fi uses only its owned temporary profile and
restores the previous WLAN on exit.
