(function () {
  "use strict";

  var ICON = "__DSH_ICON_DATA_URI__";
  var STYLE_ID = "dsh-shell-style";
  var BAR_ID = "dsh-shell-bar";
  var BAR_HEIGHT = 34;

  if (window.__dshShellInstalled && document.getElementById(BAR_ID)) return;
  window.__dshShellInstalled = true;

  function api() {
    return window.pywebview && window.pywebview.api ? window.pywebview.api : null;
  }

  function safeCall(method, args) {
    var bridge = api();
    if (!bridge || typeof bridge[method] !== "function") return Promise.resolve(null);
    try {
      return Promise.resolve(bridge[method].apply(bridge, args || []));
    } catch (error) {
      return Promise.reject(error);
    }
  }

  function toast(message, kind) {
    var host = document.getElementById("dsh-shell-toast");
    if (!host) {
      host = document.createElement("div");
      host.id = "dsh-shell-toast";
      host.setAttribute("role", "status");
      (document.body || document.documentElement).appendChild(host);
    }
    var item = document.createElement("div");
    item.className = "dsh-shell-toast-item " + (kind || "info");
    item.textContent = String(message);
    host.appendChild(item);
    setTimeout(function () {
      item.classList.add("leaving");
      setTimeout(function () { item.remove(); }, 220);
    }, 4200);
  }
  window.__dshShellToast = toast;

  function installStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = [
      "html body { padding-top: " + BAR_HEIGHT + "px !important; box-sizing: border-box !important; }",
      "#root { height: calc(100vh - " + BAR_HEIGHT + "px) !important; }",
      ".dsh-resize { position: fixed; z-index: 2147483648; background: transparent; touch-action: none; }",
      "#dsh-resize-n { top: 0; left: 14px; right: 14px; height: 4px; cursor: ns-resize; }",
      "#dsh-resize-s { bottom: 0; left: 14px; right: 14px; height: 5px; cursor: ns-resize; }",
      "#dsh-resize-w { left: 0; top: 14px; bottom: 14px; width: 5px; cursor: ew-resize; }",
      "#dsh-resize-e { right: 0; top: 14px; bottom: 14px; width: 5px; cursor: ew-resize; }",
      "#dsh-resize-nw { top: 0; left: 0; width: 14px; height: 14px; cursor: nwse-resize; }",
      "#dsh-resize-ne { top: 0; right: 0; width: 14px; height: 14px; cursor: nesw-resize; }",
      "#dsh-resize-sw { bottom: 0; left: 0; width: 14px; height: 14px; cursor: nesw-resize; }",
      "#dsh-resize-se { bottom: 0; right: 0; width: 14px; height: 14px; cursor: nwse-resize; }",
      "#dsh-shell-bar {",
      "  position: fixed; top: 0; left: 0; right: 0; height: " + BAR_HEIGHT + "px; z-index: 2147483647;",
      "  box-sizing: border-box; display: flex; align-items: stretch; overflow: hidden;",
      "  color: #182031; background: rgba(250, 251, 253, .97);",
      "  border-bottom: 1px solid rgba(15, 23, 42, .12);",
      "  font: 12px/1 -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Noto Sans CJK SC', sans-serif;",
      "  user-select: none; -webkit-user-select: none;",
      "  backdrop-filter: blur(18px) saturate(1.2);",
      "}",
      "@media (prefers-color-scheme: dark) {",
      "  #dsh-shell-bar { color: #e8ecf5; background: rgba(20, 22, 28, .97); border-bottom-color: rgba(255,255,255,.10); }",
      "}",
      ".dsh-bar-drag { flex: 1; display: flex; align-items: center; gap: 7px; padding: 0 12px; cursor: default; min-width: 0; }",
      ".dsh-bar-title { font-weight: 600; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }",
      ".dsh-bar-logo { width: 18px; height: 18px; object-fit: contain; flex: 0 0 auto; pointer-events: none; }",
      ".dsh-bar-status { display: inline-flex; align-items: center; justify-content: center; padding: 0 3px; color: #5b6577; }",
      "@media (prefers-color-scheme: dark) { .dsh-bar-status { color: #a6b0c3; } }",
      ".dsh-bar-dot { width: 6px; height: 6px; border-radius: 50%; background: #94a3b8; box-shadow: 0 0 0 3px rgba(148,163,184,.12); }",
      ".dsh-bar-dot.ok { background: #22c55e; box-shadow: 0 0 0 3px rgba(34,197,94,.14); }",
      ".dsh-bar-dot.bad { background: #ef4444; box-shadow: 0 0 0 3px rgba(239,68,68,.14); }",
      ".dsh-bar-actions { display: flex; align-items: stretch; border-left: 1px solid rgba(15,23,42,.10); }",
      "@media (prefers-color-scheme: dark) { .dsh-bar-actions { border-left-color: rgba(255,255,255,.10); } }",
      ".dsh-bar-btn { appearance: none; border: 0; background: transparent; color: inherit; width: 30px; padding: 0;",
      "  display: inline-flex; align-items: center; justify-content: center; cursor: pointer; opacity: .72;",
      "  transition: background .12s, opacity .12s; }",
      ".dsh-bar-btn:hover { opacity: 1; background: rgba(15, 23, 42, .07); }",
      "@media (prefers-color-scheme: dark) { .dsh-bar-btn:hover { background: rgba(255,255,255,.09); } }",
      ".dsh-bar-btn.close:hover { background: #e5484d; color: #fff; opacity: 1; }",
      ".dsh-bar-btn svg { width: 13px; height: 13px; display: block; }",
      "#dsh-shell-toast { position: fixed; right: 18px; bottom: 18px; z-index: 2147483647; display: flex; flex-direction: column;",
      "  gap: 8px; max-width: min(380px, calc(100vw - 36px)); pointer-events: none; }",
      ".dsh-shell-toast-item { padding: 10px 13px; border-radius: 10px; font: 12px/1.45 -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif;",
      "  color: #fff; background: rgba(24, 32, 49, .94); box-shadow: 0 8px 28px rgba(0,0,0,.22); opacity: 0; transform: translateY(6px);",
      "  animation: dsh-toast-in .18s ease forwards; pointer-events: auto; white-space: pre-wrap; }",
      ".dsh-shell-toast-item.error { background: #b42318; }",
      ".dsh-shell-toast-item.success { background: #157347; }",
      ".dsh-shell-toast-item.leaving { animation: dsh-toast-out .2s ease forwards; }",
      "@keyframes dsh-toast-in { to { opacity: 1; transform: translateY(0); } }",
      "@keyframes dsh-toast-out { to { opacity: 0; transform: translateY(4px); } }",
      "@media (prefers-reduced-motion: reduce) { #dsh-shell-bar { transition: none; }",
      "  .dsh-shell-toast-item, .dsh-shell-toast-item.leaving { animation: none; opacity: 1; transform: none; } }"
    ].join("\n");
    (document.head || document.documentElement).appendChild(style);
  }

  function icon(path) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.35");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    var pathEl = document.createElementNS("http://www.w3.org/2000/svg", "path");
    pathEl.setAttribute("d", path);
    svg.appendChild(pathEl);
    return svg;
  }

  var MAXIMIZE_PATH = "M4 4h8v8H4z";
  var RESTORE_PATH = "M6 3.5h6.5V10 M3.5 6.5H10V13H3.5z";

  function button(action, label, path, extraClass) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "dsh-bar-btn" + (extraClass ? " " + extraClass : "");
    btn.dataset.action = action;
    btn.title = label;
    btn.setAttribute("aria-label", label);
    btn.appendChild(icon(path));
    btn.addEventListener("mousedown", function (event) { event.stopPropagation(); });
    btn.addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();
      if (action === "settings") {
        safeCall("open_settings").catch(function (error) { toast(String(error), "error"); });
        return;
      }
      if (action === "reload") {
        safeCall("main_action", ["reload"]).catch(function (error) { toast(String(error), "error"); });
        return;
      }
      safeCall("main_action", [action])
        .then(refreshWindowState)
        .catch(function (error) { toast(String(error), "error"); });
    });
    return btn;
  }

  function updateMaximizeIcon(maximized) {
    var btn = document.querySelector('#dsh-shell-bar [data-action="toggle_maximize"]');
    if (!btn) return;
    btn.replaceChildren(icon(maximized ? RESTORE_PATH : MAXIMIZE_PATH));
    btn.title = maximized ? "还原" : "最大化";
    btn.setAttribute("aria-label", btn.title);
  }

  function refreshWindowState() {
    safeCall("window_state").then(function (state) {
      if (state) updateMaximizeIcon(!!state.maximized);
    }).catch(function () {});
  }
  window.__dshShellRefreshWindowState = refreshWindowState;

  var MIN_WIDTH = 900;
  var MIN_HEIGHT = 600;

  function applyResize(latest) {
    safeCall("set_window_bounds", [latest.x, latest.y, latest.width, latest.height]);
  }

  function startResize(event) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    var direction = event.currentTarget.dataset.dir || "";
    var startX = event.screenX;
    var startY = event.screenY;
    safeCall("prepare_resize").then(function (start) {
      refreshWindowState();
      if (!start || typeof start.width !== "number" || typeof start.height !== "number") return;
      var latest = null;
      var frame = 0;

      function flush() {
        frame = 0;
        if (!latest) return;
        var bounds = latest;
        latest = null;
        applyResize(bounds);
      }

      function onMove(moveEvent) {
        var dx = moveEvent.screenX - startX;
        var dy = moveEvent.screenY - startY;
        var x = start.x;
        var y = start.y;
        var width = start.width;
        var height = start.height;
        if (direction.indexOf("e") !== -1) {
          width = Math.max(MIN_WIDTH, start.width + dx);
        }
        if (direction.indexOf("w") !== -1) {
          width = Math.max(MIN_WIDTH, start.width - dx);
          x = start.x + (start.width - width);
        }
        if (direction.indexOf("s") !== -1) {
          height = Math.max(MIN_HEIGHT, start.height + dy);
        }
        if (direction.indexOf("n") !== -1) {
          height = Math.max(MIN_HEIGHT, start.height - dy);
          y = start.y + (start.height - height);
        }
        latest = {
          x: Math.round(x),
          y: Math.round(y),
          width: Math.round(width),
          height: Math.round(height)
        };
        if (!frame) frame = requestAnimationFrame(flush);
      }

      function onUp() {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onUp);
        if (frame) cancelAnimationFrame(frame);
        if (latest) {
          var bounds = latest;
          latest = null;
          applyResize(bounds);
        }
      }

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
    }).catch(function (error) {
      toast(String(error), "error");
    });
  }

  function startMove(event) {
    if (event.button !== 0) return;
    if (event.target.closest(".dsh-bar-btn")) return;
    event.preventDefault();
    event.stopPropagation();
    var startX = event.screenX;
    var startY = event.screenY;
    safeCall("get_window_bounds").then(function (start) {
      if (!start || typeof start.x !== "number" || typeof start.y !== "number") return;
      var latest = null;
      var frame = 0;

      function flush() {
        frame = 0;
        if (!latest) return;
        var next = latest;
        latest = null;
        safeCall("move_window", [next.x, next.y]);
      }

      function onMove(moveEvent) {
        latest = {
          x: Math.round(start.x + moveEvent.screenX - startX),
          y: Math.round(start.y + moveEvent.screenY - startY)
        };
        if (!frame) frame = requestAnimationFrame(flush);
      }

      function onUp() {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onUp);
        if (frame) cancelAnimationFrame(frame);
        if (latest) {
          var next = latest;
          latest = null;
          safeCall("move_window", [next.x, next.y]);
        }
      }

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
    }).catch(function (error) {
      toast(String(error), "error");
    });
  }

  function installResizeZones() {
    if (document.getElementById("dsh-resize-n")) return;
    ["n", "s", "w", "e", "nw", "ne", "sw", "se"].forEach(function (direction) {
      var zone = document.createElement("div");
      zone.id = "dsh-resize-" + direction;
      zone.className = "dsh-resize";
      zone.dataset.dir = direction;
      zone.title = "调整窗口大小";
      zone.addEventListener("pointerdown", startResize);
      document.body.appendChild(zone);
    });
  }

  function installBar() {
    if (!document.body) return;
    installStyle();
    installResizeZones();

    if (document.getElementById(BAR_ID)) return;

    var bar = document.createElement("div");
    bar.id = BAR_ID;
    bar.setAttribute("role", "banner");

    var drag = document.createElement("div");
    drag.className = "dsh-bar-drag";
    drag.title = "拖动窗口 · 双击最大化";
    drag.addEventListener("pointerdown", startMove);
    var logo = document.createElement("img");
    logo.className = "dsh-bar-logo";
    logo.alt = "";
    logo.draggable = false;
    logo.src = ICON;
    var status = document.createElement("span");
    status.className = "dsh-bar-status";
    var dot = document.createElement("i");
    dot.className = "dsh-bar-dot";
    status.appendChild(dot);
    var title = document.createElement("span");
    title.className = "dsh-bar-title";
    title.textContent = "Harness";
    drag.appendChild(logo);
    drag.appendChild(title);
    drag.appendChild(status);
    drag.addEventListener("dblclick", function (event) {
      if (event.target.closest(".dsh-bar-btn")) return;
      safeCall("main_action", ["toggle_maximize"]).then(refreshWindowState);
    });

    var actions = document.createElement("div");
    actions.className = "dsh-bar-actions";
    actions.appendChild(button("settings", "设置 · MCP 与 Skills", "M8.4 2.2h-.8a1 1 0 0 0-1 1v.32a1 1 0 0 1-.5.86l-.7.4a1 1 0 0 1-1 0l-.27-.15a1 1 0 0 0-1.37.37l-.4.7a1 1 0 0 0 .37 1.36l.27.16a1 1 0 0 1 .5.86v.8a1 1 0 0 1-.5.87l-.27.15a1 1 0 0 0-.37 1.37l.4.7a1 1 0 0 0 1.37.36l.27-.15a1 1 0 0 1 1 0l.7.4a1 1 0 0 1 .5.86v.32a1 1 0 0 0 1 1h.8a1 1 0 0 0 1-1v-.32a1 1 0 0 1 .5-.86l.7-.4a1 1 0 0 1 1 0l.27.15a1 1 0 0 0 1.37-.37l.4-.7a1 1 0 0 0-.37-1.36l-.27-.16a1 1 0 0 1-.5-.86v-.8a1 1 0 0 1 .5-.87l.27-.15a1 1 0 0 0 .37-1.37l-.4-.7a1 1 0 0 0-1.37-.36l-.27.15a1 1 0 0 1-1 0l-.7-.4a1 1 0 0 1-.5-.86v-.32a1 1 0 0 0-1-1h-.8Z M8 6.1a1.9 1.9 0 1 0 0 3.8 1.9 1.9 0 0 0 0-3.8Z"));
    actions.appendChild(button("reload", "重新加载", "M13.2 7.2a5.2 5.2 0 1 0-.5 3.2 M13.4 2.7v4.5H8.9"));
    actions.appendChild(button("minimize", "最小化", "M3 8h10"));
    actions.appendChild(button("toggle_maximize", "最大化", MAXIMIZE_PATH));
    actions.appendChild(button("close", "隐藏到托盘", "M4.2 4.2l7.6 7.6 M11.8 4.2l-7.6 7.6", "close"));

    bar.appendChild(drag);
    bar.appendChild(actions);
    bar.classList.add("visible");

    document.body.appendChild(bar);
    refreshStatus();
    refreshWindowState();
    setInterval(refreshStatus, 15000);
    setInterval(refreshWindowState, 15000);
  }

  function refreshStatus() {
    var dot = document.querySelector("#dsh-shell-bar .dsh-bar-dot");
    var statusEl = document.querySelector("#dsh-shell-bar .dsh-bar-status");
    if (!dot || !statusEl) return;
    safeCall("runtime_status").then(function (status) {
      if (!status) return;
      dot.className = "dsh-bar-dot";
      if (status.healthy) {
        dot.classList.add("ok");
      } else {
        dot.classList.add("bad");
      }
      statusEl.title = [
        status.healthy ? "已连接" : "未连接",
        status.port ? "端口: " + status.port : "",
        status.owned ? "独立实例" : "复用服务",
        status.profile ? "profile: " + status.profile : "",
        status.source ? "来源: " + status.source : "",
        status.error || ""
      ].filter(Boolean).join("\n");
    }).catch(function () {
      dot.className = "dsh-bar-dot bad";
      statusEl.title = "状态未知";
    });
  }

  function boot() {
    if (!document.body) {
      setTimeout(boot, 40);
      return;
    }
    installBar();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }

  setInterval(function () {
    if (!document.getElementById(BAR_ID)) installBar();
  }, 2500);
})();
