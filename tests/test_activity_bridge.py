from __future__ import annotations

import inspect
import queue
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dsh_shell import ipc, webview_app
from dsh_shell.api import ShellApi

CHROME_JS = Path(__file__).resolve().parents[1] / "ui" / "chrome.js"


def _api_with_callback(callback: Any) -> ShellApi:
    return ShellApi(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        on_session_activity=callback,
    )


def test_session_activity_constant_and_public_interface() -> None:
    assert ipc.SESSION_ACTIVITY == "session_activity"

    parameters = inspect.signature(ShellApi.__init__).parameters
    activity_parameter = parameters["on_session_activity"]
    assert activity_parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert activity_parameter.default is None
    assert callable(ShellApi.report_session_activity)


def test_report_session_activity_normalizes_and_only_calls_on_change() -> None:
    calls: list[bool] = []
    api = _api_with_callback(calls.append)

    assert api.report_session_activity(1) is True
    assert api.report_session_activity(True) is True
    assert calls == [True]

    assert api.report_session_activity("") is False
    assert api.report_session_activity(0) is False
    assert calls == [True, False]


def test_report_session_activity_is_thread_safe() -> None:
    calls: list[bool] = []
    api = _api_with_callback(calls.append)
    barrier = threading.Barrier(12)

    def report() -> None:
        barrier.wait()
        assert api.report_session_activity(True) is True

    threads = [threading.Thread(target=report) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert calls == [True]


class _Signal:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def __iadd__(self, handler: Any) -> _Signal:
        self.handlers.append(handler)
        return self


class _FakeWindow:
    def __init__(self) -> None:
        self.events = SimpleNamespace(loaded=_Signal(), closing=_Signal())
        self.state: dict[str, Any] = {}

    def run_js(self, script: str) -> None:
        pass

    def destroy(self) -> None:
        pass


class _FakeRuntime:
    profile = "default"

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def ensure(self) -> SimpleNamespace:
        return SimpleNamespace(url="http://127.0.0.1:3080/")

    def stop(self) -> None:
        pass


class _FakeShellApi:
    last_on_session_activity: Any = None

    def __init__(
        self,
        runtime: Any,
        mcp: Any,
        skills: Any,
        *,
        request_quit: Any = None,
        on_session_activity: Any = None,
    ) -> None:
        self.request_quit = request_quit
        type(self).last_on_session_activity = on_session_activity

    def bind(self, main: Any, settings: Any, *, runtime_error: str | None = None) -> None:
        pass


def test_run_webview_process_bridges_activity_events_to_queue(monkeypatch: Any) -> None:
    commands: queue.Queue[Any] = queue.Queue()
    activity_events: queue.Queue[Any] = queue.Queue()
    windows: list[_FakeWindow] = []

    def create_window(*args: Any, **kwargs: Any) -> _FakeWindow:
        window = _FakeWindow()
        windows.append(window)
        return window

    monkeypatch.setattr(webview_app, "ensure_data_dirs", lambda: None)
    monkeypatch.setattr(webview_app, "DshRuntime", _FakeRuntime)
    monkeypatch.setattr(webview_app, "McpConfigManager", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(webview_app, "SkillsManager", lambda: SimpleNamespace())
    monkeypatch.setattr(webview_app, "ShellApi", _FakeShellApi)
    monkeypatch.setattr(webview_app, "resource_path", lambda *parts: Path("/tmp/dsh-shell-test"))
    monkeypatch.setattr(webview_app, "icon_path", lambda: Path("/tmp/dsh-shell-test/icon.png"))
    monkeypatch.setattr(webview_app.webview, "settings", {})
    monkeypatch.setattr(webview_app.webview, "create_window", create_window)
    monkeypatch.setattr(webview_app.webview, "start", lambda **kwargs: None)

    assert webview_app.run_webview_process({}, commands, activity_events) == 0

    callback = _FakeShellApi.last_on_session_activity
    assert callable(callback)
    callback(1)
    callback(0)
    assert activity_events.get_nowait() == (ipc.SESSION_ACTIVITY, True)
    assert activity_events.get_nowait() == (ipc.SESSION_ACTIVITY, False)


def test_run_webview_process_activity_callback_ignores_queue_failure(monkeypatch: Any) -> None:
    class BrokenQueue:
        def put(self, item: Any) -> None:
            raise RuntimeError("queue closed")

    windows: list[_FakeWindow] = []

    def create_window(*args: Any, **kwargs: Any) -> _FakeWindow:
        window = _FakeWindow()
        windows.append(window)
        return window

    monkeypatch.setattr(webview_app, "ensure_data_dirs", lambda: None)
    monkeypatch.setattr(webview_app, "DshRuntime", _FakeRuntime)
    monkeypatch.setattr(webview_app, "McpConfigManager", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(webview_app, "SkillsManager", lambda: SimpleNamespace())
    monkeypatch.setattr(webview_app, "ShellApi", _FakeShellApi)
    monkeypatch.setattr(webview_app, "resource_path", lambda *parts: Path("/tmp/dsh-shell-test"))
    monkeypatch.setattr(webview_app, "icon_path", lambda: Path("/tmp/dsh-shell-test/icon.png"))
    monkeypatch.setattr(webview_app.webview, "settings", {})
    monkeypatch.setattr(webview_app.webview, "create_window", create_window)
    monkeypatch.setattr(webview_app.webview, "start", lambda **kwargs: None)

    assert webview_app.run_webview_process({}, queue.Queue(), BrokenQueue()) == 0

    callback = _FakeShellApi.last_on_session_activity
    assert callable(callback)
    callback(True)


def test_chrome_watcher_uses_stable_dom_markers_and_reports_changes() -> None:
    script = CHROME_JS.read_text(encoding="utf-8")

    assert '[data-row-key^="session:"] [data-state="ongoing"]' in script
    assert "[data-chat-running]" in script
    assert "MutationObserver" in script
    assert "subtree: true" in script
    assert "childList: true" in script
    assert "attributes: true" in script
    assert "data-state" in script
    assert "data-chat-running" in script
    assert "data-row-key" in script
    assert 'safeCall("report_session_activity", [active])' in script
    assert "setInterval(checkSessionActivity" in script
