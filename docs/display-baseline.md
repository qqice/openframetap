# Display and touch baseline

The 5-inch 720p MIPI-DSI display and five-point touch panel were already ported and made operational by the user. OpenFrameTap reuses the existing DRM, panel, compositor, and input stack; panel bring-up is explicitly outside this project.

This milestone only records connector state, modes, DRM nodes, graphical sessions/processes, input identity, multi-touch slot count, and any visible rotation/mapping evidence. It does not replace device trees, drivers, panel timings, overlays, boot configuration, or run a disruptive full-screen test.

Primary evidence is archived locally under `artifacts/remote/display-20260718-100031/`.

## Verified facts

- Active connector: `card2-DSI-1`, `connected`, `enabled`, native mode `720x1280` (720p portrait timing). HDMI is disconnected.
- GNOME Shell is running in the local seat session (`Type=wayland`, active, non-remote). The separate SSH session reports TTY/empty display variables; this does not contradict the active local graphical session.
- GNOME `monitors.xml` records a `left` rotation. Combining native portrait timing with this compositor rotation indicates a landscape logical desktop; no configuration was changed to test that inference.
- DRM nodes: `/dev/dri/card0..2` and `/dev/dri/renderD128..130`. The active DSI connector belongs to `card2`.
- `fbset` reports mode `720x1280` and geometry `720 1280 720 1280 32` (32 bits per pixel).
- Touch: `Waveshare GT911` at `/dev/input/event2`, also linked as `platform-27300000.i2c-event`.
- Read-only input ioctls report `ABS_MT_SLOT` 0–4, proving five simultaneous slots, and calibrated MT X/Y ranges of 0–4096.
- Kernel logs report a 5-inch Waveshare panel controller, a `720x1280p66` DSI mode, GT911 config v67, calibrated axes, and seam filtering.

The native connector orientation and logical desktop orientation are distinguished above. No full-screen test, compositor stop, device-tree change, timing change, or input remapping was performed.
