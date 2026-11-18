"""Unit tests for the open-loop paced file streaming service."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, cast

import pytest
from PySide6.QtCore import QObject, Signal
from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import (
    PACKET_SIZE,
    SEQ_END,
    SEQ_HEADER,
)
from ir_terminal.protocol.framer import pack_header_payload
from ir_terminal.protocol.packet import WinFormPacket
from ir_terminal.services.file_transfer_service import (
    FileTransferService,
    TransferState,
    paced_delay_ms,
    sanitize_received_name,
)
from ir_terminal.services.storage_service import StorageService


class FakeSerialManager(QObject):
    """Captures transmitted frames and injects received frames."""

    packet_received: ClassVar[Signal] = Signal(object)

    def __init__(self, baud: int = 19200) -> None:
        super().__init__()
        self.current_baud = baud
        self.sent: list[tuple[int, bytes]] = []

    def send_packet(self, sequence: int, data: bytes = b"") -> None:
        """Records one outgoing frame."""
        self.sent.append((sequence, bytes(data)))

    def deliver(self, sequence: int, data: bytes = b"") -> None:
        """Emits one incoming frame."""
        self.packet_received.emit(WinFormPacket(sequence=sequence, length=len(data), data=data))


def make_service(
    tmp_path: Path,
    baud: int = 19200,
    *,
    auto_save: bool = True,
    chunk_size: int = PACKET_SIZE,
) -> tuple[FileTransferService, FakeSerialManager]:
    """Builds a service backed by the frame-capturing fake manager."""
    storage = StorageService(data_dir=tmp_path / "data")
    manager = FakeSerialManager(baud)
    service = FileTransferService(
        cast(SerialManager, manager),
        chunk_size=chunk_size,
        storage_service=storage,
        transfers_dir=tmp_path / "transfers",
    )
    service.set_auto_save(auto_save)
    return service, manager


class TestPacedDelay:
    """Verifies the WinForm C# inter-frame pacing formula."""

    @pytest.mark.parametrize(
        ("length", "baud", "expected"),
        [
            (264, 19200, 148),
            (264, 9600, 285),
            (264, 4800, 560),
            (264, 2400, 1110),
            (8, 19200, 15),
        ],
    )
    def test_formula(self, length: int, baud: int, expected: int) -> None:
        assert paced_delay_ms(length, baud) == expected

    def test_invalid_inputs(self) -> None:
        with pytest.raises(ValueError):
            paced_delay_ms(256, 0)
        with pytest.raises(ValueError):
            paced_delay_ms(-1, 9600)


class TestSanitize:
    """Verifies announced file names are reduced to safe base names."""

    def test_strips_directories_and_control_characters(self) -> None:
        assert sanitize_received_name(r"..\evil/pa:th?.bin") == "pa_th_.bin"

    def test_empty_name_falls_back(self) -> None:
        assert sanitize_received_name("   ") == "received.bin"

    def test_long_name_is_truncated(self) -> None:
        assert len(sanitize_received_name("a" * 500)) == 200


class TestTransmission:
    """Verifies HEADER, paced DATA and END transmission."""

    def test_full_transmission_sequence(self, qtbot: QtBot, tmp_path: Path) -> None:
        service, manager = make_service(tmp_path)
        source = tmp_path / "hello.bin"
        source.write_bytes(b"A" * 600)

        with qtbot.waitSignal(service.transfer_completed, timeout=5000):
            service.start_transfer(str(source))

        sequences = [sequence for sequence, _ in manager.sent]
        assert sequences == [SEQ_HEADER, 0, 1, 2, SEQ_END]
        assert manager.sent[1][1] == b"A" * PACKET_SIZE
        assert manager.sent[2][1] == b"A" * PACKET_SIZE
        assert manager.sent[3][1] == b"A" * 88
        assert service.state is TransferState.COMPLETED

    def test_cancel_aborts_transmission(self, qtbot: QtBot, tmp_path: Path) -> None:
        service, manager = make_service(tmp_path)
        source = tmp_path / "large.bin"
        source.write_bytes(b"B" * (PACKET_SIZE * 20))

        with qtbot.waitSignal(service.transfer_failed, timeout=2000):
            service.start_transfer(str(source))
            service.cancel_transfer()

        assert service.state is TransferState.ABORTED
        assert SEQ_END not in [sequence for sequence, _ in manager.sent]

    def test_missing_file_is_rejected(self, tmp_path: Path) -> None:
        service, _ = make_service(tmp_path)
        with pytest.raises(ValueError):
            service.start_transfer(str(tmp_path / "missing.bin"))
        assert service.state is TransferState.IDLE

    def test_second_transfer_while_active_is_rejected(self, qtbot: QtBot, tmp_path: Path) -> None:
        service, _ = make_service(tmp_path)
        source = tmp_path / "active.bin"
        source.write_bytes(b"C" * 1024)
        service.start_transfer(str(source))

        with pytest.raises(ValueError):
            service.start_transfer(str(source))

        service.cancel_transfer()


