"""Launch configuration and per-instance isolation context.

A single build of IR-OWC Lab Terminal can run as one production app, or as two
independent nodes (``--instance A`` / ``--instance B``) on the same PC. This
module is the single source of truth for:

* CLI/env parsing (:class:`LaunchConfig` + :func:`parse_launch_args`), and
* filesystem isolation (:class:`InstanceContext` + :func:`build_instance_context`)

so ``app.py`` no longer has to guess the instance after logging starts.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from ir_terminal.protocol.constants import SUPPORTED_BAUDRATES
from ir_terminal.utils.path_helpers import (
    get_app_data_dir,
    get_logs_dir,
    get_transfers_dir,
    get_webengine_dirs,
    is_dual_mock_enabled,
    normalize_instance_id,
)

DEFAULT_BAUD: int = 115200
DEFAULT_LINK_HOST: str = "127.0.0.1"
DEFAULT_LINK_BASE_PORT: int = 6001
DEFAULT_LINK_A_PORT_NAME: str = "LINK_A"
DEFAULT_LINK_B_PORT_NAME: str = "LINK_B"
DEFAULT_MOCK_PORT_NAME: str = "MOCK_COM_LOOPBACK"

TRANSPORT_MODES: tuple[str, ...] = ("auto", "serial", "link", "mock")
LINK_ROLES: tuple[str, ...] = ("off", "host", "join", "auto")
WINDOW_POSITIONS: tuple[str, ...] = ("normal", "left", "right")

LINK_CONNECT_RETRIES: int = 20
LINK_CONNECT_RETRY_INTERVAL_SEC: float = 0.25
LINK_CONNECT_ATTEMPT_TIMEOUT_SEC: float = 0.5


@dataclass(frozen=True)
class LaunchConfig:
    """Immutable launch options for a single application process."""

    instance: str | None = None
    transport: str = "auto"
    port: str | None = None
    tx_port: str | None = None
    rx_port: str | None = None
    baud: int = DEFAULT_BAUD
    supported_baudrates: tuple[int, ...] = SUPPORTED_BAUDRATES
    link_role: str = "off"
    link_host: str = DEFAULT_LINK_HOST
    link_base_port: int = DEFAULT_LINK_BASE_PORT
    link_token: str | None = None
    auto_connect: bool = False
    allow_multiple: bool = False
    broker_only: bool = False
    window_pos: str = "normal"
    loss_rate: float = 0.0
    latency_ms: float = 5.0
    los_blocked: bool = False

    @property
    def effective_link_role(self) -> str:
        """Resolves ``auto`` to ``host`` (Node A / default) or ``join`` (Node B)."""
        role = (self.link_role or "off").strip().lower()
        if role != "auto":
            return role
        instance = (self.instance or "").strip().upper()
        return "join" if instance and instance != "A" else "host"

    @property
    def link_active(self) -> bool:
        """Returns True when an in-app TCP link (host/join) is requested."""
        return self.effective_link_role in ("host", "join")

    @property
    def effective_tx_port(self) -> str | None:
        """Returns the TX port: explicit ``--tx-port`` wins over ``--port``."""
        for candidate in (self.tx_port, self.port):
            if candidate and candidate.strip():
                return candidate.strip()
        return None

    @property
    def effective_rx_port(self) -> str | None:
        """Returns the RX port: explicit ``--rx-port`` wins over ``--port``."""
        for candidate in (self.rx_port, self.port):
            if candidate and candidate.strip():
                return candidate.strip()
        return None

    @property
    def preferred_port(self) -> str | None:
        """Returns the port the UI should preselect / auto-connect to.

        Precedence: explicit ``--tx-port``/``--port`` wins, then the LINK port
        derived from the role, then the mock loopback when ``--transport mock``.
        """
        if self.effective_tx_port:
            return self.effective_tx_port
        role = self.effective_link_role
        if role == "host":
            return DEFAULT_LINK_A_PORT_NAME
        if role == "join":
            return DEFAULT_LINK_B_PORT_NAME
        if self.transport == "mock":
            return DEFAULT_MOCK_PORT_NAME
        return None

    @property
    def link_endpoints(self) -> dict[str, tuple[str, int]]:
        """Maps virtual LINK port names to the configured TCP endpoints."""
        base = self.link_base_port
        return {
            DEFAULT_LINK_A_PORT_NAME: (self.link_host, base),
            DEFAULT_LINK_B_PORT_NAME: (self.link_host, base + 1),
        }


@dataclass(frozen=True)
class InstanceContext:
    """Resolved per-instance filesystem layout and user-facing identity."""

    instance_id: str | None
    data_dir: Path
    transfers_dir: Path
    logs_dir: Path
    webengine_cache_dir: Path
    webengine_storage_dir: Path
    lock_path: Path
    profile_key: str
    display_name: str
    role_label: str
    local_station: str
    remote_station: str
    window_title: str


def _role_label(config: LaunchConfig) -> str:
    """Returns a short human label for the active transport role."""
    role = config.effective_link_role
    if role == "host":
        return "Host"
    if role == "join":
        return "Client"
    if config.effective_tx_port:
        return "Serial"
    if config.transport == "mock":
        return "Mock"
    return ""


def _station_names(instance: str | None) -> tuple[str, str]:
    """Returns (local, remote) chat station names for the instance role."""
    alpha, bravo = "Station Alpha", "Station Bravo"
    if instance == "B":
        return bravo, alpha
    if instance == "A":
        return alpha, bravo
    return "Local Station Alpha", "Remote Station Bravo"


def _window_title(display_name: str, role_label: str, port: str | None) -> str:
    """Builds a window title that makes dual-node identity obvious."""
    base = "SpectraLink IR-OWC Workstation"
    tag = f"{display_name} - {role_label}" if role_label else display_name
    title = f"{base} [{tag}]"
    if port:
        title += f" {port}"
    return title


def build_instance_context(config: LaunchConfig) -> InstanceContext:
    """Resolves the isolated data layout and identity for a launch config.

    Args:
        config: Parsed launch options.

    Returns:
        InstanceContext with every directory pre-created.

    Raises:
        InvalidInstanceIdError: If ``config.instance`` is not a safe label.
    """
    instance = normalize_instance_id(config.instance)
    data_dir = get_app_data_dir(instance)
    transfers_dir = get_transfers_dir(instance)
    logs_dir = get_logs_dir(instance)
    cache_dir, storage_dir = get_webengine_dirs(instance)
    lock_path = data_dir / "app.lock"

    node_label = instance or "single"
    digest = hashlib.sha1(str(data_dir).lower().encode("utf-8")).hexdigest()[:8]
    profile_key = f"ir-terminal-{node_label.lower()}-{digest}"

    display_name = f"Node {instance}" if instance else "Single Node"
    role_label = _role_label(config)
    local_station, remote_station = _station_names(instance)

    return InstanceContext(
        instance_id=instance,
        data_dir=data_dir,
        transfers_dir=transfers_dir,
        logs_dir=logs_dir,
        webengine_cache_dir=cache_dir,
        webengine_storage_dir=storage_dir,
        lock_path=lock_path,
        profile_key=profile_key,
        display_name=display_name,
        role_label=role_label,
        local_station=local_station,
        remote_station=remote_station,
        window_title=_window_title(display_name, role_label, config.preferred_port),
    )


def parse_launch_args(argv: list[str] | None = None) -> LaunchConfig:
    """Parses CLI arguments (plus IR_INSTANCE / IR_ENABLE_DUAL_MOCK env).

    Unknown args are ignored so the desktop launcher can pass through platform
    flags (mirrors the previous ``parse_known_args`` behavior).

    Args:
        argv: Argument list, or None to use ``sys.argv[1:]``.

    Returns:
        Immutable LaunchConfig.
    """
    parser = argparse.ArgumentParser(
        prog="IR_OWC_Lab_Terminal",
        description="IR-OWC Lab Terminal — single app or dual-node (A/B) mode.",
    )
    parser.add_argument(
        "--instance",
        default=None,
        help="Instance id (A/B); isolates data, logs and WebEngine profile.",
    )
    parser.add_argument(
        "--transport",
        choices=TRANSPORT_MODES,
        default="auto",
        help="Transport selection hint (auto/serial/link/mock).",
    )
    parser.add_argument(
        "--port",
        default=None,
        help="Explicit port name: COM11, LINK_A, MOCK_COM_LOOPBACK.",
    )
    parser.add_argument(
        "--tx-port",
        default=None,
        help="Explicit TX port name (overrides --port for outgoing bytes).",
    )
    parser.add_argument(
        "--rx-port",
        default=None,
        help="Explicit RX port name (overrides --port for incoming bytes).",
    )
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="Baud rate.")
    parser.add_argument(
        "--link-role",
        choices=LINK_ROLES,
        default=None,
        help="off (default) | host | join | auto (in-app TCP link).",
    )
    parser.add_argument("--link-host", default=DEFAULT_LINK_HOST, help="Link broker host.")
    parser.add_argument(
        "--link-base-port",
        type=int,
        default=DEFAULT_LINK_BASE_PORT,
        help="Base TCP port; LINK_A uses it, LINK_B uses base+1.",
    )
    parser.add_argument("--link-token", default=None, help="Optional broker handshake token.")
    parser.add_argument(
        "--auto-connect",
        action="store_true",
        help="Connect the preferred port automatically once the UI is ready.",
    )
    parser.add_argument(
        "--allow-multiple",
        action="store_true",
        help="Debug: bypass the single-instance lock.",
    )
    parser.add_argument(
        "--broker-only",
        action="store_true",
        help="Run only the in-app link broker (headless), no UI.",
    )
    parser.add_argument(
        "--window-pos",
        choices=WINDOW_POSITIONS,
        default="normal",
        help="Place the window on the left/right half for dual demo.",
    )
    parser.add_argument("--loss", type=float, default=0.0, help="Broker byte loss rate 0..1.")
    parser.add_argument(
        "--latency-ms",
        type=float,
        default=5.0,
        help="Broker per-chunk forwarding latency (ms).",
    )
    parser.add_argument("--los", action="store_true", help="Start link broker with LOS blocked.")
    args, _ = parser.parse_known_args(argv)

    instance: str | None = args.instance or os.environ.get("IR_INSTANCE") or None

    if args.link_role is not None:
        link_role: str = str(args.link_role)
    elif args.transport == "link":
        link_role = "auto"
    elif args.transport in ("serial", "mock"):
        link_role = "off"
    elif is_dual_mock_enabled():
        link_role = "auto"
    else:
        link_role = "off"

    if instance is None and link_role in ("host", "join", "auto"):
        instance = "B" if link_role == "join" else "A"

    return LaunchConfig(
        instance=instance,
        transport=str(args.transport),
        port=args.port,
        tx_port=args.tx_port,
        rx_port=args.rx_port,
        baud=int(args.baud),
        supported_baudrates=SUPPORTED_BAUDRATES,
        link_role=link_role,
        link_host=str(args.link_host),
        link_base_port=int(args.link_base_port),
        link_token=args.link_token,
        auto_connect=bool(args.auto_connect),
        allow_multiple=bool(args.allow_multiple),
        broker_only=bool(args.broker_only),
        window_pos=str(args.window_pos),
        loss_rate=float(args.loss),
        latency_ms=float(args.latency_ms),
        los_blocked=bool(args.los),
    )
