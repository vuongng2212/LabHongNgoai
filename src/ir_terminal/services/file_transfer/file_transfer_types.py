"""Public transfer state shared by the service and its protocol handlers."""

from enum import Enum, auto


class TransferState(Enum):
    """File transfer state machine states."""

    IDLE = auto()
    HANDSHAKING = auto()
    TRANSFERRING = auto()
    FINALIZING = auto()
    RECEIVING = auto()
    COMPLETED = auto()
    FAILED = auto()
    ABORTED = auto()
