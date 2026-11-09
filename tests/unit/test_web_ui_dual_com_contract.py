"""Static contract tests for the dual-COM web UI, bridge client, and launcher."""

from pathlib import Path

import pytest

from ir_terminal.protocol.constants import SUPPORTED_BAUDRATES

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "src" / "ir_terminal" / "ui" / "web"
COMPONENTS_DIR = WEB_DIR / "components"


@pytest.fixture(scope="module")
def index_html() -> str:
    """Returns the generated Web UI entry document."""
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


class TestHardwareDualComUi:
    """Verifies the dual TX/RX selector and dynamic baudrate surface."""

    def test_dual_port_controls_rendered(self, index_html: str) -> None:
        """The generated UI exposes both COM dropdowns and the baudrate container."""
        for element_id in (
            "select-port-tx",
            "select-port-rx",
            "baudrate-selector-container",
            "baudrate-source-label",
        ):
            assert f'id="{element_id}"' in index_html

    def test_single_port_toggle_removed(self, index_html: str) -> None:
        """The shared-port checkbox and its label are gone from markup and script."""
        hardware_html = (COMPONENTS_DIR / "view_hardware.html").read_text(encoding="utf-8")
        script = (WEB_DIR / "js" / "bridge_client.js").read_text(encoding="utf-8")
        assert "single-port-toggle" not in index_html
        assert "single-port-toggle" not in hardware_html
        assert "Use single port" not in index_html
        assert "single-port-toggle" not in script
        assert "isSinglePortMode" not in script

    def test_visual_noise_markers_removed(self, index_html: str) -> None:
        """Fake badges, pulse animations, and slogan strips stay out of the UI."""
        forbidden = (
            "CLEAN LAYERED ARCHITECTURE",
            "880nm NIR Direct LOS",
            "BER &lt; 10",
            "HEALTH 99.8",
            "Auto-Checksum Verification Active",
            "[BURST_TX]",
            "animate-ping",
            "status-pulse",
            "backdrop-blur",
            "modal-pop-in",
            "Port Standby",
            "Port Active",
            "Link Standby",
            "Link Synced",
        )
        for marker in forbidden:
            assert marker not in index_html

    def test_chat_uses_compact_transmit_prefix(self) -> None:
        """The chat composer uses the plain TX > prompt instead of a burst badge."""
        chat_html = (COMPONENTS_DIR / "view_chat.html").read_text(encoding="utf-8")
        assert "TX &gt;" in chat_html
        assert "[BURST_TX]" not in chat_html

    def test_chat_send_controls_define_text_only_contract(self) -> None:
        chat_html = (COMPONENTS_DIR / "view_chat.html").read_text(encoding="utf-8")
        script = (WEB_DIR / "js" / "chat_controller.js").read_text(encoding="utf-8")
        assert 'id="btn-chat-send"' in chat_html
        assert 'id="input-chat-text"' in chat_html
        assert "<span>Send</span>" in chat_html
        assert "Type a text message to send across the optical link..." in chat_html
        assert "exclusively in File Transfer Station" in chat_html
        assert "getElementById('btn-chat-send')" in script
        assert "getElementById('input-chat-text')" in script
        assert "Type a text message before sending." in script


