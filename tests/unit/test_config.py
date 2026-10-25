"""Unit tests for launch configuration, CLI flags, and instance isolation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ir_terminal.config import (
    DEFAULT_LINK_BASE_PORT,
    LaunchConfig,
    build_instance_context,
    parse_launch_args,
)
from ir_terminal.utils.path_helpers import (
    InvalidInstanceIdError,
    get_app_data_dir,
    get_webengine_dirs,
    normalize_instance_id,
)


@pytest.fixture(autouse=True)
def _clean_instance_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removes ambient dual-node env so CLI parsing is deterministic."""
    monkeypatch.delenv("IR_INSTANCE", raising=False)
    monkeypatch.delenv("IR_ENABLE_DUAL_MOCK", raising=False)


class TestParseLaunchArgs:
    """Verifies the new dual-node CLI flags and defaults."""

    def test_defaults_are_single_app(self) -> None:
        config = parse_launch_args([])
        assert config.instance is None
        assert config.link_role == "off"
        assert config.link_active is False
        assert config.preferred_port is None
        assert config.baud == 115200
        assert config.auto_connect is False

    def test_host_role_defaults_to_node_a(self) -> None:
        config = parse_launch_args(["--link-role", "host"])
        assert config.instance == "A"
        assert config.effective_link_role == "host"
        assert config.link_active is True
        assert config.preferred_port == "LINK_A"

    def test_join_role_defaults_to_node_b(self) -> None:
        config = parse_launch_args(["--link-role", "join"])
        assert config.instance == "B"
        assert config.effective_link_role == "join"
        assert config.preferred_port == "LINK_B"

    def test_auto_role_follows_instance(self) -> None:
        assert parse_launch_args(["--instance", "A", "--link-role", "auto"]).effective_link_role == "host"
        assert parse_launch_args(["--instance", "B", "--link-role", "auto"]).effective_link_role == "join"

    def test_explicit_port_and_flags(self) -> None:
        config = parse_launch_args(
            [
                "--instance",
                "A",
                "--port",
                "COM11",
                "--baud",
                "9600",
                "--auto-connect",
                "--window-pos",
                "left",
                "--allow-multiple",
            ]
        )
        assert config.preferred_port == "COM11"
        assert config.baud == 9600
        assert config.auto_connect is True
        assert config.window_pos == "left"
        assert config.allow_multiple is True

    def test_mock_transport_prefers_loopback_port(self) -> None:
        config = parse_launch_args(["--transport", "mock"])
        assert config.preferred_port == "MOCK_COM_LOOPBACK"

    def test_tx_and_rx_port_flags(self) -> None:
        config = parse_launch_args(["--tx-port", "COM3", "--rx-port", "COM4"])
        assert config.tx_port == "COM3"
        assert config.rx_port == "COM4"
        assert config.effective_tx_port == "COM3"
        assert config.effective_rx_port == "COM4"
        assert config.preferred_port == "COM3"

    def test_legacy_port_feeds_both_effective_ports(self) -> None:
        config = parse_launch_args(["--port", "COM9"])
        assert config.effective_tx_port == "COM9"
        assert config.effective_rx_port == "COM9"

    def test_explicit_ports_override_legacy_port(self) -> None:
        config = parse_launch_args(
            ["--port", "COM9", "--tx-port", "COM3", "--rx-port", "COM4"]
        )
        assert config.effective_tx_port == "COM3"
        assert config.effective_rx_port == "COM4"

    def test_partial_dual_ports_fall_back_to_legacy_port(self) -> None:
        tx_only = parse_launch_args(["--tx-port", "COM3"])
        assert tx_only.effective_tx_port == "COM3"
        assert tx_only.effective_rx_port is None

        rx_only = parse_launch_args(["--rx-port", "COM4"])
        assert rx_only.effective_tx_port is None
        assert rx_only.effective_rx_port == "COM4"

    def test_supported_baudrates_default_list(self) -> None:
        config = parse_launch_args([])
        assert config.supported_baudrates == (
            9600,
            38400,
            57600,
            115200,
        )

    def test_link_endpoints_use_base_port(self) -> None:
        config = parse_launch_args(["--instance", "A", "--link-role", "host", "--link-base-port", "7100"])
        assert config.link_endpoints == {
            "LINK_A": ("127.0.0.1", 7100),
            "LINK_B": ("127.0.0.1", 7101),
        }

    def test_instance_env_is_honored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IR_INSTANCE", "B")
        assert parse_launch_args([]).instance == "B"

    def test_dual_mock_env_enables_link_auto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("IR_ENABLE_DUAL_MOCK", "1")
        config = parse_launch_args(["--instance", "B"])
        assert config.effective_link_role == "join"

    def test_transport_link_implies_role(self) -> None:
        config = parse_launch_args(["--transport", "link", "--instance", "B"])
        assert config.link_active is True
        assert config.preferred_port == "LINK_B"

    def test_explicit_serial_transport_overrides_dual_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IR_ENABLE_DUAL_MOCK", "1")
        config = parse_launch_args(["--transport", "serial", "--port", "COM11"])
        assert config.link_active is False
        assert config.preferred_port == "COM11"

    def test_unknown_platform_flags_are_ignored(self) -> None:
        config = parse_launch_args(["--instance", "A", "-platform", "offscreen"])
        assert config.instance == "A"


