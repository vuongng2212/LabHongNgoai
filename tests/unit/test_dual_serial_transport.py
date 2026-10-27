"""Unit tests for the dual TX/RX serial transport and factory wiring."""

from __future__ import annotations

from typing import cast

import pytest
from pytestqt.qtbot import QtBot

import ir_terminal.core.dual_serial_transport as dual_module
from ir_terminal.core.dual_serial_transport import DualSerialTransport
from ir_terminal.core.mock_serial import MockSerialPort
from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.core.socket_transport import SocketTransport
from ir_terminal.core.transport_factory import create_transport


class FakeSerial:
    """Minimal pyserial stand-in capturing writes and serving queued reads."""

    def __init__(self, **kwargs: object) -> None:
        self.port = str(kwargs.get("port"))
        self.baudrate = int(str(kwargs.get("baudrate", 0)))
        self.timeout = kwargs.get("timeout")
        self.write_timeout = kwargs.get("write_timeout")
        self.is_open = True
        self.closed = False
        self.close_calls = 0
        self.flushed = False
        self.flush_calls = 0
        self.fail_close = False
        self.fail_read = False
        self.written: list[bytes] = []
        self.pending = bytearray()

    def write(self, data: bytes) -> int:
        self.written.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        self.flushed = True
        self.flush_calls += 1

    def read(self, size: int) -> bytes:
        if self.fail_read:
            raise OSError("read exploded")
        chunk = bytes(self.pending[:size])
        del self.pending[:size]
        return chunk

    @property
    def in_waiting(self) -> int:
        return len(self.pending)

    def close(self) -> None:
        self.close_calls += 1
        if self.fail_close:
            raise OSError("close exploded")
        self.closed = True
        self.is_open = False


class SerialFactory:
    """Records every FakeSerial created and can fail on a chosen call."""

    def __init__(self) -> None:
        self.created: list[FakeSerial] = []
        self.fail_on: set[int] = set()
        self.always_fail = False

    def __call__(self, **kwargs: object) -> FakeSerial:
        index = len(self.created) + 1
        if self.always_fail or index in self.fail_on:
            raise OSError(f"cannot open port {kwargs.get('port')}")
        fake = FakeSerial(**kwargs)
        self.created.append(fake)
        return fake


@pytest.fixture()
def serial_factory(monkeypatch: pytest.MonkeyPatch) -> SerialFactory:
    """Replaces pyserial's Serial constructor with the recording fake."""
    factory = SerialFactory()
    monkeypatch.setattr(dual_module.serial, "Serial", factory)
    return factory


class TestDualSerialTransportOpen:
    """Verifies handle creation, unified mode and rollback safety."""

    def test_open_creates_separate_tx_and_rx_handles(self, serial_factory: SerialFactory) -> None:
        transport = DualSerialTransport("COM3", "COM4", 57600)

        transport.open()

        assert [handle.port for handle in serial_factory.created] == ["COM3", "COM4"]
        tx, rx = serial_factory.created
        assert cast(object, transport._tx_serial) is tx
        assert cast(object, transport._rx_serial) is rx
        assert transport.tx_port == "COM3"
        assert transport.rx_port == "COM4"
        assert transport.baudrate == 57600
        assert transport.is_single_port is False
        assert transport.is_open is True
        assert tx.baudrate == 57600
        assert rx.baudrate == 57600
        assert tx.write_timeout == 1.0
        assert rx.write_timeout is None

    def test_missing_rx_port_reuses_tx_port(self, serial_factory: SerialFactory) -> None:
        transport = DualSerialTransport("COM7", None, 115200)

        transport.open()

        assert len(serial_factory.created) == 1
        handle = serial_factory.created[0]
        assert cast(object, transport._tx_serial) is handle
        assert cast(object, transport._rx_serial) is handle
        assert transport.rx_port == "COM7"
        assert transport.is_single_port is True
        assert transport.is_open is True

    def test_rollback_closes_tx_when_rx_open_fails(self, serial_factory: SerialFactory) -> None:
        serial_factory.fail_on = {2}
        transport = DualSerialTransport("COM3", "COM4", 115200)

        with pytest.raises(OSError, match="cannot open port COM4"):
            transport.open()

        tx = serial_factory.created[0]
        assert tx.closed is True
        assert transport._tx_serial is None
        assert transport._rx_serial is None
        assert transport.is_open is False

    def test_failed_tx_open_leaves_no_handle(self, serial_factory: SerialFactory) -> None:
        serial_factory.always_fail = True
        transport = DualSerialTransport("COM3", "COM4", 115200)

        with pytest.raises(OSError):
            transport.open()

        assert transport._tx_serial is None
        assert transport._rx_serial is None
        assert transport.is_open is False


