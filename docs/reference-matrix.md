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
| Header magic is `0x55`; the 10-bit length includes header, payload, and CRC16; usual version is 1. | `djictl` message encoder/parser, `lib-osmo-ble` DUML builder, and the reverse-engineering dissector agree. Four published notification frames decode to their exact captured lengths. | High | Locally validated on 4,718 live FFF4 frames across two sessions. |
| CRC8 uses polynomial `0x31`, initial `0xEE`, reflected input/output, no xor-out. | `djictl` and `lib-osmo-ble` independently expose the same parameters; published frames validate byte-for-byte. | High | All 4,718 local live frames validated. |
| CRC16 uses polynomial `0x1021`, initial `0x496C`, reflected input/output, no xor-out; result is stored little-endian. | `djictl`, `lib-osmo-ble`, `node-osmo`, and Moblin agree; published frames validate exact trailing values. | High | All 4,718 local live frames validated. |
| Bytes 4 and 5 are sender and receiver component IDs. | `djictl` models an `InterfaceID{Sender, Receiver}` and published notifications consistently point toward app component `0x02`. Other projects sometimes expose the pair as a little-endian `target`, obscuring direction. | High for bytes, medium for all component names | Capture inference; raw values are always retained. |
| Bytes 6–7 are a big-endian 16-bit sequence/transaction ID. | `djictl`, `lib-osmo-ble`, and the published dissector use big-endian. The published frame text assigns `41 10` to ID `0x4110`. `node-osmo` and current Moblin use little-endian and conflict. | High | Local frame sequences advance coherently only under the big-endian interpretation. |
| Flag `0x40` means ACK required and `0x80` means response; `0xC0` is response/ACK. Low three bits are retained as encryption selector. | `djictl` defines the two high bits; captured request/response traffic matches. Encryption semantics have not been exercised. | High for ACK/response; low for encryption semantics | Capture inference. |
| Bytes 9 and 10 are `cmdSet` and `cmdId`; bytes 11 through `length-3` are payload. | All five projects and published dissector agree. | High | Published-capture verified; local live validation pending. |

## BLE transport conclusions

| Conclusion | Sources and agreement | Confidence | Local hardware status |
| --- | --- | --- | --- |
| `FFF4` carries device-to-app DUML notifications and `FFF5` accepts app-to-device `write-without-response`. | `lib-osmo-ble`, Moblin, and `djictl` agree on the roles; published btmon text shows notifications on value handle `0x002d` and writes on `0x0030`. | High | Two passive sessions captured 4,718 FFF4 notifications; the later human-executed pairing candidate is independently evidenced as two ATT Write Commands to FFF5 handle `0x0030`. |
| A notification is not guaranteed to contain one complete DUML frame. | ATT MTU may be 23 and DUML length can reach 1023; implementations buffer input, although their resynchronization quality differs. | High | Architectural requirement; live fragmentation pending. |
| FFF4 subscription itself is read-only at the DJI application protocol level. | BLE notification subscription writes only the standard CCCD. No DUML command is sent to FFF5 by OpenFrameTap's listen path. | High | Local btmon shows only CCCD handle `0x002e` enable/disable writes and no write to FFF5 value handle `0x0030`. |

## Pairing conclusions and conflicts

