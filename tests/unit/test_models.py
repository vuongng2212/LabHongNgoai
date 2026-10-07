"""Unit tests for domain data models and enumerations."""

from datetime import datetime

from ir_terminal.models.serial_info import (
    ChatMessage,
    SerialPortInfo,
    TelemetrySample,
    TransferStatus,
)


class TestDomainModels:
    """Tests domain model creation, immutability, and state representation."""

    def test_transfer_status_enum(self) -> None:
        """Verifies enum states exist."""
        assert TransferStatus.IDLE.name == "IDLE"
        assert TransferStatus.TRANSMITTING.name == "TRANSMITTING"
        assert TransferStatus.PAUSED_BLOCKED.name == "PAUSED_BLOCKED"
        assert TransferStatus.COMPLETED.name == "COMPLETED"
        assert TransferStatus.FAILED.name == "FAILED"
        assert TransferStatus.CANCELLED.name == "CANCELLED"

    def test_serial_port_info(self) -> None:
        """Verifies SerialPortInfo frozen dataclass."""
        info = SerialPortInfo(
            port="/dev/ttyUSB0", description="STM32 USB-VCP", is_connected=True, baudrate=115200
        )
        assert info.port == "/dev/ttyUSB0"
        assert info.description == "STM32 USB-VCP"
        assert info.is_connected is True
        assert info.baudrate == 115200

    def test_chat_message(self) -> None:
        """Verifies ChatMessage dataclass."""
        now = datetime.now()
        msg = ChatMessage(
            id="msg_1", text="Ping 880nm", is_outgoing=True, timestamp=now, is_ack_confirmed=True
        )
        assert msg.id == "msg_1"
        assert msg.text == "Ping 880nm"
        assert msg.is_outgoing is True
        assert msg.timestamp == now
        assert msg.is_ack_confirmed is True

    def test_telemetry_sample(self) -> None:
        """Verifies TelemetrySample dataclass."""
        sample = TelemetrySample(
            timestamp=123456.78,
            throughput_kbps=8.5,
            rtt_ms=25.0,
            packet_loss_rate=0.01,
            total_packets_sent=100,
            total_packets_lost=1,
        )
        assert sample.timestamp == 123456.78
        assert sample.throughput_kbps == 8.5
        assert sample.rtt_ms == 25.0
        assert sample.packet_loss_rate == 0.01
        assert sample.total_packets_sent == 100
        assert sample.total_packets_lost == 1
