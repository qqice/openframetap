"""Transport-independent, fail-closed Pocket application pairing state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PairingState(str, Enum):
    IDLE = "idle"
    SUBSCRIBED = "subscribed"
    WAITING_STATUS = "waiting_status"
    WAITING_DEVICE_CONFIRMATION = "waiting_device_confirmation"
    WAITING_STAGE1_RESPONSE = "waiting_stage1_response"
    WAITING_STAGE2_RESPONSE = "waiting_stage2_response"
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
        return PairingEvent("send_set_pairing_pin", f"authorized attempt {self.attempts}")

    def pairing_status(self, payload: bytes) -> PairingEvent:
        if self.state is not PairingState.WAITING_STATUS:
            if self.state in {
                PairingState.WAITING_DEVICE_CONFIRMATION,
                PairingState.WAITING_STAGE1_RESPONSE,
                PairingState.WAITING_STAGE2_RESPONSE,
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
        self.state = PairingState.WAITING_STAGE1_RESPONSE
        return PairingEvent("send_pairing_stage1", "Pocket approval captured")

    def stage1_response(self, payload: bytes) -> PairingEvent:
        if self.state is not PairingState.WAITING_STAGE1_RESPONSE:
            return self._fail(f"stage1 response received in {self.state.value}")
        if payload not in {b"", b"\x00"}:
            return self._fail(f"unexpected stage1 payload: {payload.hex()}")
        self.state = PairingState.WAITING_STAGE2_RESPONSE
        return PairingEvent("send_pairing_stage2", "stage1 acknowledged")

    def stage2_response(self, payload: bytes) -> PairingEvent:
        if self.state is not PairingState.WAITING_STAGE2_RESPONSE:
            return self._fail(f"stage2 response received in {self.state.value}")
        if payload and payload[0] not in {0, 1}:
            return self._fail(f"unexpected stage2 payload: {payload.hex()}")
        self.state = PairingState.PAIRED
        return PairingEvent("complete", "pairing stage2 accepted")

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
