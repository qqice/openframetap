# Pocket 3 BLE/DUML reference matrix

This matrix separates public-source conclusions from OpenFrameTap capture evidence. A value is not marked as Pocket 3 hardware-verified merely because multiple projects copied the same implementation. Repository snapshots were read-only shallow clones under the ignored `artifacts/local/references/` directory.

## Reference snapshots

| Project | Snapshot | Relevant files |
| --- | --- | --- |
| `xaionaro-go/djictl` | `ddeced5422fe3a27075602d41b49e61ca60c99d8` | `pkg/duml/{crc.go,message.go,message_type.go,interface_id.go}`, `pkg/djible/interface_app_to_wifi_ground_station_pair.go`, `pkg/djible/device.go` |
| `xaionaro/reverse-engineering-dji` | `5c9278ff0b53bbe0d03ea6c830aab13ac775871e` | `mimo/ble/message_types.md`, `mimo/ble/wireshark-dissector/dji-ble-message.c` |
| `yigitkonur/lib-osmo-ble` | `021e96c2bec7e9a2a81296292545bc1ee432af49` | `src/protocol/{duml.mjs,constants.mjs}`, `src/transport/ble.mjs`, `src/connection.mjs` |
| `datagutt/node-osmo` | `cec92aec9304a5cc3dae7f7de541eef38ebb680e` | `src/message.ts`, `src/device.ts` |
| `eerimoq/moblin` | `4eaf8dd14a0454f2082577c6915e8f7fa1d622ac` | `Moblin/Integrations/Dji/DjiMessage.swift`, `DjiDevice/DjiDevice.swift`, `DjiDevice/DjiDeviceMessage.swift` |

## Wire-format conclusions

| Conclusion | Sources and agreement | Confidence | Pocket 3 status |
| --- | --- | --- | --- |
| Header magic is `0x55`; the 10-bit length includes header, payload, and CRC16; usual version is 1. | `djictl` message encoder/parser, `lib-osmo-ble` DUML builder, and the reverse-engineering dissector agree. Four published notification frames decode to their exact captured lengths. | High | Locally validated on 17,469 live FFF4 frames across the two initial passive sessions, final paired session, and complete controlled-motion session. |
| CRC8 uses polynomial `0x31`, initial `0xEE`, reflected input/output, no xor-out. | `djictl` and `lib-osmo-ble` independently expose the same parameters; published frames validate byte-for-byte. | High | All 17,469 complete-session local live frames validated. |
| CRC16 uses polynomial `0x1021`, initial `0x496C`, reflected input/output, no xor-out; result is stored little-endian. | `djictl`, `lib-osmo-ble`, `node-osmo`, and Moblin agree; published frames validate exact trailing values. | High | All 17,469 complete-session local live frames validated. |
| Bytes 4 and 5 are sender and receiver component IDs. | `djictl` models an `InterfaceID{Sender, Receiver}` and published notifications consistently point toward app component `0x02`. Other projects sometimes expose the pair as a little-endian `target`, obscuring direction. | High for bytes, medium for all component names | Capture inference; raw values are always retained. |
| Bytes 6–7 are a big-endian 16-bit sequence/transaction ID. | `djictl`, `lib-osmo-ble`, and the published dissector use big-endian. The published frame text assigns `41 10` to ID `0x4110`. `node-osmo` and current Moblin use little-endian and conflict. | High | Local frame sequences advance coherently only under the big-endian interpretation. |
| Flag `0x40` means ACK required and `0x80` means response; `0xC0` is response/ACK. Low three bits are retained as encryption selector. | `djictl` defines the two high bits; captured request/response traffic matches. Encryption semantics have not been exercised. | High for ACK/response; low for encryption semantics | Capture inference. |
| Bytes 9 and 10 are `cmdSet` and `cmdId`; bytes 11 through `length-3` are payload. | All five projects and published dissector agree. | High | Local request/response matching on `07/45` and 2,531-frame final capture validate the layout. |

