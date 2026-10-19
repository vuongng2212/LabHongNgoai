"""Application bootstrap, stylesheet loading, and dependency wiring."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from ir_terminal.config import (
    LINK_CONNECT_ATTEMPT_TIMEOUT_SEC,
    LINK_CONNECT_RETRIES,
    LINK_CONNECT_RETRY_INTERVAL_SEC,
    InstanceContext,
    LaunchConfig,
    build_instance_context,
    parse_launch_args,
)
from ir_terminal.core.link_broker import LinkBroker
from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.services.chat_service import ChatService
from ir_terminal.services.file_transfer_service import FileTransferService
from ir_terminal.services.storage_service import StorageService
from ir_terminal.services.telemetry_service import TelemetryService
from ir_terminal.ui.backend_bridge import BackendBridge
from ir_terminal.ui.main_window_web import WebMainWindow
from ir_terminal.utils.instance_lock import InstanceLockError, acquire_instance_lock
from ir_terminal.utils.logger import setup_logger
from ir_terminal.utils.path_helpers import (
    InvalidInstanceIdError,
    is_dual_mock_enabled,
)

# Keep process-lifetime references: QLockFile must not be garbage-collected
# (which would release the lock), and the host broker owns relay threads.
_HELD_LOCK: QLockFile | None = None
_HELD_BROKER: LinkBroker | None = None


def _ensure_qapp() -> QApplication:
    """Returns the existing QApplication or creates the process one."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    app = QApplication(sys.argv)
    app.setApplicationName("IR-OWC Lab Terminal")
    app.setOrganizationName("LabHongNgoai")
    return app


def _build_serial_manager(config: LaunchConfig) -> SerialManager:
    """Builds a SerialManager wired for the selected transport/link mode."""
    return SerialManager(
        link_endpoints=config.link_endpoints if config.link_active else None,
        include_link=config.link_active,
        link_connect_retries=LINK_CONNECT_RETRIES if config.link_active else 0,
        link_retry_interval_sec=LINK_CONNECT_RETRY_INTERVAL_SEC,
        link_connect_timeout_sec=LINK_CONNECT_ATTEMPT_TIMEOUT_SEC,
        link_token=config.link_token,
    )


def _build_chat_service(
    config: LaunchConfig,
    ctx: InstanceContext,
    serial_manager: SerialManager,
    storage_service: StorageService,
) -> ChatService:
    """Builds the chat service with instance-aware station names."""
    if ctx.instance_id is not None:
        return ChatService(
            serial_manager=serial_manager,
            storage_service=storage_service,
            local_name=ctx.local_station,
            remote_name=ctx.remote_station,
        )
    return ChatService(serial_manager=serial_manager, storage_service=storage_service)


