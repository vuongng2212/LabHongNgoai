"""QWebEngineView Main Window integrating full HTML/Tailwind SPA with Python Backend."""

from __future__ import annotations

import logging

from PySide6.QtCore import QUrl
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow, QVBoxLayout, QWidget

from ir_terminal.core.backend_bridge import BackendBridge
from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.utils.path_helpers import get_project_root

logger = logging.getLogger(__name__)


class WebAppPage(QWebEnginePage):
    """Custom WebEnginePage with enhanced logging for JS console."""

    def javaScriptConsoleMessage(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line_number: int,
        source_id: str,
    ) -> None:
        logger.debug("JS Console [%s:%d]: %s", source_id, line_number, message)


class WebMainWindow(QMainWindow):
    """Main Application Window hosting Chromium WebEngine and Python QWebChannel."""

    def __init__(self, serial_manager: SerialManager) -> None:
        super().__init__()
        self._serial_manager = serial_manager
        self._bridge = BackendBridge(self._serial_manager)

        self._init_window()
        self._init_webengine()

    def _init_window(self) -> None:
        self.setWindowTitle("SpectraLink IR-OWC Workstation")
        self.resize(1380, 880)
        self.setMinimumSize(1100, 700)

    def _init_webengine(self) -> None:
        central_widget = QWidget(self)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._view = QWebEngineView(self)
        self._page = WebAppPage(self._view)
        self._view.setPage(self._page)

        # Enable local storage and JS
        settings = self._view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)

        # Setup QWebChannel
        self._channel = QWebChannel(self._page)
        self._channel.registerObject("bridge", self._bridge)
        self._page.setWebChannel(self._channel)

        # Load HTML
        index_path = get_project_root() / "src" / "ir_terminal" / "ui" / "web" / "index.html"
        self._view.load(QUrl.fromLocalFile(str(index_path.resolve())))

        layout.addWidget(self._view)
        self.setCentralWidget(central_widget)

    @property
    def bridge(self) -> BackendBridge:
        return self._bridge

    @property
    def web_view(self) -> QWebEngineView:
        return self._view
