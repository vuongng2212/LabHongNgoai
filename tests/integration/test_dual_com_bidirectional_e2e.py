"""End-to-end test for independently routed TX/RX COM ports on two nodes."""

from __future__ import annotations

import threading
import zlib
from collections import defaultdict
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

import ir_terminal.core.dual_serial_transport as dual_module
from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.services.chat_service import ChatService
from ir_terminal.services.file_transfer_service import FileTransferService, TransferState
from ir_terminal.services.storage_service import StorageService


class FakeSerialHandle:
    """Routes writes to the peer RX buffer and reads from its own buffer."""

    def __init__(self, bus: FakePortBus, **kwargs: object) -> None:
        self._bus = bus
        self.port = str(kwargs.get("port"))
        self.baudrate = int(str(kwargs.get("baudrate", 0)))
        self.is_open = True

    def write(self, data: bytes) -> int:
        payload = bytes(data)
        self._bus.deliver(self.port, payload)
        return len(payload)

    def read(self, size: int) -> bytes:
        return self._bus.take(self.port, size)

    @property
    def in_waiting(self) -> int:
        return self._bus.waiting(self.port)

    def close(self) -> None:
        self.is_open = False


class FakePortBus:
    """Models four physical UART endpoints wired across two STM32 boards."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._routes: dict[str, str] = {}
        self._buffers: dict[str, bytearray] = defaultdict(bytearray)
        self.delivered: dict[str, int] = defaultdict(int)
        self.handles: list[FakeSerialHandle] = []

    def route(self, tx_port: str, rx_port: str) -> None:
        self._routes[tx_port] = rx_port

    def open(self, **kwargs: object) -> FakeSerialHandle:
        handle = FakeSerialHandle(self, **kwargs)
        self.handles.append(handle)
        return handle

    def deliver(self, port: str, data: bytes) -> None:
        rx_port = self._routes.get(port)
        if not rx_port:
            return
        with self._lock:
            self.delivered[port] += len(data)
            self._buffers[rx_port].extend(data)

    def take(self, port: str, size: int) -> bytes:
        with self._lock:
            buffer = self._buffers[port]
            chunk = bytes(buffer[:size])
            del buffer[:size]
            return chunk

    def waiting(self, port: str) -> int:
        with self._lock:
            return len(self._buffers[port])


@pytest.fixture()
def dual_bus(monkeypatch: pytest.MonkeyPatch) -> FakePortBus:
    bus = FakePortBus()
    bus.route("COM3", "COM6")
    bus.route("COM5", "COM4")
    monkeypatch.setattr(dual_module.serial, "Serial", bus.open)
    return bus


def open_node_pair(qtbot: QtBot, baud: int = 115200) -> tuple[SerialManager, SerialManager]:
    manager_a = SerialManager()
    manager_b = SerialManager()
    with qtbot.waitSignal(manager_a.ports_connected, timeout=5000) as connected_a:
        manager_a.connect_ports("COM3", "COM4", baud)
    with qtbot.waitSignal(manager_b.ports_connected, timeout=5000) as connected_b:
        manager_b.connect_ports("COM5", "COM6", baud)
    assert connected_a.args == ["COM3", "COM4", baud]
    assert connected_b.args == ["COM5", "COM6", baud]
    return manager_a, manager_b


def test_dual_com_opens_independent_tx_rx_handles(qtbot: QtBot, dual_bus: FakePortBus) -> None:
    manager_a, manager_b = open_node_pair(qtbot, baud=57600)
    try:
        assert manager_a.current_tx_port == "COM3"
        assert manager_a.current_rx_port == "COM4"
        assert manager_a.current_baud == 57600
        assert manager_b.current_tx_port == "COM5"
        assert manager_b.current_rx_port == "COM6"
        assert manager_b.current_baud == 57600

        opened = {handle.port: handle for handle in dual_bus.handles}
        assert {"COM3", "COM4", "COM5", "COM6"} <= set(opened)
        assert opened["COM3"] is not opened["COM4"]
        assert opened["COM5"] is not opened["COM6"]
        assert all(opened[name].is_open for name in ("COM3", "COM4", "COM5", "COM6"))
        assert all(opened[name].baudrate == 57600 for name in ("COM3", "COM4", "COM5", "COM6"))
    finally:
        manager_a.shutdown()
        manager_b.shutdown()


def test_dual_com_relays_utf8_chat_both_directions(
    qtbot: QtBot, dual_bus: FakePortBus, tmp_path: Path
) -> None:
    manager_a, manager_b = open_node_pair(qtbot)
    try:
        chat_a = ChatService(
            manager_a,
            StorageService(data_dir=tmp_path / "chat_a"),
            local_name="Node A",
            remote_name="Node B",
        )
        chat_b = ChatService(
            manager_b,
            StorageService(data_dir=tmp_path / "chat_b"),
            local_name="Node B",
            remote_name="Node A",
        )

        outgoing = "Xin chào Node B — quang 880 nm ✓"
        with qtbot.waitSignal(chat_b.message_received, timeout=8000) as received_b:
            chat_a.send_message(outgoing)
        text_b, _timestamp_b, is_outgoing_b = received_b.args
        assert text_b == outgoing
        assert is_outgoing_b is False

        reply = "ACK từ Node B — đã nhận đủ khung ✓"
        with qtbot.waitSignal(chat_a.message_received, timeout=8000) as received_a:
            chat_b.send_message(reply)
        text_a, _timestamp_a, is_outgoing_a = received_a.args
        assert text_a == reply
        assert is_outgoing_a is False

        assert dual_bus.delivered["COM3"] >= len(outgoing.encode("utf-8"))
        assert dual_bus.delivered["COM5"] >= len(reply.encode("utf-8"))
    finally:
        manager_a.shutdown()
        manager_b.shutdown()


def test_dual_com_large_binary_transfer_with_arq(
    qtbot: QtBot, dual_bus: FakePortBus, tmp_path: Path
) -> None:
    manager_a, manager_b = open_node_pair(qtbot)
    try:
        source_file = tmp_path / "dual_com_firmware.bin"
        content = (bytes(range(256)) + b"IR_OWC_DUAL_COM_CHUNK_") * 40
        source_file.write_bytes(content)
        expected_crc = zlib.crc32(content) & 0xFFFFFFFF

        downloads_b = tmp_path / "node_b_downloads"
        service_a = FileTransferService(
            serial_manager=manager_a,
            chunk_size=240,
            storage_service=StorageService(data_dir=tmp_path / "a_data"),
        )
        service_b = FileTransferService(
            serial_manager=manager_b,
            chunk_size=240,
            storage_service=StorageService(data_dir=tmp_path / "b_data"),
            transfers_dir=downloads_b,
        )

        with (
            qtbot.waitSignal(service_a.transfer_completed, timeout=30000) as done_a,
            qtbot.waitSignal(service_b.transfer_completed, timeout=30000) as done_b,
        ):
            service_a.start_transfer(str(source_file))

        assert done_a.args[0] == source_file.name
        assert done_a.args[1] == len(content)
        assert done_a.args[3] == f"0x{expected_crc:08X}"
        assert service_a.state == TransferState.COMPLETED

        assert done_b.args[0] == source_file.name
        assert done_b.args[1] == len(content)
        assert done_b.args[3] == f"0x{expected_crc:08X}"
        assert service_b.state == TransferState.COMPLETED

        received = downloads_b / source_file.name
        assert received.exists()
        assert received.read_bytes() == content
        assert dual_bus.delivered["COM3"] >= len(content)
        assert dual_bus.delivered["COM5"] > 0
    finally:
        manager_a.shutdown()
        manager_b.shutdown()