## BLE transport conclusions

| Conclusion | Sources and agreement | Confidence | Local hardware status |
| --- | --- | --- | --- |
| `FFF4` carries device-to-app DUML notifications and `FFF5` accepts app-to-device `write-without-response`. | `lib-osmo-ble`, Moblin, and `djictl` agree on the roles; published btmon text shows notifications on value handle `0x002d` and writes on `0x0030`. | High | Two passive sessions plus the final paired session captured 7,249 FFF4 notifications; each human-executed 34-byte pairing frame is independently evidenced as two ATT Write Commands to FFF5 handle `0x0030`. |
| A notification is not guaranteed to contain one complete DUML frame. | ATT MTU may be 23 and DUML length can reach 1023; implementations buffer input, although their resynchronization quality differs. | High | Architectural requirement; live fragmentation pending. |
| FFF4 subscription itself is read-only at the DJI application protocol level. | BLE notification subscription writes only the standard CCCD. No DUML command is sent to FFF5 by OpenFrameTap's listen path. | High | Local btmon shows only CCCD handle `0x002e` enable/disable writes and no write to FFF5 value handle `0x0030`. |

## Pairing conclusions and conflicts

| Conclusion | Sources and agreement | Confidence | Local hardware status |
| --- | --- | --- | --- |
| Pairing status command is `cmdSet 0x07`, `cmdId 0x45`; captured response flags are `0xC0`, sender/receiver `0x07/0x02`, with payload `00 01` (already paired) or `00 02` (confirmation required). | `djictl`, `lib-osmo-ble`, and published captures agree on command and response values. | High | Local Pocket returned exact matching-sequence responses: `00 02` on the first attempt and `00 01` on the final attempt. |
| Approval notification is `0x400746` with payload `01`; stage-one app ACK is `0xC00746` with payload `00`. | `djictl` and published captures agree. | High | Local Pocket sent ten `40/07/46 payload 01` requests after screen confirmation. A later new connection contained no approval request, and the exact-prerequisite guard correctly caused zero stage-one writes. |
| Stage two is app `0x02` to pairer `0x88`, flags `0x40`, `cmdSet/cmdId 0x00/0x32`, payload `31 31 00 00 00`. | `djictl` and a published full capture agree; OpenFrameTap reproduces captured frame `551204c7028874aa4000323131000000426a`. | High for the reference flow | Offline/capture verified only; the local Pocket returned `already_paired`, so OpenFrameTap correctly did not propose or send this frame. |
| Before the first DUML request, `djictl` writes `01 00` to its pairing-request characteristic (FFF4/value handle `0x002e`). | `djictl` and derived `lib-osmo-ble` do this. Moblin and `node-osmo` do not show the same trigger. | Low/contested | OpenFrameTap will not send this during passive listen or before approval. |
| `set_pairing_pin` request payload format and default PIN. | `djictl` uses packed string `001749319286102` plus packed PIN `5160`; `lib-osmo-ble` retains the identifier but defaults to `love`. Moblin and `node-osmo` instead prepend byte `20` plus ASCII `284ae5b8d76b3375a04a6417ad71bea3`, then a packed PIN; Moblin defaults to `mbln`. These may not be independent implementations. | High for the reviewed identifier + `5160` frame on this Pocket; no universal default claim | Local hardware returned the expected matching pairing-status response and subsequent approval requests. |

## Passive telemetry candidates

