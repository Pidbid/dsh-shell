from __future__ import annotations

import os
import threading
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from dsh_shell import webview_app
from dsh_shell.webview_app import install_clipboard_permission


class _Signal:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def connect(self, handler: Any) -> None:
        self.handlers.append(handler)


class _FakePage:
    def __init__(self) -> None:
        self.featurePermissionRequested = _Signal()
        self.permissions: list[tuple[Any, Any, Any]] = []

    def setFeaturePermission(self, url: Any, feature: Any, policy: Any) -> None:  # noqa: N802
        self.permissions.append((url, feature, policy))


class _FakeWindow:
    def __init__(self, page: _FakePage | None) -> None:
        self.native = SimpleNamespace(webview=SimpleNamespace(page=lambda: page))


def test_install_clipboard_permission_grants_only_clipboard_writes() -> None:
    qtwebengine = pytest.importorskip("PySide6.QtWebEngineCore")
    page = _FakePage()
    window = _FakeWindow(page)

    assert install_clipboard_permission(window) is True
    assert len(page.featurePermissionRequested.handlers) == 1

    handler = page.featurePermissionRequested.handlers[0]
    url = object()
    handler(url, qtwebengine.QWebEnginePage.Feature.ClipboardReadWrite)
    assert page.permissions == [
        (
            url,
            qtwebengine.QWebEnginePage.Feature.ClipboardReadWrite,
            qtwebengine.QWebEnginePage.PermissionPolicy.PermissionGrantedByUser,
        )
    ]

    handler(url, qtwebengine.QWebEnginePage.Feature.Geolocation)
    assert len(page.permissions) == 1


def test_install_clipboard_permission_is_idempotent() -> None:
    pytest.importorskip("PySide6.QtWebEngineCore")
    page = _FakePage()
    window = _FakeWindow(page)

    assert install_clipboard_permission(window) is True
    assert install_clipboard_permission(window) is True
    assert len(page.featurePermissionRequested.handlers) == 1


def test_install_clipboard_permission_without_qt_page_is_a_noop() -> None:
    assert install_clipboard_permission(_FakeWindow(None)) is False
    assert install_clipboard_permission(SimpleNamespace()) is False


def test_clipboard_installer_is_dispatched_to_the_qt_gui_thread(monkeypatch: Any) -> None:
    qtcore = pytest.importorskip("PySide6.QtCore")
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    from dsh_shell import webview_app

    app = qtwidgets.QApplication.instance() or qtwidgets.QApplication([])
    installer = webview_app._make_clipboard_permission_installer(SimpleNamespace())
    assert installer is not None
    installed_on: list[str] = []

    def record_install(window: Any) -> bool:
        installed_on.append(threading.current_thread().name)
        return True

    monkeypatch.setattr(webview_app, "install_clipboard_permission", record_install)
    worker = threading.Thread(
        target=webview_app._request_clipboard_permission_install,
        args=(installer,),
        name="pywebview-loaded-event",
    )
    worker.start()
    qtcore.QTimer.singleShot(2000, app.quit)
    app.exec()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert installed_on == [threading.main_thread().name]


def test_clipboard_installer_is_not_created_for_non_qt_backends(monkeypatch: Any) -> None:
    monkeypatch.setattr(webview_app.sys, "platform", "win32")

    assert webview_app._make_clipboard_permission_installer(SimpleNamespace()) is None
