"""The GUI process: one main DSH window plus a hidden settings window."""

from __future__ import annotations

import base64
import html
import json
import os
import queue
import sys
import threading
from typing import Any

import webview
from loguru import logger

from .api import ShellApi
from .dsh_runtime import DshRuntime
from .ipc import (
    OPEN_EXTERNAL,
    OPEN_SETTINGS,
    QUIT,
    RELOAD,
    RESTART_DSH,
    SHOW_MAIN,
    TOGGLE_MAIN,
    TRAY_UNAVAILABLE,
)
from .mcp_config import McpConfigManager
from .paths import ensure_data_dirs, icon_path, resource_path
from .skills import SkillsManager
from .version import __version__


def build_chrome_script() -> str:
    path = resource_path("ui", "chrome.js")
    script = path.read_text(encoding="utf-8")
    icon_candidates = (
        resource_path("assets", "icon-64.png"),
        resource_path("assets", "icon.png"),
    )
    icon_file = next((candidate for candidate in icon_candidates if candidate.is_file()), None)
    if icon_file is None:
        data_uri = ""
    else:
        encoded = base64.b64encode(icon_file.read_bytes()).decode("ascii")
        data_uri = f"data:image/png;base64,{encoded}"
    return script.replace("__DSH_ICON_DATA_URI__", data_uri)


def _error_page(message: str) -> str:
    escaped = html.escape(message)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>dsh-shell · 启动失败</title>
