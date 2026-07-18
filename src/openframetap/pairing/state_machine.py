"""Transport-independent, fail-closed Pocket application pairing state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PairingState(str, Enum):
    IDLE = "idle"
    SUBSCRIBED = "subscribed"
    WAITING_STATUS = "waiting_status"
    WAITING_DEVICE_CONFIRMATION = "waiting_device_confirmation"
    READY_STAGE1_SEND = "ready_stage1_send"
    READY_STAGE2_SEND = "ready_stage2_send"
    PAIRED = "paired"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True, slots=True)
class PairingEvent:
    action: str
    detail: str


class PairingStateMachine:
    """Validate pairing responses; unexpected states never produce a send action."""

    def __init__(self, *, timeout_seconds: float = 15.0, max_attempts: int = 2) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 1 <= max_attempts <= 2:
            raise ValueError("this phase permits one or two attempts")
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.attempts = 0
        self.state = PairingState.IDLE
        self.last_error: str | None = None

    def subscribed(self) -> PairingEvent:
        if self.state is not PairingState.IDLE:
            return self._fail(f"subscribe received in {self.state.value}")
        self.state = PairingState.SUBSCRIBED
        return PairingEvent("none", "FFF4 subscribed; no write authorized")

    def begin_authorized_attempt(self) -> PairingEvent:
        if self.state not in {PairingState.SUBSCRIBED, PairingState.FAILED}:
            return self._fail(f"attempt requested in {self.state.value}")
        if self.attempts >= self.max_attempts:
            return self._fail("maximum pairing attempts reached")
        self.attempts += 1
        self.last_error = None
        self.state = PairingState.WAITING_STATUS
        return PairingEvent("propose_set_pairing_pin", f"confirmed attempt {self.attempts}")

    def pairing_status(self, payload: bytes) -> PairingEvent:
        if self.state is not PairingState.WAITING_STATUS:
            if self.state in {
                PairingState.WAITING_DEVICE_CONFIRMATION,
                PairingState.READY_STAGE1_SEND,
                PairingState.READY_STAGE2_SEND,
                PairingState.PAIRED,
            }:
                return PairingEvent("none", "duplicate pairing-status response ignored")
            return self._fail(f"pairing status received in {self.state.value}")
        if len(payload) < 2 or payload[0] != 0:
            return self._fail(f"unexpected pairing-status payload: {payload.hex()}")
        if payload[1] == 1:
            self.state = PairingState.PAIRED
            return PairingEvent("complete", "device reports already paired")
        if payload[1] == 2:
            self.state = PairingState.WAITING_DEVICE_CONFIRMATION
            return PairingEvent("wait_for_user", "confirm pairing on the Pocket screen")
        return self._fail(f"unknown pairing-status value: {payload[1]}")

    def device_approved(self, payload: bytes) -> PairingEvent:
        if self.state is not PairingState.WAITING_DEVICE_CONFIRMATION:
            return self._fail(f"device approval received in {self.state.value}")
        if payload != b"\x01":
            return self._fail(f"unexpected approval payload: {payload.hex()}")
        self.state = PairingState.READY_STAGE1_SEND
        return PairingEvent("propose_pairing_stage1", "Pocket approval captured")

    def stage1_sent(self) -> PairingEvent:
        """The captured C00746 frame is itself the ACK to device approval."""

        if self.state is not PairingState.READY_STAGE1_SEND:
            return self._fail(f"stage1 sent in {self.state.value}")
        self.state = PairingState.READY_STAGE2_SEND
        return PairingEvent("propose_pairing_stage2", "manually executed stage1 recorded")

    def stage2_sent(self) -> PairingEvent:
        """Complete the captured Mimo pairing sequence after explicit approval."""

        if self.state is not PairingState.READY_STAGE2_SEND:
            return self._fail(f"stage2 sent in {self.state.value}")
        self.state = PairingState.PAIRED
        return PairingEvent("complete", "Pocket approval observed and captured finalization sent")

    def timeout(self) -> PairingEvent:
        return self._fail(f"timeout after {self.timeout_seconds:g}s")

    def disconnect(self) -> PairingEvent:
        self.state = PairingState.DISCONNECTED
        self.last_error = "BLE disconnected"
        return PairingEvent("stop", self.last_error)

    def cancel(self) -> PairingEvent:
        self.state = PairingState.CANCELLED
        self.last_error = "cancelled by user"
        return PairingEvent("stop", self.last_error)

    def _fail(self, detail: str) -> PairingEvent:
        self.state = PairingState.FAILED
        self.last_error = detail
        return PairingEvent("stop", detail)
