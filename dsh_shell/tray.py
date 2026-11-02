"""System tray menu for the shell host process.

The tray uses Qt's :class:`QSystemTrayIcon` on every platform, so the icon file
is handed to Qt directly and never decoded in Python.  On Linux Qt speaks the
StatusNotifierItem / DBusMenu protocol, which is what GNOME's AppIndicators
extension renders and which Wayland desktops host (unlike XEmbed).
"""

from __future__ import annotations

import math
import queue
import threading
from collections.abc import Callable
from typing import Any

from loguru import logger

from .ipc import (
    OPEN_EXTERNAL,
    OPEN_SETTINGS,
    QUIT,
    RELOAD,
    RESTART_DSH,
    SESSION_ACTIVITY,
    TOGGLE_MAIN,
)
from .paths import APP_DISPLAY_NAME, icon_path

ACTIVE_TOOLTIP = f"{APP_DISPLAY_NAME} · 会话运行中"
ACTIVITY_FRAME_COUNT = 12
ACTIVITY_FRAME_INTERVAL_MS = 120
ACTIVITY_POLL_INTERVAL_MS = 80


def generate_activity_frames(frame_count: int = ACTIVITY_FRAME_COUNT) -> list[Any]:
    """Render rotating/pulsing activity-ring frames over the static tray icon."""
    if frame_count < 1:
        raise ValueError("frame_count must be positive")

    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

    base = QPixmap(str(icon_path()))
    if base.isNull():
        return []

    size = min(base.width(), base.height())
    if size <= 0:
        return []

    ring_width = max(2.0, size * 0.065)
    inset = ring_width / 2 + max(1.0, size * 0.015)
    ring_rect = QRectF(inset, inset, size - inset * 2, size - inset * 2)
    track_color = QColor(75, 107, 245, 95)
    accent_color = QColor(75, 107, 245, 255)

    frames: list[Any] = []
    for frame_index in range(frame_count):
        canvas = base.copy()
        painter = QPainter(canvas)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(
                QPen(
                    track_color,
                    ring_width,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            painter.drawEllipse(ring_rect)

            phase = frame_index / frame_count
            pulse = (math.sin(phase * math.tau) + 1.0) / 2.0
            sweep = 92.0 + pulse * 34.0
            start_angle = int((90.0 - frame_index * 360.0 / frame_count) * 16)
            painter.setPen(
                QPen(
                    accent_color,
                    ring_width * (0.78 + pulse * 0.22),
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            painter.drawArc(ring_rect, start_angle, int(-sweep * 16))
        finally:
            painter.end()
        frames.append(QIcon(canvas))
    return frames


class TrayHost:
    """Owns the tray icon; commands are forwarded to the webview child."""

    def __init__(
        self,
        send: Callable[[str], None],
        *,
        on_quit: Callable[[], None],
        ensure_child: Callable[[], None] | None = None,
        activity_events: Any = None,
    ) -> None:
        self._send = send
        self._on_quit = on_quit
        self._ensure_child = ensure_child
        self._activity_events = activity_events
        self._qt_app: Any = None
        self._qt_tray: Any = None
        self._qt_menu: Any = None
        self._quit_bridge: Any = None
        self._activity_timer: Any = None
        self._activity_poll_timer: Any = None
        self._activity_frames: list[Any] | None = None
        self._activity_frame_index = 0
        self._static_icon: Any = None
        self._session_active = False
        self._lock = threading.Lock()
        self._quit_requested = False

    def _command(self, command: str) -> None:
        if self._ensure_child is not None:
            try:
                self._ensure_child()
            except Exception as error:
                logger.warning("Could not restart webview child: {}", error)
        self._send(command)

    def _toggle(self, icon: Any = None, item: Any = None) -> None:
        self._command(TOGGLE_MAIN)

    def _settings(self, icon: Any = None, item: Any = None) -> None:
        self._command(OPEN_SETTINGS)

    def _reload(self, icon: Any = None, item: Any = None) -> None:
        self._command(RELOAD)

    def _external(self, icon: Any = None, item: Any = None) -> None:
        self._command(OPEN_EXTERNAL)

    def _restart_dsh(self, icon: Any = None, item: Any = None) -> None:
        self._command(RESTART_DSH)

    def _quit(self, icon: Any = None, item: Any = None) -> None:
        with self._lock:
            if self._quit_requested:
                return
            self._quit_requested = True
        try:
            self._send(QUIT)
        except Exception as error:
            logger.warning("Could not send quit command: {}", error)
        try:
            self._on_quit()
        finally:
            self.stop()

    def _static_qicon(self) -> Any:
        if self._static_icon is None:
            from PySide6.QtGui import QIcon

            self._static_icon = QIcon(str(icon_path()))
        return self._static_icon

    def _ensure_activity_frames(self) -> list[Any]:
        if self._activity_frames is None:
            try:
                self._activity_frames = generate_activity_frames()
            except Exception as error:
                logger.warning("Could not render tray activity animation: {}", error)
                self._activity_frames = []
        return self._activity_frames

    def _set_session_active(self, active: bool) -> None:
        """Apply the session-running state; called by the Qt-thread event poller."""
        active = bool(active)
        if active and self._session_active:
            return
        self._session_active = active

        tray = self._qt_tray
        if active:
            frames = self._ensure_activity_frames()
            self._activity_frame_index = 0
            if tray is not None and frames:
                tray.setIcon(frames[0])
                tray.setToolTip(ACTIVE_TOOLTIP)
            elif tray is not None:
                tray.setToolTip(ACTIVE_TOOLTIP)
            timer = self._activity_timer
            if timer is not None and frames:
                timer.start()
            return

        timer = self._activity_timer
        if timer is not None:
            timer.stop()
        self._activity_frame_index = 0
        if tray is not None:
            tray.setIcon(self._static_qicon())
            tray.setToolTip(APP_DISPLAY_NAME)

    def _advance_activity_frame(self) -> None:
        if not self._session_active:
            return
        frames = self._activity_frames
        tray = self._qt_tray
        if not frames or tray is None:
            return
        self._activity_frame_index = (self._activity_frame_index + 1) % len(frames)
        tray.setIcon(frames[self._activity_frame_index])

    def _poll_activity(self) -> None:
        """Drain session activity events; must run on the Qt main thread."""
        events = self._activity_events
        if events is None:
            return
        while True:
            try:
                event = events.get_nowait()
            except queue.Empty:
                return
            except (EOFError, OSError, ValueError) as error:
                logger.debug("Tray activity queue unavailable: {}", error)
                return
            if not isinstance(event, tuple) or len(event) < 2:
                continue
            kind, value = event[0], event[1]
            if kind == SESSION_ACTIVITY:
                self._set_session_active(bool(value))

    def _stop_activity_timers(self) -> None:
        for timer in (self._activity_poll_timer, self._activity_timer):
            if timer is None:
                continue
            try:
                timer.stop()
            except Exception as error:
                logger.debug("Qt tray timer stop failed: {}", error)

    def stop(self) -> None:
        bridge = self._quit_bridge
        if bridge is None:
            self._stop_activity_timers()
            return
        try:
            # Queued signal: stops timers in the Qt thread and exits its loop.
            bridge.requested.emit()
        except Exception as error:
            logger.debug("Qt tray stop failed: {}", error)
            self._stop_activity_timers()

    def notify(self, message: str, title: str = APP_DISPLAY_NAME) -> None:
        tray = self._qt_tray
        if tray is None:
            return
        try:
            tray.showMessage(title, message)
        except Exception as error:
            logger.debug("Tray notification unavailable: {}", error)

    # --------------------------------------------------------------------- Qt
    def _run_qt(self) -> bool:
        try:
            from PySide6.QtCore import QObject, Qt, QTimer, Signal
            from PySide6.QtGui import QIcon
            from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
        except Exception as error:
            logger.warning("Qt tray unavailable: {}", error)
            return False

        app = QApplication.instance() or QApplication([])
        app.setApplicationName("dsh-shell")
        app.setDesktopFileName("dsh-shell")
        app.setQuitOnLastWindowClosed(False)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("System tray is unavailable; close buttons will quit instead of hiding")
            return False

        class _QuitBridge(QObject):
            requested = Signal()

        self._static_icon = QIcon(str(icon_path()))
        tray = QSystemTrayIcon(self._static_icon, app)
        tray.setToolTip(APP_DISPLAY_NAME)
        menu = QMenu()
        for label, handler in (
            ("显示/隐藏", self._toggle),
            ("MCP 与 Skills 设置…", self._settings),
            ("重新加载页面", self._reload),
            ("在浏览器中打开", self._external),
        ):
            action = menu.addAction(label)
            action.triggered.connect(handler)
        menu.addSeparator()
        for label, handler in (
            ("重启 DSH 服务", self._restart_dsh),
            ("退出", self._quit),
        ):
            action = menu.addAction(label)
            action.triggered.connect(handler)
        tray.setContextMenu(menu)

        def on_activated(reason: Any) -> None:
            if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
                self._toggle()

        tray.activated.connect(on_activated)

        activity_timer = QTimer()
        activity_timer.setInterval(ACTIVITY_FRAME_INTERVAL_MS)
        activity_timer.timeout.connect(self._advance_activity_frame)

        activity_poll_timer = QTimer()
        activity_poll_timer.setInterval(ACTIVITY_POLL_INTERVAL_MS)
        activity_poll_timer.timeout.connect(self._poll_activity)
        activity_poll_timer.start()

        bridge = _QuitBridge()

        def request_shutdown() -> None:
            self._stop_activity_timers()
            app.quit()

        bridge.requested.connect(request_shutdown, Qt.ConnectionType.QueuedConnection)

        self._qt_app = app
        self._qt_tray = tray
        self._qt_menu = menu
        self._quit_bridge = bridge
        self._activity_timer = activity_timer
        self._activity_poll_timer = activity_poll_timer
        tray.show()
        logger.info("System tray icon started (Qt)")
        try:
            app.exec()
            return True
        except Exception as error:
            logger.warning("Qt tray loop failed: {}", error)
            return False
        finally:
            self._stop_activity_timers()
            self._activity_timer = None
            self._activity_poll_timer = None
            self._quit_bridge = None
            self._qt_tray = None
            self._qt_app = None
            try:
                tray.hide()
                menu.deleteLater()
            except Exception:
                pass
            self._qt_menu = None

    def run(self) -> bool:
        """Run the tray loop; returns False when the desktop has no tray support."""
        return self._run_qt()
