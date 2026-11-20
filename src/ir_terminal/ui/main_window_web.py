"""Cửa sổ ứng dụng chính tích hợp QWebEngineView hiển thị giao diện Web SPA với lõi Python.

Khởi tạo môi trường Chromium nhúng, đăng ký đối tượng cầu nối BackendBridge thông qua QWebChannel,
nạp trang chủ index.html và quản lý vòng đời ứng dụng giao diện (thu phóng, đóng cửa sổ, giải phóng luồng).
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QUrl
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow, QVBoxLayout, QWidget

from ir_terminal.config import InstanceContext
from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.ui.backend_bridge import BackendBridge
from ir_terminal.utils.logger import LogTag
from ir_terminal.utils.path_helpers import get_web_index_path

logger = logging.getLogger(__name__)


class WebAppPage(QWebEnginePage):
    """Trang WebEngine mở rộng hỗ trợ chuyển tiếp thông báo log console từ JavaScript sang Python logger."""

    def javaScriptConsoleMessage(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line_number: int,
        source_id: str,
    ) -> None:
        """Chuyển tiếp đầu ra console của JavaScript sang hệ thống logging chuẩn của Python.

        Args:
            level: Cấp độ cảnh báo lỗi của console.
            message: Nội dung văn bản thông điệp log.
            line_number: Số dòng phát sinh log trong mã nguồn JavaScript.
            source_id: Tên hoặc URI của tệp tin nguồn JavaScript.
        """
        text = f"JS Console [{source_id}:{line_number}]: {message}"
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            logger.error("%s %s", LogTag.IPC_BRIDGE, text)
        elif level == QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel:
            logger.warning("%s %s", LogTag.IPC_BRIDGE, text)
        else:
            logger.debug("%s %s", LogTag.IPC_BRIDGE, text)


class WebMainWindow(QMainWindow):
    """Cửa sổ ứng dụng chính chứa trình duyệt Chromium WebEngine và cầu nối QWebChannel."""

    def __init__(
        self,
        serial_manager: SerialManager,
        bridge: BackendBridge | None = None,
        instance_context: InstanceContext | None = None,
    ) -> None:
        """Khởi tạo cửa sổ ứng dụng Web.

        Args:
            serial_manager: Bộ quản lý kết nối nối tiếp điều phối luồng worker I/O.
            bridge: Cầu nối BackendBridge tùy chọn đã được khởi tạo.
            instance_context: Ngữ cảnh thông tin định danh và đường dẫn bộ nhớ đệm riêng cho từng node.
        """
        super().__init__()
        self._serial_manager = serial_manager
        self._instance_context = instance_context
        self._profile: QWebEngineProfile | None = None
        if bridge is None:
            logger.warning(
                "%s WebMainWindow without injected bridge; creating default (port=%s connected=%s)",
                LogTag.IPC_BRIDGE,
                serial_manager.current_port,
                serial_manager.is_connected,
            )
        self._bridge = bridge or BackendBridge(self._serial_manager)

        self._init_window()
        self._init_webengine()
        logger.info(
            "%s WebMainWindow ready: 1380x880 bridge=%s node=%s",
            LogTag.IPC_BRIDGE,
            type(self._bridge).__name__,
            instance_context.display_name if instance_context else "single",
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        """Đảm bảo dừng an toàn luồng worker thread và các bộ định thời khi đóng cửa sổ."""
        logger.info(
            "%s WebMainWindow close requested; shutting down bridge+serial", LogTag.IPC_BRIDGE
        )
        try:
            self._bridge.shutdown()
        except Exception as exc:
            logger.warning(
                "%s Bridge shutdown on close FAILED (%s: %s)",
                LogTag.IPC_BRIDGE,
                type(exc).__name__,
                exc,
            )
        try:
            self._serial_manager.shutdown()
        except Exception as exc:
            logger.warning(
                "%s Serial shutdown on close FAILED (%s: %s)",
                LogTag.HW_SERIAL,
                type(exc).__name__,
                exc,
            )
        super().closeEvent(event)

    def _init_window(self) -> None:
        """Cấu hình các thuộc tính hình học của cửa sổ, giới hạn kích thước tối thiểu và tiêu đề."""
        title = (
            self._instance_context.window_title
            if self._instance_context is not None
            else "SpectraLink IR-OWC Workstation"
        )
        self.setWindowTitle(title)
        self.resize(1380, 880)
        self.setMinimumSize(720, 600)

    def _create_page(self) -> WebAppPage:
        """Khởi tạo trang WebEngine và cô lập profile lưu trữ/cookies riêng biệt cho từng tiến trình."""
        ctx = self._instance_context
        if ctx is None:
            return WebAppPage(self._view)
        profile = QWebEngineProfile(ctx.profile_key, self)
        profile.setPersistentStoragePath(str(ctx.webengine_storage_dir))
        profile.setCachePath(str(ctx.webengine_cache_dir))
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        self._profile = profile
        logger.info(
            "%s Per-instance WebEngine profile: name=%s cache=%s storage=%s",
            LogTag.IPC_BRIDGE,
            ctx.profile_key,
            ctx.webengine_cache_dir,
            ctx.webengine_storage_dir,
        )
        return WebAppPage(profile, self._view)

    def _init_webengine(self) -> None:
        """Khởi tạo thành phần hiển thị QWebEngineView, đăng ký cầu nối QWebChannel và tải trang index.html."""
        central_widget = QWidget(self)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._view = QWebEngineView(self)
        self._page = self._create_page()
        self._view.setPage(self._page)

        settings = self._view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)

        self._channel = QWebChannel(self._page)
        self._channel.registerObject("bridge", self._bridge)
        self._page.setWebChannel(self._channel)

        self._bridge.closeWindowRequested.connect(self.close)
        self._bridge.minimizeWindowRequested.connect(self.showMinimized)
        self._bridge.maximizeWindowRequested.connect(self._toggle_maximize)

        try:
            index_path = get_web_index_path()
        except FileNotFoundError as exc:
            logger.error("Web UI index.html not found: %s", exc)
            raise
        self._view.load(QUrl.fromLocalFile(str(index_path.resolve())))

        layout.addWidget(self._view)
        self.setCentralWidget(central_widget)

    def _toggle_maximize(self) -> None:
        """Chuyển đổi trạng thái giữa phóng to toàn màn hình và kích thước cửa sổ thông thường."""
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def apply_window_position(self, position: str) -> None:
        """Tự động định vị cửa sổ ứng dụng ở nửa trái hoặc nửa phải màn hình chính.

        Hỗ trợ hiển thị song song Node A và Node B cạnh nhau khi khởi chạy kiểm thử hai node.

        Args:
            position: 'left' (nửa trái), 'right' (nửa phải), hoặc giá trị khác để giữ nguyên.
        """
        if position not in ("left", "right"):
            return
        screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        half_w = max(720, geo.width() // 2)
        self.setMinimumSize(min(self.minimumWidth(), half_w), 600)
        self.resize(half_w, geo.height())
        x = geo.x() if position == "left" else geo.x() + geo.width() - half_w
        self.move(x, geo.y())

    @property
    def instance_context(self) -> InstanceContext | None:
        """Ngữ cảnh của phiên tiến trình hiện hành hoặc None nếu ở chế độ đơn lẻ."""
        return self._instance_context

    @property
    def bridge(self) -> BackendBridge:
        """Đối tượng cầu nối BackendBridge kết nối với JavaScript."""
        return self._bridge

    @property
    def web_view(self) -> QWebEngineView:
        """Thành phần điều khiển QWebEngineView bên trong."""
        return self._view
