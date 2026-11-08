"""Unit tests for the dual TX/RX serial transport and factory wiring."""

from __future__ import annotations

import ctypes
from typing import cast

import pytest
import serial as pyserial
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
        self.dtr = True
        self.rts = True
        self.is_open = False
        self.closed = False
        self.close_calls = 0
        self.open_calls = 0
        self.fail_open = False
        self.open_error: BaseException = OSError("open exploded")
        self.flushed = False
        self.flush_calls = 0
        self.fail_close = False
        self.fail_read = False
        self.reset_input_calls = 0
        self.reset_output_calls = 0
        self.written: list[bytes] = []
        self.pending = bytearray()

    def open(self) -> None:
        self.open_calls += 1
        if self.fail_open:
            self.is_open = False
            raise self.open_error
        self.is_open = True

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

    def reset_input_buffer(self) -> None:
        self.reset_input_calls += 1
        self.pending.clear()

    def reset_output_buffer(self) -> None:
        self.reset_output_calls += 1

    def close(self) -> None:
        self.close_calls += 1
        if self.fail_close:
            raise OSError("close exploded")
        self.closed = True
        self.is_open = False


class FakeWin32Struct(ctypes.Structure):
    """Placeholder ctypes structure consumed by the Win32 helpers."""


class FakeWin32Api:
    """Records Win32 serial calls performed by the resilient transport."""

    INVALID_HANDLE_VALUE = -1
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    PURGE_TXCLEAR = 4
    PURGE_TXABORT = 1
    PURGE_RXCLEAR = 8
    PURGE_RXABORT = 2

    def __init__(self, handle: int = 42, events: list[str] | None = None) -> None:
        self.handle = handle
        self.events: list[str] = events if events is not None else []
        self.created_paths: list[str] = []
        self.closed_handles: list[int] = []

    def DWORD(self) -> FakeWin32Struct:
        return FakeWin32Struct()

    def COMSTAT(self) -> FakeWin32Struct:
        return FakeWin32Struct()

    def CreateFile(self, *args: object) -> int | None:
        self.created_paths.append(str(args[0]))
        self.events.append("create")
        return self.handle

    def CloseHandle(self, handle: int) -> int:
        self.closed_handles.append(handle)
        self.events.append("close")
        return 1

    def ClearCommError(self, *args: object) -> int:
        self.events.append("clear")
        return 1

    def PurgeComm(self, *args: object) -> int:
        self.events.append("purge")
        return 1


def _make_resilient_serial() -> dual_module.ResilientWin32Serial:
    instance = dual_module.ResilientWin32Serial(port=None, baudrate=115200, timeout=0)
    handle_attribute = "_port_handle"
    setattr(instance, handle_attribute, 42)
    return instance