| Message | Evidence | Confidence | Decode policy |
| --- | --- | --- | --- |
| `0x000081` device-info candidate | Published traffic associates printable `hg212` with Pocket 3. The controlled local Pocket emitted 260 identical 64-byte payloads with that prefix. | Medium | Preserve the raw payload and expose only the ASCII prefix as a candidate; one-device association does not prove a universal model-code mapping. |
| `0x000d02` battery/status candidate | `node-osmo` and `djictl` use offset 20 for the 34-byte layout; `lib-osmo-ble` conflicts at offset 0. The controlled local screen observations 74 and 72 matched offset 20 exactly within 246 ms. | High for the observed 34-byte Pocket 3 layout | Decode offset 20 only when payload length is at least 21 and value is 0–100; retain the full payload and provenance. Repeat once independently before `confirmed`. |
| `0x000405` gimbal-status candidate | `lib-osmo-ble` names offsets 0/2/4, while reverse-engineering notes leave the 49-byte payload unknown and `djictl` has no field decoder. Controlled local motion supports int16 LE candidates at offsets 16 (yaw, medium), 20 (pitch, low), and 22 (roll, medium). | Medium for family and yaw/roll candidates; low for pitch | Keep `_candidate` names and raw values. Do not select a physical scale. The public offsets 0/2/4 layout is rejected for this observed payload. |
| `0x000427` sparse state candidate | `djictl` calls it `KeepAlive`; published and local payload layouts vary. Local data toggled one byte after pitch-down and both roll actions and restored after neutral return. | Low | Preserve both payload values and event-aligned transitions. Do not treat it as continuous three-axis telemetry or assign a bit semantic. |
| `0x00041C` and `0x000438` constant status candidates | Public names are speculative. Local controlled motion retained `48` for all 521 `04/1C` frames and `0000646400` for all 261 `04/38` frames. | Unknown | Preserve unchanged; the controlled session rejects direct changing-axis interpretations. |
| `0xC00745` pairing status | Public sources agree on `00 01`/`00 02`; local requests received both exact values with matching sequence. | High | Decode `00 01` as `already_paired` and `00 02` as `confirmation_required`. |
| `0x400746` pairing approval request | Public Mimo capture identifies it immediately after Pocket confirmation; local hardware emitted payload `01` ten times after the user's screen action. | High | Record Pocket-screen confirmation; never auto-ACK. |
| `0x000280` source-labeled pairing-started message | The published reverse-engineering fixture labels this family as pairing started, but the final local session captured it 643 times before and after an explicit `already_paired` response. | Low; local evidence rejects the pairing semantic | Preserve the raw payload as `camera_status_02_80_candidate`; do not expose a pairing-started state. |

## Pairing state graph

```text
IDLE --FFF4 subscribed--> SUBSCRIBED
SUBSCRIBED --human executes one confirmed candidate--> WAITING_STATUS
WAITING_STATUS --00 01--> PAIRED (already paired)
WAITING_STATUS --00 02--> WAITING_DEVICE_CONFIRMATION
WAITING_DEVICE_CONFIRMATION --400746 payload 01--> PROPOSE_STAGE1
PROPOSE_STAGE1 --same connection + separate human-confirmed frame--> PROPOSE_STAGE2
PROPOSE_STAGE2 --same connection + separate human-confirmed frame--> PAIRED

Any unexpected payload, timeout, disconnect, or user cancellation --> stop/fail
No transition automatically invokes a BLE write
An explicitly initiated retry is possible only while attempts < 2
```

BlueZ bonding is not a node in this state graph. It is a separate system-layer mechanism and is neither requested nor used as a substitute for the DJI application-layer exchange.

## Safety decision

The current send allowlist contains only `set_pairing_pin`, `pairing_stage1_ack`, and `pairing_stage2`. Even those require the local owner to type the full candidate SHA-256, after which the remote runtime creates a narrow `SendAuthorization` for that one invocation. The transport decodes the frame, validates both CRCs, verifies sender/receiver and command IDs against the definition, and then applies the authorization before it can call Bleak's write API. One invocation cannot select or send another frame. Gimbal, camera, streaming, and Wi-Fi commands are a hard denylist in `protocol/commands.py`. The two permitted pairing attempts are complete: only `set_pairing_pin` was sent, once in each attempt, and the final explicit status was `already_paired`.