<style>
  :root {{ color-scheme: light dark; --bg:#f6f7fb; --panel:#fff; --text:#182031; --muted:#667085;
    --accent:#4b6bf5; --danger:#d92d20; --border:rgba(24,32,49,.12); }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1115; --panel:#171a21; --text:#e8ecf5;
    --muted:#9aa4b7; --accent:#7c93ff; --danger:#ff7b72; --border:rgba(255,255,255,.10); }} }}
  * {{ box-sizing:border-box; }}
  html,body {{ height:100%; margin:0; }}
  body {{ padding: calc(36px + 24px) 24px 24px; background:var(--bg); color:var(--text);
    font:13px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Noto Sans CJK SC",sans-serif;
    display:flex; align-items:center; justify-content:center; overflow:auto; }}
  .panel {{ width:min(620px,100%); background:var(--panel); border:1px solid var(--border);
    border-radius:18px; padding:32px; box-shadow:0 24px 60px rgba(20,32,64,.13); }}
  .mark {{ width:54px; height:54px; border-radius:15px; display:grid; place-items:center;
    background:color-mix(in srgb,var(--accent) 13%,transparent); color:var(--accent);
    font-size:25px; margin-bottom:18px; }}
  h1 {{ margin:0; font-size:22px; letter-spacing:-.02em; }}
  p {{ margin:11px 0 0; color:var(--muted); }}
  pre {{ margin:18px 0 0; padding:14px 15px; border-radius:11px; background:color-mix(in srgb,var(--danger) 9%,transparent);
    color:var(--danger); font:11.5px/1.6 "SFMono-Regular","Cascadia Code",Menlo,monospace;
    white-space:pre-wrap; overflow-wrap:anywhere; max-height:240px; overflow:auto; }}
  .actions {{ display:flex; gap:9px; flex-wrap:wrap; margin-top:22px; }}
  button {{ appearance:none; border:1px solid var(--border); background:var(--panel); color:var(--text);
    border-radius:9px; padding:9px 14px; font:600 12px inherit; cursor:pointer; }}
  button.primary {{ background:var(--accent); border-color:var(--accent); color:#fff; }}
  button:hover {{ filter:brightness(.97); }}
  .version {{ margin-top:22px; color:var(--muted); font-size:11px; }}
</style>
</head>
<body>
  <main class="panel">
    <div class="mark">◔</div>
    <h1>未能启动 DeepSeek Harness</h1>
    <p>Shell 没有找到可复用的 <code>dsh web</code> 服务，也没能拉起独立实例。</p>
    <pre id="details">{escaped}</pre>
    <div class="actions">
      <button class="primary" onclick="retry()">重试连接</button>
      <button onclick="settings()">打开设置</button>
      <button onclick="quit()">退出</button>
    </div>
    <div class="version">dsh-shell v{__version__}</div>
  </main>
  <script>
    function bridge() {{ return window.pywebview && window.pywebview.api; }}
    function retry() {{
      var api = bridge();
      if (!api) return;
      api.retry_runtime().catch(function (error) {{ alert(String(error && error.message || error)); }});
    }}
    function settings() {{
      var api = bridge();
      if (api) api.open_settings();
    }}
    function quit() {{
      var api = bridge();
      if (api) api.quit_app();
    }}
    window.addEventListener('pywebviewready', function () {{
      document.body.dataset.bridge = 'ready';
    }}, {{ once: true }});
  </script>
</body>
</html>"""


def _wire_window_events(
    main: webview.Window,
    settings: webview.Window,
    api: ShellApi,
    *,
    quit_event: threading.Event,
    tray_state: dict[str, bool],
) -> None:
    def inject() -> None:
        try:
            script = build_chrome_script()
            main.run_js(script)
        except Exception as error:
            logger.warning("Could not inject shell chrome: {}", error)

    def on_loaded() -> None:
        # The document can finish loading before pywebview's bridge is ready; the
        # injected script itself waits for `pywebviewready`.
        threading.Timer(0.25, inject).start()

    main.events.loaded += on_loaded

    def main_closing() -> bool:
        if quit_event.is_set():
            return True
        if not tray_state["available"]:
            quit_event.set()
            try:
                settings.destroy()
            except Exception:
                pass
            return True
        api.main_action("hide")
        logger.debug("Main window hidden to tray")
        return False

    def settings_closing() -> bool:
        if quit_event.is_set():
            return True
        if not tray_state["available"]:
            quit_event.set()
            try:
                main.destroy()
            except Exception:
                pass
            return True
        settings.hide()
        return False

    main.events.closing += main_closing
    settings.events.closing += settings_closing


def _command_loop(
    main: webview.Window,
    settings: webview.Window,
    api: ShellApi,
    runtime: DshRuntime,
    commands: Any,
    quit_event: threading.Event,
    tray_state: dict[str, bool],
) -> None:
    while not quit_event.is_set():
        try:
            command = commands.get(timeout=0.25)
        except (queue.Empty, EOFError, OSError):
            continue
        if command is None:
            continue
        logger.debug("Webview command: {}", command)
        try:
            if command == SHOW_MAIN:
                api.main_action("show")
            elif command == TOGGLE_MAIN:
                api.main_action("toggle")
            elif command == TRAY_UNAVAILABLE:
                tray_state["available"] = False
                api.set_tray_available(False)
                logger.info("Tray unavailable; close buttons now quit the application")
            elif command == OPEN_SETTINGS:
                api.open_settings()
            elif command == RELOAD:
                api.reload_main()
            elif command == OPEN_EXTERNAL:
                api.open_external()
            elif command == RESTART_DSH:
                api.restart_dsh()
            elif command == QUIT:
                quit_event.set()
                logger.info("Shutting down webview")
                try:
                    runtime.stop()
                except Exception as error:
                    logger.warning("Runtime stop failed: {}", error)
                for name, window in (("main", main), ("settings", settings)):
                    try:
                        logger.debug("Destroying {} window", name)
                        window.destroy()
                    except Exception as error:
                        logger.warning("Could not destroy {} window: {}", name, error)
                # QtWebEngine occasionally keeps the hidden settings page alive
                # after its window is destroyed.  Never leave a tray-less child
                # process behind.
                threading.Timer(1.5, os._exit, args=(0,)).start()
                break
        except Exception as error:
            logger.warning("Command {} failed: {}", command, error)
            try:
                main.run_js(
                    "window.__dshShellToast && window.__dshShellToast("
                    + json.dumps(str(error))
                    + ", 'error')"
                )
            except Exception:
                pass


def run_webview_process(config: dict[str, Any], commands: Any) -> int:
    ensure_data_dirs()
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["ALLOW_FILE_URLS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.settings["SHOW_DEFAULT_MENUS"] = False
    # In debug mode pywebview otherwise opens one DevTools window per webview;
    # those windows show an unusable localhost inspector page.  CDP on 9229 is
    # the debugging surface instead.
    webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
    webview.settings["DRAG_REGION_SELECTOR"] = ".pywebview-drag-region"
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = False
    if config.get("debug"):
        webview.settings["REMOTE_DEBUGGING_PORT"] = int(config.get("debug_port", 9229))

    runtime = DshRuntime(
        explicit_url=config.get("url"),
        reuse=bool(config.get("reuse", True)),
        spawn=bool(config.get("spawn", True)),
        profile=config.get("profile"),
        preferred_port=config.get("port", 3080),
        dsh_command=config.get("dsh_command"),
        startup_timeout=float(config.get("startup_timeout", 180)),
    )

    endpoint = None
    runtime_error = None
    try:
        endpoint = runtime.ensure()
    except Exception as error:
        runtime_error = str(error)
        logger.error("DeepSeek Harness is unavailable: {}", error)

    mcp = McpConfigManager(profile=runtime.profile)
    skills = SkillsManager()
    local_commands: Any = commands
    quit_event = threading.Event()
    tray_state = {"available": True}

    def request_quit() -> None:
        try:
            local_commands.put(QUIT)
        except Exception:
            quit_event.set()

    api = ShellApi(runtime, mcp, skills, request_quit=request_quit)

    main = webview.create_window(
        "DeepSeek Harness",
        url=endpoint.url if endpoint else None,
        html=None if endpoint else _error_page(runtime_error or "未知错误"),
        js_api=api,
        width=1400,
        height=900,
        min_size=(900, 600),
        frameless=True,
        easy_drag=False,
        resizable=True,
        text_select=True,
        zoomable=False,
        transparent=False,
        shadow=True,
        background_color="#0f1115",
        confirm_close=False,
    )
    settings_path = resource_path("ui", "settings.html")
    settings = webview.create_window(
        "dsh-shell · 设置",
        url=settings_path.as_uri(),
        js_api=api,
        width=1020,
        height=780,
        min_size=(760, 560),
        frameless=True,
        easy_drag=False,
        resizable=True,
        hidden=True,
        text_select=True,
        transparent=False,
        shadow=True,
        background_color="#0f1115",
        confirm_close=False,
    )
    api.bind(main, settings, runtime_error=runtime_error)
    _wire_window_events(main, settings, api, quit_event=quit_event, tray_state=tray_state)

    command_thread = threading.Thread(
        target=_command_loop,
        args=(main, settings, api, runtime, commands, quit_event, tray_state),
        name="webview-commands",
        daemon=True,
    )
    command_thread.start()

    gui = None
    if sys.platform.startswith("linux"):
        try:
            import PySide6  # noqa: F401

            gui = "qt"
        except ImportError:
            gui = "gtk"

    try:
        webview.start(
            gui=gui,
            debug=bool(config.get("debug", False)),
            icon=str(icon_path()),
        )
    except Exception:
        logger.exception("pywebview failed to start")
        raise
    finally:
        quit_event.set()
        runtime.stop()
        logger.info("Webview process exiting")
    return 0
