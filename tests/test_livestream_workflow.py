from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from openframetap.devices.pocket3_livestream import (
    load_fixed_proposal,
    load_fixed_stream_proposal,
    load_fixed_stream_url,
    load_fixed_start_transport_proposal,
    load_fixed_wifi_proposal,
    write_prepare_recovery_proposal,
    write_prepare_proposal,
    write_stream_proposal,
    write_start_transport_proposal,
    write_wifi_proposal,
)
from openframetap.network.secrets import WifiProvisioningSecrets
from openframetap.protocol.commands import (
    CommandRejected,
    SendAuthorization,
    assert_send_allowed,
    get_command_definition,
    validate_command_frame,
)
from openframetap.protocol.duml import decode_duml_frame
from openframetap.protocol.livestream_commands import (
    build_prepare_to_live_stream_frame,
    build_prepare_stream_stage2_frame,
    build_configure_live_stream_frame,
    build_start_live_stream_transport_frame,
    build_wifi_connect_frame,
)
from openframetap.workflows.pocket3_rtmp import Pocket3RtmpWorkflow


ADDRESS = "00:11:22:33:44:55"
EVIDENCE_SHA = "1" * 64
PAIRING_EVIDENCE = "paired-session-sha256:" + "2" * 64


def test_prepare_frame_round_trip_and_allowlist() -> None:
    raw = build_prepare_to_live_stream_frame()
    decoded = decode_duml_frame(raw)
    command = get_command_definition("prepare_to_live_stream")
    assert decoded.sender == 2 and decoded.receiver == 8
    assert decoded.sequence == 0x8C12
    assert (decoded.flags, decoded.cmd_set, decoded.cmd_id, decoded.payload) == (
        0x40,
        0x02,
        0xE1,
        b"\x1A",
    )
    assert decoded.crc8_valid and decoded.crc16_valid
    validate_command_frame(command, decoded)
    authorization = SendAuthorization.single_command(
        command.name, purpose="fixture", approval_reference="fixture-sha"
    )
    assert_send_allowed(command, authorization)
    with pytest.raises(CommandRejected):
        assert_send_allowed(
            get_command_definition("wifi_connect"), authorization
        )


def test_prepare_stage2_matches_public_pocket3_mimo_capture_and_stays_denied() -> None:
    raw = build_prepare_stream_stage2_frame()
    assert raw.hex() == "551104920208ffab40028e00011c003bc8"
    decoded = decode_duml_frame(raw)
    assert decoded.crc8_valid and decoded.crc16_valid
    assert (decoded.sender, decoded.receiver, decoded.sequence) == (2, 8, 0xFFAB)
    assert (decoded.flags, decoded.cmd_set, decoded.cmd_id, decoded.payload) == (
        0x40,
        0x02,
        0x8E,
        bytes.fromhex("00011c00"),
    )
    command = get_command_definition("prepare_stream_transport")
    validate_command_frame(command, decoded)
    with pytest.raises(CommandRejected, match="denied"):
        assert_send_allowed(
            command,
            SendAuthorization.single_command(
                command.name, purpose="fixture", approval_reference="fixture"
            ),
        )


def test_stream_configuration_frame_is_fixed_720p30_and_round_trips() -> None:
    url = "rtmp://192.168.1.229:1935/live/fixture-key"
    raw = build_configure_live_stream_frame(rtmp_url=url)
    decoded = decode_duml_frame(raw)
    assert (decoded.sender, decoded.receiver, decoded.sequence) == (2, 8, 0x8C2C)
    assert (decoded.flags, decoded.cmd_set, decoded.cmd_id) == (0x40, 0x08, 0x78)
    assert decoded.payload[:12] == bytes.fromhex("002e0004a00f020003000000")
    url_length = int.from_bytes(decoded.payload[12:14], "little")
    assert url_length == len(url.encode())
    assert decoded.payload[14:].decode() == url
    assert decoded.crc8_valid and decoded.crc16_valid
    validate_command_frame(get_command_definition("configure_live_stream"), decoded)