| Conclusion | Sources and agreement | Confidence | Local hardware status |
| --- | --- | --- | --- |
| Pairing status command is `cmdSet 0x07`, `cmdId 0x45`; captured response flags are `0xC0`, sender/receiver `0x07/0x02`, with payload `00 01` (already paired) or `00 02` (confirmation required). | `djictl`, `lib-osmo-ble`, and published captures agree on command and response values. | High | Local Pocket returned exact `C0/07/45`, sequence `72AA`, payload `00 02` after the human-executed request. |
| Approval notification is `0x400746` with payload `01`; stage-one app ACK is `0xC00746` with payload `00`. | `djictl` and published captures agree. | High | Local Pocket sent ten `40/07/46 payload 01` requests after screen confirmation. Stage-one ACK is generated but not yet transmitted. |
| Stage two is app `0x02` to pairer `0x88`, flags `0x40`, `cmdSet/cmdId 0x00/0x32`, payload `31 31 00 00 00`. | `djictl` and a published full capture agree; OpenFrameTap reproduces captured frame `551204c7028874aa4000323131000000426a`. | High | Offline/capture verified only. |
| Before the first DUML request, `djictl` writes `01 00` to its pairing-request characteristic (FFF4/value handle `0x002e`). | `djictl` and derived `lib-osmo-ble` do this. Moblin and `node-osmo` do not show the same trigger. | Low/contested | OpenFrameTap will not send this during passive listen or before approval. |
| `set_pairing_pin` request payload format and default PIN. | `djictl` uses packed string `001749319286102` plus packed PIN `5160`; `lib-osmo-ble` retains the identifier but defaults to `love`. Moblin and `node-osmo` instead prepend byte `20` plus ASCII `284ae5b8d76b3375a04a6417ad71bea3`, then a packed PIN; Moblin defaults to `mbln`. These may not be independent implementations. | High for the reviewed identifier + `5160` frame on this Pocket; no universal default claim | Local hardware returned the expected matching pairing-status response and subsequent approval requests. |

## Passive telemetry candidates

| Message | Evidence | Confidence | Decode policy |
| --- | --- | --- | --- |
| `0x000081` device-info candidate | Published traffic contains a payload beginning with printable `hg212`; the source hypothesizes Pocket 3 identity. | Medium | Preserve raw payload and expose only the ASCII prefix as a candidate. |
| `0x000d02` battery/status candidate | Published payloads contain plausible values 88 and 100 at offset 20. `node-osmo` and Moblin use that offset; `lib-osmo-ble` instead uses offset 0. | Medium for offset 20, still local-unverified | Decode offset 20 only when payload length is at least 21 and value is 0–100; retain the full payload. |
| `0x000405` gimbal-status candidate | Published captures and multiple projects name it gimbal status, but field layouts are inconsistent and were not behaviorally correlated here. | Low | Classify the message without converting bytes to pitch/roll/yaw. |
| `0xC00745` pairing status | Public sources agree on `00 01`/`00 02`; the local request received `00 02` with matching sequence. | High | Decode as `confirmation_required`. |
| `0x400746` pairing approval request | Public Mimo capture identifies it immediately after Pocket confirmation; local hardware emitted payload `01` ten times after the user's screen action. | High | Record Pocket-screen confirmation; never auto-ACK. |

## Pairing state graph

```text
IDLE --FFF4 subscribed--> SUBSCRIBED
SUBSCRIBED --human executes one confirmed candidate--> WAITING_STATUS
WAITING_STATUS --00 01--> PAIRED (already paired)
WAITING_STATUS --00 02--> WAITING_DEVICE_CONFIRMATION
WAITING_DEVICE_CONFIRMATION --400746 payload 01--> PROPOSE_STAGE1
PROPOSE_STAGE1 --separate human-confirmed frame + evidence--> PROPOSE_STAGE2
PROPOSE_STAGE2 --separate human-confirmed frame + evidence--> PAIRED

Any unexpected payload, timeout, disconnect, or user cancellation --> stop/fail
No transition automatically invokes a BLE write
An explicitly initiated retry is possible only while attempts < 2
```

BlueZ bonding is not a node in this state graph. It is a separate system-layer mechanism and is neither requested nor used as a substitute for the DJI application-layer exchange.

## Safety decision

The current send allowlist contains only `set_pairing_pin`, `pairing_stage1_ack`, and `pairing_stage2`. Even those require the local owner to type the full candidate SHA-256, after which the remote runtime creates a narrow `SendAuthorization` for that one invocation. The transport decodes the frame, validates both CRCs, verifies sender/receiver and command IDs against the definition, and then applies the authorization before it can call Bleak's write API. One invocation cannot select or send another frame. Gimbal, camera, streaming, and Wi-Fi commands are a hard denylist in `protocol/commands.py`.
