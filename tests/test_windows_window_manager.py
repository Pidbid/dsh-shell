from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from dsh_shell import api as api_module
from dsh_shell import window_manager
from dsh_shell.api import ShellApi
from dsh_shell.window_manager import MINMAXINFO, MONITORINFO


def test_frameless_style_preserves_native_maximize_capability_when_resizable() -> None:
    style = window_manager._build_frameless_window_style(
        window_manager.WS_CAPTION,
        enable_resize=True,
    )

    assert style & window_manager.WS_CAPTION == 0
    assert style & window_manager.WS_MAXIMIZEBOX
    assert style & window_manager.WS_THICKFRAME


def test_frameless_style_removes_maximize_capability_when_fixed_size() -> None:
    style = window_manager._build_frameless_window_style(
        window_manager.WS_CAPTION | window_manager.WS_MAXIMIZEBOX | window_manager.WS_THICKFRAME,
        enable_resize=False,
    )

    assert style & window_manager.WS_MAXIMIZEBOX == 0
    assert style & window_manager.WS_THICKFRAME == 0


def test_minmax_info_uses_work_area_and_keeps_large_tracking_limits() -> None:
    info = MONITORINFO()
    info.rcMonitor.left = 1920
    info.rcMonitor.top = 0
    info.rcMonitor.right = 3840
    info.rcMonitor.bottom = 1080
    info.rcWork.left = 1920
    info.rcWork.top = 0
    info.rcWork.right = 3840
    info.rcWork.bottom = 1040
    limits = MINMAXINFO()
    limits.ptMaxTrackSize.x = 5000
    limits.ptMaxTrackSize.y = 10

    window_manager._apply_work_area_to_minmax_info(info, limits)

    assert limits.ptMaxPosition.x == 0
    assert limits.ptMaxPosition.y == 0
    assert limits.ptMaxSize.x == 1920
    assert limits.ptMaxSize.y == 1040
    assert limits.ptMaxTrackSize.x == 5000
    assert limits.ptMaxTrackSize.y == 1040


def test_minmax_info_handles_left_monitor_with_top_taskbar() -> None:
    info = MONITORINFO()
    info.rcMonitor.left = -1920
    info.rcMonitor.top = 0
    info.rcMonitor.right = 0
    info.rcMonitor.bottom = 1080
    info.rcWork.left = -1920
    info.rcWork.top = 40
    info.rcWork.right = 0
    info.rcWork.bottom = 1080
    limits = MINMAXINFO()
    limits.ptMaxTrackSize.x = 1000
    limits.ptMaxTrackSize.y = 1000

    window_manager._apply_work_area_to_minmax_info(info, limits)

    assert limits.ptMaxPosition.x == 0
    assert limits.ptMaxPosition.y == 40
    assert limits.ptMaxSize.x == 1920
    assert limits.ptMaxSize.y == 1040
    assert limits.ptMaxTrackSize.x == 1920
    assert limits.ptMaxTrackSize.y == 1040


def test_maximize_window_to_work_area_sets_bounds_on_ui_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []
    working_area = object()
    maximized_state = object()

    class FakeAction:
        def __init__(self, callback: object) -> None:
            self.callback = callback

    class NativeForm:
        InvokeRequired = True
        MaximizedBounds = None
        WindowState = None

        def Invoke(self, action: FakeAction) -> None:
            calls.append(("invoke", self))
            action.callback()  # type: ignore[operator]

    native = NativeForm()
    fake_system = ModuleType("System")
    fake_system.Action = FakeAction  # type: ignore[attr-defined]
    fake_forms = ModuleType("System.Windows.Forms")
    fake_forms.FormWindowState = SimpleNamespace(Maximized=maximized_state)
    fake_forms.Screen = SimpleNamespace(
        FromControl=lambda form: calls.append(("screen", form))
        or SimpleNamespace(WorkingArea=working_area)
    )
    fake_system.Windows = SimpleNamespace(Forms=fake_forms)  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "System", fake_system)
    monkeypatch.setitem(sys.modules, "System.Windows.Forms", fake_forms)
    monkeypatch.setattr(window_manager.platform, "system", lambda: "Windows")

    assert window_manager.maximize_window_to_work_area(SimpleNamespace(native=native)) is True
    assert calls == [("invoke", native), ("screen", native)]
    assert native.MaximizedBounds is working_area
    assert native.WindowState is maximized_state


def test_maximize_window_to_work_area_returns_false_without_native_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(window_manager.platform, "system", lambda: "Windows")

    assert window_manager.maximize_window_to_work_area(SimpleNamespace(native=None)) is False


def test_attach_windows_frameless_window_hooks_before_show(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    class FakeEvent:
        def __init__(self) -> None:
            self.callbacks: list[object] = []

        def __iadd__(self, callback: object) -> FakeEvent:
            self.callbacks.append(callback)
            return self

    before_show = FakeEvent()
    window = SimpleNamespace(events=SimpleNamespace(before_show=before_show))
    monkeypatch.setattr(window_manager.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        window_manager,
        "_apply_windows_frameless_style",
        lambda target, *, enable_resize: calls.append((target, enable_resize)) or True,
    )

    assert window_manager.attach_windows_frameless_window(window, enable_resize=True) is True
    assert len(before_show.callbacks) == 1
    before_show.callbacks[0]()  # type: ignore[operator]
    assert calls == [(window, True)]


def test_windows_maximize_prefers_working_area_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    api = ShellApi(SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    api._main = SimpleNamespace()
    api._linux = False

    monkeypatch.setattr(api_module, "maximize_window_to_work_area", lambda window: True)
    monkeypatch.setattr(
        api,
        "_windows_show",
        lambda command: pytest.fail(f"unexpected ShowWindow fallback: {command}"),
    )

    assert api.main_action("maximize") is True
    assert api._main_maximized is True


def test_windows_maximize_uses_pywebview_only_as_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    api = ShellApi(SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    api._main = SimpleNamespace(maximize=lambda: calls.append("maximize"))
    api._linux = False

    monkeypatch.setattr(api_module, "maximize_window_to_work_area", lambda window: False)
    monkeypatch.setattr(
        api,
        "_windows_show",
        lambda command: pytest.fail(f"unexpected ShowWindow fallback: {command}"),
    )

    assert api.main_action("toggle_maximize") is True
    assert calls == ["maximize"]
    assert api._main_maximized is True