def test_stream_configuration_rejects_non_lan_url() -> None:
    raw = build_configure_live_stream_frame(
        rtmp_url="rtmp://100.125.223.67:1935/live/fixture-key"
    )
    with pytest.raises(CommandRejected, match="LAN policy"):
        validate_command_frame(
            get_command_definition("configure_live_stream"), decode_duml_frame(raw)
        )


def test_stream_proposal_is_private_and_sanitized(tmp_path: Path) -> None:
    stream_key = "fixture-secret-key"
    url = f"rtmp://192.168.1.229:1935/live/{stream_key}"
    payload = write_stream_proposal(
        address=ADDRESS,
        rtmp_url=url,
        private_root=tmp_path / "artifacts" / "private" / "proposals",
        sanitized_root=tmp_path / "artifacts" / "sanitized" / "proposals",
        wifi_result_sha256="5" * 64,
        server_status_sha256="6" * 64,
    )
    private_path = Path(payload["private_proposal"])
    private, raw = load_fixed_stream_proposal(private_path, expected_address=ADDRESS)
    assert load_fixed_stream_url(private_path, expected_address=ADDRESS) == url
    sanitized = Path(payload["sanitized_proposal"]).read_text(encoding="utf-8")
    assert stream_key.encode() in raw
    assert stream_key not in sanitized
    assert url not in sanitized
    assert private["frame_sha256"] == hashlib.sha256(raw).hexdigest()
    assert payload["sequence"] == "0x8C2C"
    assert payload["resolution"] == 720
    assert payload["fps"] == 30
    assert payload["bitrate_kbps"] == 4000
    with pytest.raises(PermissionError, match="address"):
        load_fixed_stream_proposal(private_path, expected_address="AA:BB:CC:DD:EE:FF")


def test_start_transport_frame_matches_reviewed_exact_example_and_override(tmp_path: Path) -> None:
    raw = build_start_live_stream_transport_frame()
    assert raw.hex() == "551304030208b4bb40028e01011a0001013238"
    assert hashlib.sha256(raw).hexdigest() == (
        "a5ea033f25d80ddd6b7ffe2f09b9693abede7c95458fab1da88b3bc140c6d150"
    )
    command = get_command_definition("start_live_stream_transport")
    decoded = decode_duml_frame(raw)
    validate_command_frame(command, decoded)
    with pytest.raises(CommandRejected, match="denied"):
        assert_send_allowed(
            command,
            SendAuthorization.single_command(
                command.name, purpose="fixture", approval_reference="fixture"
            ),
        )
    authorization = SendAuthorization.explicit_single_frame(
        command.name,
        frame_sha256=hashlib.sha256(raw).hexdigest(),
        purpose="reversible fixed start fixture",
        approval_reference="fixture",
        allow_denied_command=True,
    )
    assert_send_allowed(command, authorization)
    proposal = write_start_transport_proposal(
        address=ADDRESS,
        private_root=tmp_path / "artifacts" / "private" / "proposals",
        sanitized_root=tmp_path / "artifacts" / "sanitized" / "proposals",
        configure_result_sha256="7" * 64,
    )
    loaded, loaded_raw = load_fixed_start_transport_proposal(
        Path(proposal["private_proposal"]), expected_address=ADDRESS
    )
    assert loaded_raw == raw
    assert loaded["frame_sha256"] == hashlib.sha256(raw).hexdigest()


def test_prepare_proposal_sha_address_and_sanitization(tmp_path: Path) -> None:
    payload = write_prepare_proposal(
        address=ADDRESS,
        private_root=tmp_path / "artifacts" / "private" / "proposals",
        sanitized_root=tmp_path / "artifacts" / "sanitized" / "proposals",
        server_evidence_sha256=EVIDENCE_SHA,
        pairing_evidence=PAIRING_EVIDENCE,
    )
    private_path = Path(payload["private_proposal"])
    private, raw = load_fixed_proposal(private_path, expected_address=ADDRESS)
    assert hashlib.sha256(raw).hexdigest() == private["frame_sha256"]
    sanitized_text = Path(payload["sanitized_proposal"]).read_text()
    assert ADDRESS not in sanitized_text
    assert payload["frame_sha256"] in sanitized_text
    assert payload["max_send_count"] == 1
    assert payload["automatic_retry"] is False