class TestDualSerialTransportIO:
    """Verifies TX writes and RX reads are routed to the right handle."""

    def test_write_goes_to_tx_and_read_from_rx(self, serial_factory: SerialFactory) -> None:
        transport = DualSerialTransport("COM3", "COM4", 115200)
        transport.open()
        tx, rx = serial_factory.created
        rx.pending.extend(b"optical")

        assert transport.write(b"payload") == 7
        assert tx.written == [b"payload"]
        assert rx.written == []
        assert transport.in_waiting == 7
        assert transport.read() == b"optical"
        assert transport.in_waiting == 0
        assert transport.read(0) == b""
        assert transport.read() == b""

    def test_single_port_handle_serves_both_directions(self, serial_factory: SerialFactory) -> None:
        transport = DualSerialTransport("COM9", "COM9", 115200)
        transport.open()
        handle = serial_factory.created[0]
        handle.pending.extend(b"loop")

        assert transport.write(b"echo") == 4
        assert handle.written == [b"echo"]
        assert transport.read(4) == b"loop"

    def test_flush_calls_only_tx_flush(self, serial_factory: SerialFactory) -> None:
        transport = DualSerialTransport("COM3", "COM4", 115200)
        transport.open()
        tx, rx = serial_factory.created

        transport.flush()

        assert tx.flushed is True
        assert tx.flush_calls == 1
        assert rx.flushed is False
        assert rx.flush_calls == 0

    def test_flush_on_closed_transport_is_noop(self) -> None:
        transport = DualSerialTransport("COM3", "COM4", 115200)
        transport.flush()

    def test_write_on_closed_transport_raises(self) -> None:
        transport = DualSerialTransport("COM3", "COM4", 115200)

        with pytest.raises(RuntimeError, match="not open for write"):
            transport.write(b"x")

    def test_read_and_in_waiting_are_empty_when_closed(self) -> None:
        transport = DualSerialTransport("COM3", "COM4", 115200)

        assert transport.read() == b""
        assert transport.read(10) == b""
        assert transport.in_waiting == 0

    def test_read_errors_propagate(self, serial_factory: SerialFactory) -> None:
        transport = DualSerialTransport("COM3", "COM4", 115200)
        transport.open()
        rx = serial_factory.created[1]
        rx.fail_read = True

        with pytest.raises(OSError, match="read exploded"):
            transport.read(4)


class TestDualSerialTransportClose:
    """Verifies close is safe, idempotent and leak-free."""

    def test_close_closes_both_handles(self, serial_factory: SerialFactory) -> None:
        transport = DualSerialTransport("COM3", "COM4", 115200)
        transport.open()
        tx, rx = serial_factory.created

        transport.close()

        assert tx.close_calls == 1
        assert rx.close_calls == 1
        assert transport.is_open is False
        assert transport._tx_serial is None
        assert transport._rx_serial is None

    def test_close_unified_handle_once(self, serial_factory: SerialFactory) -> None:
        transport = DualSerialTransport("COM3", "COM3", 115200)
        transport.open()
        handle = serial_factory.created[0]

        transport.close()

        assert handle.close_calls == 1

    def test_close_swallows_handle_errors(self, serial_factory: SerialFactory) -> None:
        transport = DualSerialTransport("COM3", "COM4", 115200)
        transport.open()
        tx, rx = serial_factory.created
        rx.fail_close = True

        transport.close()

        assert tx.closed is True
        assert rx.close_calls == 1
        assert transport.is_open is False

    def test_close_without_open_is_noop(self) -> None:
        transport = DualSerialTransport("COM3", "COM4", 115200)

        transport.close()

        assert transport.is_open is False


class TestTransportFactoryDualPorts:
    """Verifies the factory builds the right transport for each port kind."""

    def test_physical_ports_create_dual_transport(self) -> None:
        transport = create_transport("COM3", "COM4", 230400)

        assert isinstance(transport, DualSerialTransport)
        assert transport.tx_port == "COM3"
        assert transport.rx_port == "COM4"
        assert transport.baudrate == 230400

    def test_rx_port_defaults_to_tx_port(self) -> None:
        transport = create_transport("/dev/ttyUSB0", None, 9600)

        assert isinstance(transport, DualSerialTransport)
        assert transport.tx_port == "/dev/ttyUSB0"
        assert transport.rx_port == "/dev/ttyUSB0"
        assert transport.is_single_port is True

    def test_mock_port_stays_single_mock_transport(self) -> None:
        transport = create_transport("MOCK_COM_LOOPBACK", "MOCK_COM_LOOPBACK", 115200)

        assert isinstance(transport, MockSerialPort)

    def test_link_port_stays_socket_transport(self) -> None:
        transport = create_transport("LINK_A", "LINK_A", 115200)

        assert isinstance(transport, SocketTransport)


class TestSerialManagerDualPorts:
    """Verifies the manager exposes the dual-port connect API."""

    def test_connect_ports_emits_tx_rx_and_baud(self, qtbot: QtBot) -> None:
        manager = SerialManager()
        try:
            with qtbot.waitSignal(manager.ports_connected, timeout=2000) as blocker:
                manager.connect_ports("MOCK_NODE_A", "MOCK_NODE_B", 57600)

            assert blocker.args == ["MOCK_NODE_A", "MOCK_NODE_B", 57600]
            qtbot.waitUntil(lambda: manager.current_rx_port == "MOCK_NODE_B", timeout=1000)
            assert manager.current_tx_port == "MOCK_NODE_A"
            assert manager.current_rx_port == "MOCK_NODE_B"
            assert manager.current_baud == 57600
            assert manager.is_connected is True
        finally:
            manager.shutdown()

    def test_connect_port_keeps_single_port_compatibility(self, qtbot: QtBot) -> None:
        manager = SerialManager()
        try:
            with qtbot.waitSignal(manager.ports_connected, timeout=2000) as blocker:
                manager.connect_port("MOCK_COM_LOOPBACK", 115200)

            assert blocker.args == ["MOCK_COM_LOOPBACK", "MOCK_COM_LOOPBACK", 115200]
        finally:
            manager.shutdown()