class TestInstanceContext:
    """Verifies per-instance directories and UI/chat identity."""

    def test_node_a_layout_and_identity(self) -> None:
        ctx = build_instance_context(
            LaunchConfig(instance="A", link_role="host", link_base_port=DEFAULT_LINK_BASE_PORT)
        )
        expected = get_app_data_dir("A")
        assert ctx.instance_id == "A"
        assert ctx.data_dir == expected
        assert ctx.transfers_dir == expected / "transfers"
        assert ctx.logs_dir == expected / "logs"
        assert ctx.webengine_cache_dir == expected / "webengine" / "cache"
        assert ctx.lock_path == expected / "app.lock"
        assert ctx.display_name == "Node A"
        assert ctx.role_label == "Host"
        assert ctx.local_station == "Station Alpha"
        assert ctx.remote_station == "Station Bravo"
        assert "Node A - Host" in ctx.window_title
        assert "LINK_A" in ctx.window_title

    def test_node_b_swaps_station_names(self) -> None:
        ctx = build_instance_context(LaunchConfig(instance="B", link_role="join"))
        assert ctx.display_name == "Node B"
        assert ctx.role_label == "Client"
        assert ctx.local_station == "Station Bravo"
        assert ctx.remote_station == "Station Alpha"

    def test_profile_keys_differ_between_instances(self) -> None:
        ctx_a = build_instance_context(LaunchConfig(instance="A"))
        ctx_b = build_instance_context(LaunchConfig(instance="B"))
        assert ctx_a.profile_key != ctx_b.profile_key

    def test_single_mode_keeps_backward_compatible_names(self) -> None:
        ctx = build_instance_context(LaunchConfig())
        assert ctx.instance_id is None
        assert ctx.display_name == "Single Node"
        assert ctx.local_station == "Local Station Alpha"
        assert ctx.remote_station == "Remote Station Bravo"

    def test_invalid_instance_id_is_rejected(self) -> None:
        with pytest.raises(InvalidInstanceIdError):
            build_instance_context(LaunchConfig(instance="../../escape"))


class TestPathHelperIsolation:
    """Verifies instance validation and WebEngine directory isolation."""

    def test_normalize_instance_id(self) -> None:
        assert normalize_instance_id(" a ") == "A"
        assert normalize_instance_id("lab-2") == "LAB-2"
        assert normalize_instance_id(None) is None
        assert normalize_instance_id("   ") is None
        with pytest.raises(InvalidInstanceIdError):
            normalize_instance_id("bad/name")

    def test_webengine_dirs_are_per_instance(self) -> None:
        cache_a, storage_a = get_webengine_dirs("A")
        cache_b, storage_b = get_webengine_dirs("B")
        assert cache_a != cache_b
        assert storage_a != storage_b
        assert cache_a.is_dir()
        assert storage_a.is_dir()
        assert cache_a.parent.name == "webengine"

    def test_instance_subtree_matches_data_root(self) -> None:
        override = Path(os.environ["IR_DATA_DIR"])
        assert get_app_data_dir("B") == override / "instances" / "B"
