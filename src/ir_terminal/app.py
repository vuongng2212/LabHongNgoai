"""Application bootstrap, stylesheet loading, and dependency wiring."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.services.chat_service import ChatService
from ir_terminal.services.file_transfer_service import FileTransferService
from ir_terminal.services.storage_service import StorageService
from ir_terminal.services.telemetry_service import TelemetryService
from ir_terminal.ui.backend_bridge import BackendBridge
from ir_terminal.ui.main_window_web import WebMainWindow
from ir_terminal.utils.logger import setup_logger
from ir_terminal.utils.path_helpers import get_data_dir, get_instance_name, get_transfers_dir


def create_app(instance: str | None = None) -> tuple[QApplication, WebMainWindow]:
    """Bootstraps and configures the Qt application instance.

    Args:
        instance: Optional dual-mock instance name (A/B). When set, JSON
            stores, transfers, and logs are isolated under
            ~/.ir_terminal/instances/<A|B>/ so two apps on one PC never
            share state. None keeps the legacy single-app ~/.ir_terminal/.

    Returns:
        A tuple of (QApplication, WebMainWindow).
    """
    logger, _ = setup_logger()
    inst = get_instance_name(instance)
    logger.info(
        "Initializing IR-OWC Lab Terminal application (QWebEngine + QWebChannel)... instance=%s",
        inst or "single",
    )

    qt_instance = QApplication.instance()
    if isinstance(qt_instance, QApplication):
        qt_app = qt_instance
    else:
        qt_app = QApplication(sys.argv)

    qt_app.setApplicationName("IR-OWC Lab Terminal")
    qt_app.setOrganizationName("LabHongNgoai")

    # Dependency Injection Container & Composition Root
    storage_service = StorageService(data_dir=get_data_dir(inst))
    serial_manager = SerialManager()
    chat_service = ChatService(serial_manager=serial_manager, storage_service=storage_service)
    file_service = FileTransferService(
        serial_manager=serial_manager,
        storage_service=storage_service,
        transfers_dir=get_transfers_dir(inst),
    )
    telemetry_service = TelemetryService(storage_service=storage_service)

    bridge = BackendBridge(
        serial_manager=serial_manager,
        chat_service=chat_service,
        file_service=file_service,
        telemetry_service=telemetry_service,
        storage_service=storage_service,
    )
    main_window = WebMainWindow(serial_manager=serial_manager, bridge=bridge)

    return qt_app, main_window


def run() -> int:
    """Application main entry point.

    Supports ``--instance A|B`` for dual-mock mode (2 apps on 1 PC with
    isolated data dirs). Production single-app usage is unchanged.

    Returns:
        Exit code integer.
    """
    import argparse
    import os

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--instance", default=None)
    args, _ = parser.parse_known_args()
    instance = args.instance or os.environ.get("IR_INSTANCE")
    app, window = create_app(instance=instance)
    window.show()
    return app.exec()
