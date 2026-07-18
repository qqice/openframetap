# ROCK 4D hardware audit

Status: live audit completed on 2026-07-18. Primary evidence is archived locally under `artifacts/remote/doctor-20260718-095823/`; earlier iterations are retained rather than overwritten.

## Fixed baseline

- Board: Radxa ROCK 4D, RK3576, 16 GB (board/memory evidence recorded separately by the live audit).
- Runtime: Armbian on the vendor `6.1.115-vendor-rk35xx` kernel family.
- Wireless module: Quectel FCU760K / AIC8800D80 combination module.
- Display: already-ported Waveshare 5-inch 720p MIPI-DSI touch display.

No audit tool changes the kernel, firmware, module bindings, NetworkManager backend, routes, boot chain, DTB, or display configuration.

## Verified facts

- Host/account: `radxa-rock-4d`, user `qqice`; board model reports `ROCK 4D`.
- OS/kernel: `Armbian 26.5.1 noble`, `6.1.115-vendor-rk35xx`; the expected-kernel check is true.
- Wireless USB enumeration: `a69c:8d81 AICSemi AIC 8800D80` at USB `3-1.4`.
- Wi-Fi: `wlan0` is up, connected and managed by NetworkManager through `aic8800_fdrv`; DKMS reports `aic8800-usb/4.0+git20250410.b99ca8b6-5` installed for this exact kernel. Neither Wi-Fi nor Bluetooth is rfkill-blocked.
- Firmware loaded from `/lib/firmware/aic8800_fw/USB/aic8800D80/`: `fw_patch_table_8800d80_u02.bin`, `fw_adid_8800d80_u02.bin`, `fw_patch_8800d80_u02.bin`, `fw_patch_8800d80_u02_ext0.bin`, and `fmacfw_8800d80_u02.bin`.
- `iw list` advertises `managed`, `AP`, `P2P-client`, `P2P-GO`, `P2P-device`, and `monitor`. This is capability evidence only; no mode was configured.
- The default route uses `wlan0`, but the SSH client route uses `tailscale0`, so the audit did not operate over or disrupt the default Wi-Fi path.
- Bluetooth: controller `00:9C:17:5E:D2:C5`, `hci0`, powered with LE support; `bluetooth.service` is active and BlueZ is `5.72`.
- Media nodes include three DRM cards, three render nodes, `/dev/video-dec0`, `/dev/video-enc0`, `/dev/mpp_service`, and `/dev/rga`. GStreamer exposes Rockchip MPP video/JPEG decoders and H.264/H.265 plumbing; FFmpeg exposed no matching hardware decoder in the queried list.

## Log-derived conclusion

The boot log contains initial `aic_load_fw` probe failures during USB firmware/re-enumeration, followed by AIC firmware upload, `aic8800_fdrv`/`aic_btusb` registration, an operational `wlan0`, and an operational `hci0`. This makes the early messages historical/non-fatal for the current boot, not evidence of a present Wi-Fi failure.

## Still unverified

The module's commercial FCU760K label is supplied by the hardware baseline; USB itself exposes only `AICSemi AIC 8800D80`. Advertised AP/P2P/monitor capabilities have not been exercised or stability-tested, by design.
