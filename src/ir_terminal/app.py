"""Điểm xuất phát khởi động toàn bộ ứng dụng phần mềm (Application Bootstrap & Wiring).

Đảm nhiệm việc ráp nối các mảnh ghép của hệ thống:
1. Đọc cấu hình dòng lệnh và xác định xem trạm đang chạy ở chế độ đơn lẻ hay chạy song song Node A / Node B.
2. Kiểm tra khóa file app.lock để chống mở đè hai lần trên cùng một cổng COM.
3. Nếu chạy chế độ thử nghiệm tia quang ảo (Host), tự động bật trạm trung chuyển LinkBroker.
4. Ráp nối các khối xử lý: Bộ nhớ StorageService, Bộ não SerialManager, Động cơ truyền file FileTransferService, Dịch vụ chat ChatService, Bộ đo TelemetryService và Cầu nối BackendBridge lên cửa sổ màn hình WebMainWindow.
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from ir_terminal import __version__
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

_HELD_LOCK: QLockFile | None = None
_HELD_BROKER: LinkBroker | None = None


def _ensure_qapp() -> QApplication:
    """Trả về đối tượng QApplication hiện có hoặc khởi tạo một tiến trình QApplication mới."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    app = QApplication(sys.argv)
    app.setApplicationName("IR-OWC Lab Terminal")
    app.setOrganizationName("LabHongNgoai")
    return app


def _build_serial_manager(config: LaunchConfig) -> SerialManager:
    """Khởi tạo SerialManager được kết nối với chế độ truyền tải (phần cứng hoặc socket link) đã chọn."""
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
    file_service: FileTransferService | None = None,
) -> ChatService:
    """Khởi tạo dịch vụ trò chuyện ChatService với tên trạm nhận diện tương ứng của instance."""
    if ctx.instance_id is not None:
        return ChatService(
            serial_manager=serial_manager,
            storage_service=storage_service,
            local_name=ctx.local_station,
            remote_name=ctx.remote_station,
            file_transfer_service=file_service,
        )
    return ChatService(
        serial_manager=serial_manager,
        storage_service=storage_service,
        file_transfer_service=file_service,
    )


def create_app(
    config: LaunchConfig | None = None,
    instance: str | None = None,
    *,
    acquire_lock: bool = False,
) -> tuple[QApplication, WebMainWindow]:
    """Khởi động toàn bộ các tầng phụ thuộc và tạo phiên cửa sổ ứng dụng Qt.

    Args:
        config: Cấu hình tham số khởi động đã phân tích (hoặc None để suy luận từ instance/môi trường).
        instance: Tên instance tùy chọn (A hoặc B), chỉ dùng khi không truyền config.
        acquire_lock: True để chiếm giữ khóa chống mở trùng tiến trình trên thư mục dữ liệu.

    Returns:
        Tuple gồm đối tượng (QApplication, WebMainWindow).

    Raises:
        InvalidInstanceIdError: Nếu định danh instance không hợp lệ.
        InstanceLockError: Nếu tiến trình khác đang chiếm giữ khóa của cùng một instance.
        OSError: Nếu Broker không thể gán cổng TCP khi chạy ở vai trò Host.
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

    logger, _ = setup_logger(log_dir=ctx.logs_dir)
    logger.info(
        "Initializing IR-OWC Lab Terminal v%s: instance=%s role=%s transport=%s port=%s "
        "link=%s:%d data=%s transfers=%s logs=%s",
        __version__,
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

    global _HELD_LOCK
    if acquire_lock:
        try:
            _HELD_LOCK = acquire_instance_lock(ctx.lock_path, allow_multiple=config.allow_multiple)
        except InstanceLockError as exc:
            logger.error("%s", exc)
            QMessageBox.warning(
                None,
                f"{ctx.display_name} is already running",
                f"{exc}\n\nClose the other window or use --allow-multiple.",
            )
            raise

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

    storage_service = StorageService(data_dir=ctx.data_dir)
    serial_manager = _build_serial_manager(config)
    file_service = FileTransferService(
        serial_manager=serial_manager,
        storage_service=storage_service,
        transfers_dir=ctx.transfers_dir,
    )
    chat_service = _build_chat_service(
        config, ctx, serial_manager, storage_service, file_service=file_service
    )
    telemetry_service = TelemetryService(storage_service=storage_service)

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
        auto_data = config.effective_tx_port or preferred_port
        auto_ctrl = config.effective_rx_port or ""

        def _auto_connect() -> None:
            logger.info(
                "Auto-connect: COM1=%s COM2=%s baud=%d node=%s",
                auto_data,
                auto_ctrl or "<none>",
                config.baud,
                ctx.display_name,
            )
            bridge.connectDualPorts(auto_data, auto_ctrl, config.baud)

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
    """Khởi chạy Broker liên kết quang ảo ở chế độ độc lập (không giao diện UI) cho đến khi bị ngắt."""
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
    """Điểm vào chính của ứng dụng IR-OWC Lab Terminal.

    Args:
        argv: Danh sách tham số dòng lệnh CLI tùy chọn (mặc định sys.argv[1:]).

    Returns:
        Mã thoát tiến trình.
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
