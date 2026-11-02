from __future__ import annotations

import os
import queue
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QIcon, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dsh_shell.ipc import SESSION_ACTIVITY  # noqa: E402
from dsh_shell.paths import APP_DISPLAY_NAME  # noqa: E402
from dsh_shell.tray import TrayHost, generate_activity_frames  # noqa: E402

ACTIVE_TOOLTIP = "dsh-shell · 会话运行中"


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class RecordingTray:
    def __init__(self) -> None:
        self.icon: QIcon | None = None
        self.tooltip = ""

    def setIcon(self, icon: QIcon) -> None:  # noqa: N802
        self.icon = icon

    def setToolTip(self, tooltip: str) -> None:  # noqa: N802
        self.tooltip = tooltip


class RecordingTimer:
    def __init__(self) -> None:
        self.running = False
        self.start_count = 0
        self.stop_count = 0

    def start(self) -> None:
        self.running = True
        self.start_count += 1

    def stop(self) -> None:
        self.running = False
        self.stop_count += 1


def make_host(events: Any) -> tuple[TrayHost, RecordingTray, RecordingTimer]:
    host = TrayHost(lambda command: None, on_quit=lambda: None, activity_events=events)
    tray = RecordingTray()
    timer = RecordingTimer()
    host._qt_tray = tray
    host._activity_timer = timer
    return host, tray, timer


def test_activity_events_switch_icon_tooltip_and_animation(qt_app: QApplication) -> None:
    events: queue.Queue[tuple[str, bool]] = queue.Queue()
    host, tray, timer = make_host(events)

    host._set_session_active(False)
    idle_icon_key = tray.icon.cacheKey()
    assert tray.tooltip == APP_DISPLAY_NAME
    assert timer.running is False

    events.put((SESSION_ACTIVITY, True))
    host._poll_activity()

    assert tray.tooltip == ACTIVE_TOOLTIP
    assert timer.running is True
    assert tray.icon is not None
    assert tray.icon.cacheKey() != idle_icon_key

    animated_icon_key = tray.icon.cacheKey()
    host._advance_activity_frame()
    assert tray.icon is not None
    assert tray.icon.cacheKey() != animated_icon_key

    events.put((SESSION_ACTIVITY, False))
    host._poll_activity()

    assert tray.tooltip == APP_DISPLAY_NAME
    assert timer.running is False
    assert tray.icon is not None
    assert tray.icon.cacheKey() == idle_icon_key


def test_generate_activity_frames_has_multiple_distinct_frames(qt_app: QApplication) -> None:
    frames = generate_activity_frames()

    assert 10 <= len(frames) <= 12
    assert all(not frame.isNull() for frame in frames)

    images = [
        frame.pixmap(64, 64).toImage().convertToFormat(QImage.Format.Format_RGBA8888) for frame in frames
    ]
    assert len({bytes(image.bits()) for image in images}) == len(frames)


def test_stop_stops_activity_timer(qt_app: QApplication) -> None:
    host, _tray, timer = make_host(queue.Queue())
    timer.running = True

    host.stop()

    assert timer.running is False


def test_invalid_and_unrelated_events_are_ignored(qt_app: QApplication) -> None:
    events: queue.Queue[Any] = queue.Queue()
    host, tray, timer = make_host(events)
    host._set_session_active(False)
    idle_icon_key = tray.icon.cacheKey()

    events.put("not-a-tuple")
    events.put(("some_other_event", True))
    host._poll_activity()

    assert tray.tooltip == APP_DISPLAY_NAME
    assert timer.running is False
    assert tray.icon is not None
    assert tray.icon.cacheKey() == idle_icon_key