class TestReception:
    """Verifies HEADER, DATA offset writes and END finalization."""

    def test_complete_reception_is_finalized(self, qtbot: QtBot, tmp_path: Path) -> None:
        service, manager = make_service(tmp_path)
        payload0 = b"0" * PACKET_SIZE
        payload1 = b"1" * 44

        with qtbot.waitSignal(service.transfer_completed, timeout=2000):
            manager.deliver(SEQ_HEADER, pack_header_payload("incoming.bin", 2))
            manager.deliver(1, payload1)
            manager.deliver(0, payload0)
            manager.deliver(SEQ_END)

        received = (tmp_path / "transfers" / "incoming.bin").read_bytes()
        assert received == payload0 + payload1
        assert service.state is TransferState.COMPLETED

    def test_missing_packets_finalize_partial_file(self, qtbot: QtBot, tmp_path: Path) -> None:
        service, manager = make_service(tmp_path)

        with qtbot.waitSignal(service.transfer_failed, timeout=2000) as blocker:
            manager.deliver(SEQ_HEADER, pack_header_payload("gap.bin", 2))
            manager.deliver(1, b"Z" * 10)
            manager.deliver(SEQ_END)

        assert "1 lost" in blocker.args[0]
        received = (tmp_path / "transfers" / "gap.bin").read_bytes()
        assert received == b"\x00" * PACKET_SIZE + b"Z" * 10
        assert service.state is TransferState.FAILED

    def test_timeout_keeps_partial_when_auto_save_enabled(self, tmp_path: Path) -> None:
        service, manager = make_service(tmp_path, auto_save=True)
        manager.deliver(SEQ_HEADER, pack_header_payload("timeout.bin", 3))
        manager.deliver(0, b"Q" * 20)

        service._on_rx_timeout()

        assert service.state is TransferState.FAILED
        assert (tmp_path / "transfers" / "timeout.bin.partial").read_bytes() == b"Q" * 20

    def test_timeout_discards_partial_when_auto_save_disabled(self, tmp_path: Path) -> None:
        service, manager = make_service(tmp_path, auto_save=False)
        manager.deliver(SEQ_HEADER, pack_header_payload("timeout.bin", 3))
        manager.deliver(0, b"Q" * 20)

        service._on_rx_timeout()

        assert service.state is TransferState.FAILED
        assert not (tmp_path / "transfers" / "timeout.bin.partial").exists()

    def test_cancel_reception_with_auto_save(self, tmp_path: Path) -> None:
        service, manager = make_service(tmp_path, auto_save=True)
        manager.deliver(SEQ_HEADER, pack_header_payload("cancel.bin", 2))
        manager.deliver(0, b"R" * 16)

        service.cancel_transfer()

        assert service.state is TransferState.ABORTED
        assert (tmp_path / "transfers" / "cancel.bin.partial").read_bytes() == b"R" * 16

    def test_invalid_header_is_ignored(self, tmp_path: Path) -> None:
        service, manager = make_service(tmp_path)
        manager.deliver(SEQ_HEADER, b"\x01")

        assert service.state is TransferState.IDLE
        assert not (tmp_path / "transfers").exists() or not list(
            (tmp_path / "transfers").iterdir()
        )

    def test_out_of_range_chunk_is_ignored(self, tmp_path: Path) -> None:
        service, manager = make_service(tmp_path)
        manager.deliver(SEQ_HEADER, pack_header_payload("range.bin", 1))
        manager.deliver(9, b"X" * 10)

        assert service.state is TransferState.RECEIVING
        service.cancel_transfer()

    def test_non_winform_object_is_ignored(self, tmp_path: Path) -> None:
        service, manager = make_service(tmp_path)
        manager.deliver(SEQ_HEADER, b"not a frame")

        assert service.state is TransferState.IDLE


class TestConfiguration:
    """Verifies persisted transfer settings."""

    def test_settings_round_trip(self, tmp_path: Path) -> None:
        service, _ = make_service(tmp_path)
        target = tmp_path / "archive"

        config = service.update_config(
            max_file_size_bytes=1024,
            transfers_dir=target,
            auto_save=False,
        )

        assert config["max_file_size_bytes"] == 1024
        assert Path(str(config["transfers_dir"])) == target.resolve()
        assert config["auto_save"] is False
        assert service.uses_extended_protocol is False

    def test_size_cap_rejects_large_file(self, tmp_path: Path) -> None:
        service, _ = make_service(tmp_path)
        service.set_max_file_size_bytes(4)
        source = tmp_path / "big.bin"
        source.write_bytes(b"12345")

        with pytest.raises(ValueError):
            service.start_transfer(str(source))

    def test_invalid_directory_is_rejected(self, tmp_path: Path) -> None:
        service, _ = make_service(tmp_path)
        target = tmp_path / "file.txt"
        target.write_text("not a directory", encoding="utf-8")

        with pytest.raises(ValueError):
            service.set_transfers_dir(target)