@pytest.fixture()
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Captures retry delays instead of actually sleeping."""
    delays: list[float] = []

    def fake_sleep(duration: float) -> None:
        delays.append(duration)

    monkeypatch.setattr(dual_module, "_sleep", fake_sleep)
    return delays


class SerialFactory:
    """Records every FakeSerial created and can fail on a chosen call."""

    def __init__(self) -> None:
        self.created: list[FakeSerial] = []
        self.calls = 0
        self.fail_on: set[int] = set()
        self.always_fail = False
        self.failures: dict[int, BaseException] = {}
        self.open_failures: dict[int, BaseException] = {}

    def __call__(self, **kwargs: object) -> FakeSerial:
        self.calls += 1
        index = self.calls
        failure = self.failures.get(index)
        if failure is not None:
            raise failure
        if self.always_fail or index in self.fail_on:
            raise OSError(f"cannot open port {kwargs.get('port')}")
        fake = FakeSerial(**kwargs)
        open_failure = self.open_failures.get(index)
        if open_failure is not None:
            fake.fail_open = True
            fake.open_error = open_failure
        self.created.append(fake)
        return fake


@pytest.fixture()
def serial_factory(monkeypatch: pytest.MonkeyPatch) -> SerialFactory:
    """Replaces the resilient pyserial class with the recording fake."""
    factory = SerialFactory()
    monkeypatch.setattr(dual_module, "ResilientWin32Serial", factory)
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

    def test_single_port_detection_is_case_insensitive(self) -> None:
        transport = DualSerialTransport("com3", "COM3", 115200)

        assert transport.is_single_port is True

    def test_rollback_closes_tx_when_rx_open_fails(self, serial_factory: SerialFactory) -> None:
        serial_factory.open_failures = {2: OSError("cannot open port COM4")}
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

    def test_create_serial_forces_quiescent_dtr_rts(
        self, serial_factory: SerialFactory
    ) -> None:
        transport = DualSerialTransport("COM3", "COM3", 115200)

        transport.open()

        handle = serial_factory.created[0]
        assert handle.port == "COM3"
        assert handle.open_calls == 1
        assert cast(object, handle.dtr) is False
        assert cast(object, handle.rts) is False


class TestOpenRetry:
    """Verifies bounded retry and descriptor cleanup around Error 31."""

    def test_open_recovers_from_transient_error_31(
        self, serial_factory: SerialFactory, no_sleep: list[float]
    ) -> None:
        serial_factory.failures = {
            1: pyserial.SerialException(
                "Cannot configure port, something went wrong. "
                "Original message: PermissionError(13, 'A device attached to "
                "the system is not functioning.', None, 31)"
            )
        }
        transport = DualSerialTransport("COM6", "COM6", 115200)

        transport.open()

        assert transport.is_open is True
        assert serial_factory.calls == 2
        assert len(serial_factory.created) == 1
        assert no_sleep == [dual_module.INITIAL_RETRY_DELAY_SEC]

    def test_open_retries_when_open_raises_error_31(
        self, serial_factory: SerialFactory, no_sleep: list[float]
    ) -> None:
        serial_factory.open_failures = {
            1: pyserial.SerialException(
                "Cannot configure port, something went wrong. "
                "Original message: PermissionError(13, 'A device attached to "
                "the system is not functioning.', None, 31)"
            )
        }
        transport = DualSerialTransport("COM6", "COM6", 115200)

        transport.open()

        assert transport.is_open is True
        assert serial_factory.calls == 2
        assert serial_factory.created[0].open_calls == 1
        assert serial_factory.created[1].open_calls == 1
        assert no_sleep == [dual_module.INITIAL_RETRY_DELAY_SEC]

    def test_retry_exhaustion_closes_every_rollback_handle(
        self, serial_factory: SerialFactory, no_sleep: list[float]
    ) -> None:
        error = pyserial.SerialException(
            "Cannot configure port, something went wrong. "
            "Original message: PermissionError(13, 'A device attached to "
            "the system is not functioning.', None, 31)"
        )
        serial_factory.failures = {2: error, 4: error, 6: error}
        transport = DualSerialTransport("COM3", "COM4", 115200)

        with pytest.raises(pyserial.SerialException, match="31"):
            transport.open()

        tx_handles = [handle for handle in serial_factory.created if handle.port == "COM3"]
        assert serial_factory.calls == 6
        assert len(tx_handles) == 3
        assert all(handle.closed for handle in tx_handles)
        assert all(handle.close_calls == 1 for handle in tx_handles)
        assert transport._tx_serial is None
        assert transport._rx_serial is None
        assert transport.is_open is False
        assert no_sleep == [
            dual_module.INITIAL_RETRY_DELAY_SEC,
            dual_module.INITIAL_RETRY_DELAY_SEC * 2,
        ]

    def test_non_retryable_error_is_not_retried(
        self, serial_factory: SerialFactory, no_sleep: list[float]
    ) -> None:
        serial_factory.failures = {1: OSError("permanent failure")}
        transport = DualSerialTransport("COM6", "COM6", 115200)

        with pytest.raises(OSError, match="permanent failure"):
            transport.open()

        assert serial_factory.calls == 1
        assert no_sleep == []


class TestWin32Helpers:
    """Verifies the Win32 pre-clear helpers stay safe and correctly ordered."""

    def test_pre_clear_opens_clears_purges_and_closes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeWin32Api()
        monkeypatch.setattr(dual_module, "_win32_api", lambda: fake)

        dual_module._win32_pre_clear_path("COM12")

        assert fake.created_paths == ["\\\\.\\COM12"]
        assert fake.events == ["create", "clear", "purge", "close"]
        assert fake.closed_handles == [fake.handle]

    def test_pre_clear_skips_invalid_handle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeWin32Api(handle=-1)
        monkeypatch.setattr(dual_module, "_win32_api", lambda: fake)

        dual_module._win32_pre_clear_path("COM6")

        assert fake.events == ["create"]
        assert fake.closed_handles == []

    def test_win32_api_is_absent_off_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dual_module, "_is_windows", lambda: False)

        assert dual_module._win32_api() is None

    def test_device_path_normalization(self) -> None:
        assert dual_module._win32_device_path("com12") == "\\\\.\\com12"
        assert dual_module._win32_device_path("COM4") == "\\\\.\\COM4"
        assert dual_module._win32_device_path("COM1") == "\\\\.\\COM1"


class TestResilientSerialReconfigure:
    """Verifies the reconfigure hook clears driver errors around SetCommState."""

    def test_clears_before_and_after_base_reconfigure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[str] = []
        fake = FakeWin32Api(events=events)
        monkeypatch.setattr(dual_module, "_is_windows", lambda: True)
        monkeypatch.setattr(dual_module, "_win32_api", lambda: fake)

        def base_reconfigure(_self: object, force_update: bool = False) -> None:
            events.append("reconfigure")

        monkeypatch.setattr(pyserial.Serial, "_reconfigure_port", base_reconfigure)
        instance = _make_resilient_serial()

        instance._reconfigure_port()

        assert events == ["clear", "purge", "reconfigure", "clear", "purge"]

    def test_retries_transient_error_31(
        self, monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]
    ) -> None:
        events: list[str] = []
        fake = FakeWin32Api(events=events)
        attempts = 0
        monkeypatch.setattr(dual_module, "_is_windows", lambda: True)
        monkeypatch.setattr(dual_module, "_win32_api", lambda: fake)

        def base_reconfigure(_self: object, force_update: bool = False) -> None:
            nonlocal attempts
            attempts += 1
            events.append(f"reconfigure:{attempts}")
            if attempts == 1:
                raise pyserial.SerialException(
                    "Cannot configure port, something went wrong. "
                    "Original message: PermissionError(13, 'A device attached "
                    "to the system is not functioning.', None, 31)"
                )

        monkeypatch.setattr(pyserial.Serial, "_reconfigure_port", base_reconfigure)
        instance = _make_resilient_serial()

        instance._reconfigure_port()

        assert attempts == 2
        assert no_sleep == [dual_module.RECONFIGURE_DELAY_SEC]
        assert events == [
            "clear",
            "purge",
            "reconfigure:1",
            "clear",
            "purge",
            "reconfigure:2",
            "clear",
            "purge",
        ]

    def test_delegates_without_win32_calls_off_windows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[str] = []
        fake = FakeWin32Api()
        monkeypatch.setattr(dual_module, "_is_windows", lambda: False)
        monkeypatch.setattr(dual_module, "_win32_api", lambda: fake)

        def base_reconfigure(_self: object, force_update: bool = False) -> None:
            events.append(f"reconfigure:{force_update}")

        monkeypatch.setattr(pyserial.Serial, "_reconfigure_port", base_reconfigure)
        instance = _make_resilient_serial()

        instance._reconfigure_port(force_update=True)

        assert events == ["reconfigure:True"]
        assert fake.events == []

    def test_missing_handle_skips_win32_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events: list[str] = []
        fake = FakeWin32Api()
        monkeypatch.setattr(dual_module, "_is_windows", lambda: True)
        monkeypatch.setattr(dual_module, "_win32_api", lambda: fake)

        def base_reconfigure(_self: object, force_update: bool = False) -> None:
            events.append("reconfigure")

        monkeypatch.setattr(pyserial.Serial, "_reconfigure_port", base_reconfigure)
        instance = dual_module.ResilientWin32Serial(port=None, baudrate=115200, timeout=0)

        instance._reconfigure_port()

        assert events == ["reconfigure"]
        assert fake.events == []


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