def test_tampered_or_wrong_address_proposal_is_rejected(tmp_path: Path) -> None:
    payload = write_prepare_proposal(
        address=ADDRESS,
        private_root=tmp_path / "artifacts" / "private" / "proposals",
        sanitized_root=tmp_path / "artifacts" / "sanitized" / "proposals",
        server_evidence_sha256=EVIDENCE_SHA,
        pairing_evidence=PAIRING_EVIDENCE,
    )
    path = Path(payload["private_proposal"])
    with pytest.raises(PermissionError, match="address"):
        load_fixed_proposal(path, expected_address="AA:BB:CC:DD:EE:FF")
    private = json.loads(path.read_text())
    private["frame_sha256"] = "0" * 64
    path.write_text(json.dumps(private))
    with pytest.raises(PermissionError, match="SHA-256"):
        load_fixed_proposal(path, expected_address=ADDRESS)


def test_workflow_rejects_illegal_transition_and_persists(tmp_path: Path) -> None:
    state = Pocket3RtmpWorkflow()
    with pytest.raises(ValueError, match="illegal"):
        state.transition("wifi_sent")
    state.transition("server_ready", evidence={"fixture": True})
    path = tmp_path / "artifacts" / "private" / "workflow.json"
    state.save(path)
    loaded = Pocket3RtmpWorkflow.load(path)
    assert loaded.phase == "server_ready"
    assert loaded.history[0]["evidence"] == {"fixture": True}


def test_cli_send_approved_has_no_arbitrary_hex_option() -> None:
    from openframetap.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["pocket3", "rtmp", "send-approved", "proposal.json", "--hex", "55"]
        )


def test_cli_prepare_recovery_has_no_frame_or_command_override() -> None:
    from openframetap.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "pocket3",
                "rtmp",
                "recover-prepare",
                "proposal.json",
                "--hex",
                "55",
            ]
        )


def test_cli_prepare_wifi_recovery_has_no_frame_or_command_override() -> None:
    from openframetap.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "pocket3",
                "rtmp",
                "recover-prepare-wifi",
                "prepare.json",
                "wifi.json",
                "--command",
                "anything",
            ]
        )


def test_wifi_proposal_cli_has_no_plaintext_credential_options() -> None:
    from openframetap.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["pocket3", "rtmp", "propose", "wifi", "--ssid", "secret", "--psk", "secret"]
        )


def test_wifi_frame_uses_two_length_prefixed_utf8_strings() -> None:
    raw = build_wifi_connect_frame(ssid="Lab5G", psk="fixture-password")
    decoded = decode_duml_frame(raw)
    assert (decoded.sender, decoded.receiver, decoded.sequence) == (2, 7, 0x8C19)
    assert (decoded.flags, decoded.cmd_set, decoded.cmd_id) == (0x40, 0x07, 0x47)
    assert decoded.payload == b"\x05Lab5G\x10fixture-password"
    assert decoded.crc8_valid and decoded.crc16_valid
    validate_command_frame(get_command_definition("wifi_connect"), decoded)


def test_wifi_proposal_is_private_and_sanitized_output_has_no_payload(
    tmp_path: Path,
) -> None:
    secrets = WifiProvisioningSecrets("Fixture5G", "fixture-password")
    payload = write_wifi_proposal(
        address=ADDRESS,
        secrets=secrets,
        private_root=tmp_path / "artifacts" / "private" / "proposals",
        sanitized_root=tmp_path / "artifacts" / "sanitized" / "proposals",
        prepare_result_sha256="3" * 64,
    )
    private_text = Path(payload["private_proposal"]).read_text(encoding="utf-8")
    sanitized_text = Path(payload["sanitized_proposal"]).read_text(encoding="utf-8")
    private_payload = json.loads(private_text)
    private_frame = bytes.fromhex(private_payload["frame_hex"])
    assert b"Fixture5G" in private_frame
    assert b"fixture-password" in private_frame
    assert "Fixture5G" not in sanitized_text
    assert "fixture-password" not in sanitized_text
    assert payload["frame_sha256"] in sanitized_text
    assert private_payload["frame_hex"] not in sanitized_text
    assert "frame_hex" not in sanitized_text
    assert payload["max_send_count"] == 1
    assert payload["automatic_retry"] is False
    loaded, loaded_raw = load_fixed_wifi_proposal(
        Path(payload["private_proposal"]), expected_address=ADDRESS
    )
    assert loaded["frame_sha256"] == payload["frame_sha256"]
    assert loaded_raw == private_frame