def create_app(
    config: LaunchConfig | None = None,
    instance: str | None = None,
    *,
    acquire_lock: bool = False,
) -> tuple[QApplication, WebMainWindow]:
    """Bootstraps and configures the Qt application instance.

    Args:
        config: Parsed launch configuration. When omitted, a minimal config is
            built from ``instance`` / env (backward-compatible callers).
        instance: Optional dual-mock instance name (A/B), used only when
            ``config`` is not provided.
        acquire_lock: When True, enforce one process per instance data dir.

    Returns:
        A tuple of (QApplication, WebMainWindow).

    Raises:
        InvalidInstanceIdError: If the instance id is not a safe label.
        InstanceLockError: If another process holds the same instance lock.
        OSError: If the host link broker cannot bind its TCP ports.
    """
    if config is None:
        config = LaunchConfig(
            instance=instance or os.environ.get("IR_INSTANCE"),
            link_role="auto" if is_dual_mock_enabled() else "off",
        )

    qt_app = _ensure_qapp()
    try:
        ctx = build_instance_context(config)
    except InvalidInstanceIdError as exc:
        QMessageBox.critical(None, "Invalid instance id", str(exc))
        raise

    # Logging is configured with the instance log dir BEFORE any handler opens
    # a file, so two nodes never share data/logs/app.log.
    logger, _ = setup_logger(log_dir=ctx.logs_dir)
    logger.info(
        "Initializing IR-OWC Lab Terminal: instance=%s role=%s transport=%s port=%s "
        "link=%s:%d data=%s transfers=%s logs=%s",
        ctx.instance_id or "single",
        config.effective_link_role,
        config.transport,
        config.preferred_port or "<manual>",
        config.link_host,
        config.link_base_port,
        ctx.data_dir,
        ctx.transfers_dir,
        ctx.logs_dir,
    )

    # One process per instance data dir (A and B have different lock files).
    global _HELD_LOCK
    if acquire_lock:
        try:
            _HELD_LOCK = acquire_instance_lock(
                ctx.lock_path, allow_multiple=config.allow_multiple
            )
        except InstanceLockError as exc:
            logger.error("%s", exc)
            QMessageBox.warning(
                None,
                f"{ctx.display_name} is already running",
                f"{exc}\n\nClose the other window or use --allow-multiple.",
            )
            raise

    # Host node embeds the link broker so no external Python process is needed.
    global _HELD_BROKER
    if config.link_active and config.effective_link_role == "host":
        broker = LinkBroker(
            port_a=config.link_base_port,
            port_b=config.link_base_port + 1,
            host=config.link_host,
            loss_rate=config.loss_rate,
            latency_ms=config.latency_ms,
            token=config.link_token,
        )
        try:
            broker.start()
        except OSError as exc:
            logger.exception(
                "In-app link broker failed to bind %s:%d/%d: %s",
                config.link_host,
                config.link_base_port,
                config.link_base_port + 1,
                exc,
            )
            QMessageBox.critical(
                None,
                "Link broker unavailable",
                f"Could not open TCP ports {config.link_base_port}/"
                f"{config.link_base_port + 1} on {config.link_host}.\n\n{exc}\n\n"
                "Close the other app using these ports or change --link-base-port.",
            )
            raise
        _HELD_BROKER = broker
        if config.los_blocked:
            broker.set_los_blocked(True)
        qt_app.aboutToQuit.connect(broker.stop)
        logger.info(
            "In-app link broker started (host): A=%s:%d B=%s:%d",
            config.link_host,
            broker.port_a,
            config.link_host,
            broker.port_b,
        )

    # Dependency Injection Container & Composition Root
    storage_service = StorageService(data_dir=ctx.data_dir)
    serial_manager = _build_serial_manager(config)
    chat_service = _build_chat_service(config, ctx, serial_manager, storage_service)
    file_service = FileTransferService(
        serial_manager=serial_manager,
        storage_service=storage_service,
        transfers_dir=ctx.transfers_dir,
    )
    telemetry_service = TelemetryService(storage_service=storage_service)

    # Restore persisted transfer settings (max size / receive dir); defaults
    # apply on first run when no settings.json keys exist yet.
    file_service.load_settings()

    bridge = BackendBridge(
        serial_manager=serial_manager,
        chat_service=chat_service,
        file_service=file_service,
        telemetry_service=telemetry_service,
        storage_service=storage_service,
        launch_config=config,
        instance_context=ctx,
    )
    main_window = WebMainWindow(
        serial_manager=serial_manager,
        bridge=bridge,
        instance_context=ctx,
    )

    preferred_port = config.preferred_port
    if config.auto_connect and preferred_port is not None:
        auto_port = preferred_port

        def _auto_connect() -> None:
            logger.info(
                "Auto-connect: port=%s baud=%d node=%s",
                auto_port,
                config.baud,
                ctx.display_name,
            )
            bridge.connectPort(auto_port, config.baud)

        QTimer.singleShot(700, _auto_connect)

    logger.info(
        "DI wiring complete: Storage=%s SerialManager=%s Chat=%s File(state=%s) "
        "Telemetry(sent=%d) Bridge=%s Window=%s",
        type(storage_service).__name__,
        type(serial_manager).__name__,
        type(chat_service).__name__,
        file_service.state.name,
        telemetry_service.total_packets_sent,
        type(bridge).__name__,
        type(main_window).__name__,
    )

    return qt_app, main_window


def _run_broker_only(config: LaunchConfig) -> int:
    """Runs only the in-app link broker (headless) until interrupted."""
    ctx = build_instance_context(config)
    logger, _ = setup_logger(log_dir=ctx.logs_dir)
    broker = LinkBroker(
        port_a=config.link_base_port,
        port_b=config.link_base_port + 1,
        host=config.link_host,
        loss_rate=config.loss_rate,
        latency_ms=config.latency_ms,
        token=config.link_token,
    )
    if config.los_blocked:
        broker.set_los_blocked(True)
    logger.info(
        "Broker-only mode: A=%s:%d B=%s:%d (Ctrl+C to stop)",
        config.link_host,
        config.link_base_port,
        config.link_host,
        config.link_base_port + 1,
    )
    try:
        broker.run()
    except OSError as exc:
        logger.exception("Broker-only failed to bind: %s", exc)
        return 3
    return 0


def run(argv: list[str] | None = None) -> int:
    """Application main entry point.

    Examples:
        ``IR_OWC_Lab_Terminal.exe --instance A --link-role host --auto-connect``
        ``IR_OWC_Lab_Terminal.exe --instance B --link-role join --auto-connect``
        ``IR_OWC_Lab_Terminal.exe --instance A --port COM11 --auto-connect``

    Args:
        argv: Optional CLI argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Exit code integer.
    """
    config = parse_launch_args(argv)
    if config.broker_only:
        try:
            return _run_broker_only(config)
        except (InvalidInstanceIdError, OSError) as exc:
            print(f"IR-OWC broker: {exc}", file=sys.stderr)
            return 2

    try:
        app, window = create_app(config, acquire_lock=True)
    except (InvalidInstanceIdError, InstanceLockError) as exc:
        print(f"IR-OWC: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"IR-OWC: failed to start link broker: {exc}", file=sys.stderr)
        return 3

    window.show()
    window.apply_window_position(config.window_pos)
    return app.exec()
