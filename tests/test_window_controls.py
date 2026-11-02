from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dsh_shell.api import ShellApi, _make_qt_action_bridge

CHROME_JS = Path(__file__).resolve().parents[1] / "ui" / "chrome.js"


class _FakeWindowHandle:
    def __init__(self) -> None:
        self.resize_edges: list[Any] = []
        self.resize_threads: list[str] = []
        self.move_threads: list[str] = []

    def startSystemResize(self, edges: Any) -> bool:  # noqa: N802
        self.resize_edges.append(edges)
        self.resize_threads.append(threading.current_thread().name)
        return True

    def startSystemMove(self) -> bool:  # noqa: N802
        self.move_threads.append(threading.current_thread().name)
        return True


class _FakeView:
    def __init__(self, handle: _FakeWindowHandle | None) -> None:
        self._handle = handle

    def windowHandle(self) -> _FakeWindowHandle | None:  # noqa: N802
        return self._handle


def _api_with_view(view: _FakeView | None) -> ShellApi:
    api = ShellApi(SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    api._main = SimpleNamespace(uid="main")
    api._qt_browser_view = lambda: view  # type: ignore[method-assign]
    return api


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ("n", 1),
        ("s", 8),
        ("w", 2),
        ("e", 4),
        ("nw", 2 | 1),
        ("ne", 4 | 1),
        ("sw", 2 | 8),
        ("se", 4 | 8),
    ],
)
def test_begin_system_resize_maps_all_edges_to_qt(direction: str, expected: int) -> None:
    handle = _FakeWindowHandle()
    api = _api_with_view(_FakeView(handle))

    assert api.begin_system_resize(direction) is True

    assert len(handle.resize_edges) == 1
    # Qt's flag values are stable: Top=1, Left=2, Right=4, Bottom=8.
    assert handle.resize_edges[0].value == expected


def test_begin_system_resize_rejects_empty_direction() -> None:
    handle = _FakeWindowHandle()
    api = _api_with_view(_FakeView(handle))

    assert api.begin_system_resize("") is False
    assert handle.resize_edges == []


def test_begin_system_move_uses_native_qt_move() -> None:
    handle = _FakeWindowHandle()
    api = _api_with_view(_FakeView(handle))

    assert api.begin_system_move() is True

    assert len(handle.move_threads) == 1


def test_native_window_controls_are_unavailable_without_qt_window() -> None:
    api = _api_with_view(None)

    assert api.begin_system_resize("se") is False
    assert api.begin_system_move() is False


def test_native_window_actions_are_marshalled_to_the_gui_thread() -> None:
    qtcore = pytest.importorskip("PySide6.QtCore")
    app = qtcore.QCoreApplication.instance() or qtcore.QCoreApplication([])
    handle = _FakeWindowHandle()
    api = _api_with_view(_FakeView(handle))
    api._qt_actions = _make_qt_action_bridge(api)
    results: list[bool] = []

    def call_from_worker() -> None:
        results.append(api.begin_system_resize("se"))
        results.append(api.begin_system_move())

    worker = threading.Thread(target=call_from_worker, name="pywebview-js-api")
    worker.start()
    qtcore.QTimer.singleShot(2000, app.quit)
    app.exec()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert results == [True, True]
    assert handle.resize_threads == [threading.main_thread().name]
    assert handle.move_threads == [threading.main_thread().name]


def test_chrome_uses_native_move_and_resize_with_pointer_capture_fallback() -> None:
    script = CHROME_JS.read_text(encoding="utf-8")

    assert 'safeCall("begin_system_resize", [direction])' in script
    assert 'safeCall("begin_system_move")' in script
    assert "setPointerCapture" in script
    assert "releasePointerCapture" in script
    assert "moveEvent.buttons & 1" in script
    assert "DRAG_THRESHOLD" in script


def test_resize_handles_stay_above_title_bar() -> None:
    script = CHROME_JS.read_text(encoding="utf-8")

    # Chromium clamps larger CSS z-index values to 2147483647.  If the title
    # bar shares that value and is appended later, it swallows clicks on the
    # top resize corners.
    assert "z-index: 2147483647; background: transparent" in script
    assert "z-index: 2147483646;" in script
    assert "2147483648" not in script
