from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from openframetap.devices.pocket3_livestream import (
    load_fixed_proposal,
    write_prepare_proposal,
)
from openframetap.protocol.commands import (
    CommandRejected,
    SendAuthorization,
    assert_send_allowed,
    get_command_definition,
    validate_command_frame,
)
from openframetap.protocol.duml import decode_duml_frame
from openframetap.protocol.livestream_commands import build_prepare_to_live_stream_frame
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
