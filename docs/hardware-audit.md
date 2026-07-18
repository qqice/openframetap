# ROCK 4D hardware audit

Status: awaiting/undergoing live evidence collection. This document is updated only from artifacts copied back from the ROCK 4D.

## Fixed baseline

- Board: Radxa ROCK 4D, RK3576, 16 GB (board/memory evidence recorded separately by the live audit).
- Runtime: Armbian on the vendor `6.1.115-vendor-rk35xx` kernel family.
- Wireless module: Quectel FCU760K / AIC8800D80 combination module.
- Display: already-ported Waveshare 5-inch 720p MIPI-DSI touch display.

No audit tool changes the kernel, firmware, module bindings, NetworkManager backend, routes, boot chain, DTB, or display configuration.

## Evidence classification

Live results below will label statements as either verified facts, log-derived conclusions, or unverified hypotheses. Missing commands are recorded as tooling gaps rather than treated as device failures.
