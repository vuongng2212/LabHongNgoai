"""Unit tests for the ASCII baud-control channel grammar."""

from __future__ import annotations

import pytest

from ir_terminal.protocol.control_channel import (
    CONTROL_LINE_MAX_BYTES,
    ControlKind,
    encode_control_line,
    format_baud_err,
    format_baud_ok,
    format_notify_baud,
    format_set_baud,
    is_control_line,
    normalize_outgoing_control_line,
    parse_control_line,
)


class TestParseControlLine:
    """Verifies every control message kind and rejection rule."""

    def test_parse_baud_ok(self) -> None:
        message = parse_control_line("BAUD_OK:19200\n")
        assert message is not None
        assert message.kind == ControlKind.BAUD_OK
        assert message.baudrate == 19200
        assert message.raw == "BAUD_OK:19200"

    def test_parse_notify_baud(self) -> None:
        message = parse_control_line(b"NOTIFY_BAUD:4800\r\n")
        assert message is not None
        assert message.kind == ControlKind.NOTIFY_BAUD
        assert message.baudrate == 4800

    def test_parse_set_baud_echo(self) -> None:
        message = parse_control_line("SET_BAUD:2400\n")
        assert message is not None
        assert message.kind == ControlKind.SET_BAUD
        assert message.baudrate == 2400

    def test_parse_baud_err_reason(self) -> None:
        message = parse_control_line("BAUD_ERR:unsupported rate\n")
        assert message is not None
        assert message.kind == ControlKind.BAUD_ERR
        assert message.reason == "unsupported rate"
        assert message.baudrate is None

    @pytest.mark.parametrize(
        "line",
        [
            "",
            "\n",
            "BAUD_OK",
            "BAUD_OK:",
            "BAUD_OK:abc",
            "BAUD_OK:0",
            "BAUD_OK:-1",
            "BAUD_OK:38400",
            "BAUD_OK:14400",
            "BAUD_OK:999999",
            "BAUD_OK:12345678",
            "BAUD_OK:19200:extra",
            "UNKNOWN:19200",
            "BAUD_ERR:",
            "BAUD_ERR:" + "x" * 49,
            "BAUD_OK:19200\nBAUD_OK:4800",
            "BAUD_OK:19é00",
        ],
    )
    def test_rejects_malformed_lines(self, line: str) -> None:
        assert parse_control_line(line) is None
        assert is_control_line(line) is False

    def test_rejects_overlong_line(self) -> None:
        line = "BAUD_ERR:" + "x" * CONTROL_LINE_MAX_BYTES
        assert parse_control_line(line) is None


class TestOutgoingControlLine:
    """Verifies outgoing validation and canonical formatting."""

    def test_normalizes_terminators(self) -> None:
        assert normalize_outgoing_control_line("SET_BAUD:19200\r\n") == "SET_BAUD:19200"
        assert encode_control_line("SET_BAUD:19200") == b"SET_BAUD:19200\n"

    @pytest.mark.parametrize(
        "line",
        ["", "\r\n", "SET_BAUD:19200\nSET_BAUD:4800", "SET_BAUD:19é00", "x" * 65],
    )
    def test_rejects_invalid_outgoing_lines(self, line: str) -> None:
        with pytest.raises(ValueError):
            normalize_outgoing_control_line(line)

    def test_format_helpers(self) -> None:
        assert format_set_baud(19200) == "SET_BAUD:19200"
        assert format_baud_ok(19200) == "BAUD_OK:19200"
        assert format_notify_baud(4800) == "NOTIFY_BAUD:4800"
        assert format_baud_err("nope") == "BAUD_ERR:nope"

    def test_format_set_baud_rejects_unsupported_rate(self) -> None:
        with pytest.raises(ValueError):
            format_set_baud(38400)