class TestBridgeClientContract:
    """Verifies the JavaScript bridge calls the dual-port backend API."""

    def test_dual_port_api_wired(self) -> None:
        """Connect, rescan, signals, and ribbon summary use TX/RX pairs."""
        script = (WEB_DIR / "js" / "bridge_client.js").read_text(encoding="utf-8")
        assert "connectDualPorts(txPort, rxPort, selectedBaud)" in script
        assert "getAvailableBaudrates" in script
        assert "portConnected.connect((txPort, rxPort, baud)" in script
        assert "TX: ' + txPort + ' • RX: ' + rxPort" in script

    def test_connect_button_uses_explicit_disconnect_state(self) -> None:
        script = (WEB_DIR / "js" / "bridge_client.js").read_text(encoding="utf-8")
        assert "if (btn.id === 'btn-connect')" in script
        assert "if (btn.dataset.state === 'connected')" in script
        assert "bridge.disconnectPort()" in script
        assert "btn.innerText.includes('Connect')" not in script
        assert 'data-icon="link_off">link_off</span> Disconnect' in script
        assert 'data-icon="link">link</span> Connect' in script

    def test_connect_button_starts_in_disconnected_state(self) -> None:
        hardware_html = (COMPONENTS_DIR / "view_hardware.html").read_text(encoding="utf-8")
        assert 'id="btn-connect" data-state="disconnected"' in hardware_html

    def test_default_baudrates_are_optical_values(self) -> None:
        """Backend and browser fallbacks expose only the four optical rates."""
        assert SUPPORTED_BAUDRATES == (2400, 4800, 9600, 19200)
        script = (WEB_DIR / "js" / "bridge_client.js").read_text(encoding="utf-8")
        assert "const DEFAULT_BAUDRATES = [2400, 4800, 9600, 19200];" in script

    def test_baud_click_uses_optical_control_channel(self) -> None:
        """Clicking a baud button sends SET_BAUD instead of reconnecting."""
        script = (WEB_DIR / "js" / "bridge_client.js").read_text(encoding="utf-8")
        assert "setOpticalBaud(" in script
        assert "opticalBaudChanged.connect" in script
        assert "pendingBaud" in script

    def test_hardware_view_shows_fixed_physical_uart(self) -> None:
        """The hardware card labels the optical selector and the fixed UART."""
        hardware_html = (COMPONENTS_DIR / "view_hardware.html").read_text(encoding="utf-8")
        assert "Optical Baudrate (bps)" in hardware_html
        assert 'id="hw-physical-baud"' in hardware_html
        assert "115200 bps FIXED" in hardware_html

    def test_refresh_port_list_preserves_configured_launch_ports(self) -> None:
        """Configured launch ports are injected into entries if missing from scan."""
        script = (WEB_DIR / "js" / "bridge_client.js").read_text(encoding="utf-8")
        assert "targetTx && !entries.some" in script
        assert "targetRx && !entries.some" in script


    def test_manual_ping_has_auto_timeout_timer(self) -> None:
        """Manual ping clears loading and warns when no echo arrives in 1.5s."""
        script = (WEB_DIR / "js" / "bridge_client.js").read_text(encoding="utf-8")
        assert "pingFailTimer" in script
        assert "1500" in script

    def test_settings_modal_debug_diagnostics_contract(self, index_html: str) -> None:
        modal_html = (COMPONENTS_DIR / "settings_modal.html").read_text(encoding="utf-8")
        for element_id in (
            "btn-toggle-debug",
            "settings-debug-panel",
            "debug-physical-baud",
            "debug-optical-baud",
            "debug-log-count",
            "btn-clear-debug-logs",
            "btn-copy-debug-logs",
            "chk-autoscroll-debug",
            "debug-log-console",
        ):
            assert f'id="{element_id}"' in modal_html
            assert f'id="{element_id}"' in index_html

    def test_settings_controller_debug_logging_contract(self) -> None:
        script = (WEB_DIR / "js" / "settings_controller.js").read_text(encoding="utf-8")
        assert "decodeRawBytes" in script
        assert "appendDebugLog" in script
        assert "clearDebugLogs" in script
        assert "copyDebugLogs" in script
        assert "rawTxData" in script
        assert "rawRxData" in script
        assert "opticalBaudChanged" in script


class TestLauncherContract:
    """Verifies the hardware launcher accepts four independent COM ports."""

    def test_com_pair_accepts_four_ports(self) -> None:
        """Start_Com_Pair maps TX_A, RX_A, TX_B, and RX_B onto CLI flags."""
        script = (REPO_ROOT / "scripts" / "Start_Com_Pair.cmd").read_text(encoding="utf-8")
        assert "Start_Com_Pair.cmd [TX_A] [RX_A] [TX_B] [RX_B]" in script
        assert "--tx-port %TX_A%" in script
        assert "--rx-port %RX_A%" in script
        assert "--tx-port %TX_B%" in script
        assert "--rx-port %RX_B%" in script
