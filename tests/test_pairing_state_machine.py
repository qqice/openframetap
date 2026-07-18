from __future__ import annotations

from dataclasses import dataclass, field

from openframetap.pairing.state_machine import PairingState, PairingStateMachine


@dataclass
class MockTransport:
    proposed_actions: list[str] = field(default_factory=list)

    def apply(self, action: str) -> None:
        if action.startswith("propose_"):
            self.proposed_actions.append(action)


def ready_machine() -> tuple[PairingStateMachine, MockTransport]:
    machine = PairingStateMachine(timeout_seconds=1, max_attempts=2)
    transport = MockTransport()
    transport.apply(machine.subscribed().action)
    transport.apply(machine.begin_authorized_attempt().action)
    return machine, transport


def test_normal_pairing_responses() -> None:
    machine, transport = ready_machine()
    event = machine.pairing_status(b"\x00\x02")
    assert event.action == "wait_for_user"
    transport.apply(machine.device_approved(b"\x01").action)
    transport.apply(machine.stage1_sent().action)
    event = machine.stage2_sent()
    assert event.action == "complete"
    assert machine.state is PairingState.PAIRED
    assert transport.proposed_actions == [
        "propose_set_pairing_pin",
        "propose_pairing_stage1",
        "propose_pairing_stage2",
    ]


def test_already_paired_is_strong_success() -> None:
    machine, _ = ready_machine()
    event = machine.pairing_status(b"\x00\x01")
    assert event.action == "complete"
    assert machine.state is PairingState.PAIRED


def test_timeout_fails_without_retrying_automatically() -> None:
    machine, transport = ready_machine()
    assert machine.timeout().action == "stop"
    assert machine.state is PairingState.FAILED
    assert transport.proposed_actions == ["propose_set_pairing_pin"]


def test_error_response_fails_closed() -> None:
    machine, _ = ready_machine()
    event = machine.pairing_status(b"\x01\x02")
    assert event.action == "stop"
    assert machine.state is PairingState.FAILED


def test_duplicate_status_never_causes_duplicate_send() -> None:
    machine, transport = ready_machine()
    machine.pairing_status(b"\x00\x02")
    event = machine.pairing_status(b"\x00\x02")
    transport.apply(event.action)
    assert event.action == "none"
    assert transport.proposed_actions == ["propose_set_pairing_pin"]


def test_disconnect_stops_state_machine() -> None:
    machine, _ = ready_machine()
    assert machine.disconnect().action == "stop"
    assert machine.state is PairingState.DISCONNECTED


def test_user_cancel_stops_state_machine() -> None:
    machine, _ = ready_machine()
    assert machine.cancel().action == "stop"
    assert machine.state is PairingState.CANCELLED


def test_attempts_never_exceed_two() -> None:
    machine, _ = ready_machine()
    machine.timeout()
    assert machine.begin_authorized_attempt().action == "propose_set_pairing_pin"
    machine.timeout()
    event = machine.begin_authorized_attempt()
    assert event.action == "stop"
    assert machine.attempts == 2
