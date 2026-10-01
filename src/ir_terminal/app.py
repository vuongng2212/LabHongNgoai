"""Application bootstrap, stylesheet loading, and dependency wiring."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ir_terminal.core.serial_manager import SerialManager
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

    # Dependency Injection Container
    serial_manager = SerialManager()
    main_window = WebMainWindow(serial_manager)

    return app, main_window


def run() -> int:
    """Application main entry point.

    Returns:
        Exit code integer.
    """
    app, window = create_app()
    window.show()
    return app.exec()