def test_wifi_proposal_loader_rejects_tampered_fingerprint(tmp_path: Path) -> None:
    payload = write_wifi_proposal(
        address=ADDRESS,
        secrets=WifiProvisioningSecrets("Fixture5G", "fixture-password"),
        private_root=tmp_path / "artifacts" / "private" / "proposals",
        sanitized_root=tmp_path / "artifacts" / "sanitized" / "proposals",
        prepare_result_sha256="3" * 64,
    )
    path = Path(payload["private_proposal"])
    private = json.loads(path.read_text(encoding="utf-8"))
    private["secret_fingerprints"]["psk_sha256"] = "0" * 64
    path.write_text(json.dumps(private), encoding="utf-8")
    with pytest.raises(PermissionError, match="fingerprints"):
        load_fixed_wifi_proposal(path, expected_address=ADDRESS)


def test_prepare_recovery_proposal_requires_two_human_confirmed_frames(
    tmp_path: Path,
) -> None:
    payload = write_prepare_recovery_proposal(
        address=ADDRESS,
        private_root=tmp_path / "artifacts" / "private" / "proposals",
        sanitized_root=tmp_path / "artifacts" / "sanitized" / "proposals",
        wifi_result_sha256="4" * 64,
    )
    assert payload["automatic_retry"] is False
    assert payload["automatic_follow_up"] is False
    assert payload["human_confirmation_required_per_frame"] is True
    assert payload["wifi_retry_included"] is False
    stage1, stage2 = payload["stages"]
    assert stage1["frame_sha256"] == hashlib.sha256(
        bytes.fromhex(stage1["frame_hex"])
    ).hexdigest()
    assert stage2["frame_hex"] == "551104920208ffab40028e00011c003bc8"
    assert stage2["conditional_on_stage1_ack"] is True
    assert stage1["locally_sent"] is False and stage2["locally_sent"] is False


def test_wifi_proposal_cli_is_offline_and_stdout_is_sanitized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from openframetap.cli import main

    private = tmp_path / "artifacts" / "private"
    sanitized = tmp_path / "artifacts" / "sanitized"
    state_path = private / "workflow.json"
    state = Pocket3RtmpWorkflow(phase="prepare_acknowledged")
    state.save(state_path)
    prepare_result = sanitized / "prepare-result.json"
    prepare_result.parent.mkdir(parents=True)
    prepare_result.write_text(
        json.dumps(
            {
                "status": "prepare_acknowledged",
                "response": {"cmd_set": "0x02", "cmd_id": "0xE1", "payload_hex": "00"},
            }
        ),
        encoding="utf-8",
    )
    secret_file = private / "secrets.env"
    secret_file.write_text(
        "OPENFRAMETAP_WIFI_SSID=Fixture5G\n"
        "OPENFRAMETAP_WIFI_PSK=fixture-password\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        secret_file.chmod(0o600)
    status = main(
        [
            "pocket3",
            "rtmp",
            "propose",
            "wifi",
            "--address",
            ADDRESS,
            "--secret-file",
            str(secret_file),
            "--prepare-result",
            str(prepare_result),
            "--private-root",
            str(private / "proposals"),
            "--sanitized-root",
            str(sanitized / "proposals"),
            "--state-file",
            str(state_path),
        ]
    )
    output = capsys.readouterr().out
    assert status == 0
    assert "Fixture5G" not in output
    assert "fixture-password" not in output
    assert "PROPOSAL ONLY" in output
    assert Pocket3RtmpWorkflow.load(state_path).phase == "wifi_proposed"
