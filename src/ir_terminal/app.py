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


def create_app() -> tuple[QApplication, WebMainWindow]:
    """Bootstraps and configures the Qt application instance.

    Returns:
        A tuple of (QApplication, WebMainWindow).
    """
    logger, _ = setup_logger()
    logger.info("Initializing IR-OWC Lab Terminal application (QWebEngine + QWebChannel)...")

    instance = QApplication.instance()
    if isinstance(instance, QApplication):
        app = instance
    else:
        app = QApplication(sys.argv)

    app.setApplicationName("IR-OWC Lab Terminal")
    app.setOrganizationName("LabHongNgoai")

    # Dependency Injection Container & Composition Root
    storage_service = StorageService()
    serial_manager = SerialManager()
    chat_service = ChatService(serial_manager=serial_manager, storage_service=storage_service)
    file_service = FileTransferService(
        serial_manager=serial_manager, storage_service=storage_service
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

    return app, main_window


def run() -> int:
    """Application main entry point.

    Returns:
        Exit code integer.
    """
    app, window = create_app()
    window.show()
    return app.exec()
