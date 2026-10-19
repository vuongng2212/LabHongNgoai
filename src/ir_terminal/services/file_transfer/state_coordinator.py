"""Owns the externally visible transfer state value and transitions."""

from __future__ import annotations

from .file_transfer_types import TransferState


class StateCoordinator:
    """Stores one serialized transfer state and returns prior state on change."""

    def __init__(self, initial: TransferState = TransferState.IDLE) -> None:
        self._state = initial

    @property
    def state(self) -> TransferState:
        """Returns the current application-facing state."""
        return self._state

    def transition(self, new_state: TransferState) -> TransferState:
        """Sets a state and returns the previous state for transition logging."""
        previous = self._state
        self._state = new_state
        return previous
