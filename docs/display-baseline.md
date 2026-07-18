# Display and touch baseline

The 5-inch 720p MIPI-DSI display and five-point touch panel were already ported and made operational by the user. OpenFrameTap reuses the existing DRM, panel, compositor, and input stack; panel bring-up is explicitly outside this project.

This milestone only records connector state, modes, DRM nodes, graphical sessions/processes, input identity, multi-touch slot count, and any visible rotation/mapping evidence. It does not replace device trees, drivers, panel timings, overlays, boot configuration, or run a disruptive full-screen test.

Live baseline values are populated after `./scripts/remote.sh display-audit` and cross-checked against the archived raw output. An empty `DISPLAY`, `WAYLAND_DISPLAY`, or `XDG_SESSION_TYPE` in an SSH shell is not by itself evidence that the local screen is inactive.
