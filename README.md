# dsh-shell

Frameless desktop shell for the **DeepSeek Harness** web UI, built with Python 3.12, [uv](https://docs.astral.sh/uv/), and [pywebview](https://pywebview.flowrl.com/).

The shell is deliberately decoupled from the harness:

- It talks to `dsh web` only over HTTP.
- It reuses an existing server on port `3080` (including token-authenticated ones) whenever possible.
- Otherwise it starts one with `dsh web`, or falls back to `npx` / `pnpm` / `npm`.
- It can be pointed at any compatible URL with `--url`.

It also manages the two user-facing extension surfaces:

- **MCP** — the official profile patch file `$DSH_HOME/profiles/<profile>/cordis.patch.yml`, with `@deepseek-ai/dsh-mcp-client` entries.
- **Skills** — directory bundles and flat Markdown files under `~/.agents/skills`, with enable/disable, edit, create, and safe delete.

## Features

- Frameless main window with square corners, a reserved 34 px top title bar
  (settings / reload / minimize / maximize-restore / close), and eight HTML
  resize handles (edges and corners).
- System tray icon with a right-click menu: show/hide, MCP/Skills settings,
  reload, open in browser, restart DSH, quit. All platforms use Qt's
  `QSystemTrayIcon`; on Linux it speaks StatusNotifierItem (GNOME
  AppIndicators, KDE, XFCE).
- Single-instance guard: a second launch focuses the first window.
- MCP form editor plus a raw YAML editor; comments, unknown fields, and `!!js` expressions are preserved.
- Skills manager with frontmatter editing, model/user invocation flags, enable/disable by moving between `skills` and `skills-disabled`, and move-to-trash deletion.
- Auth-token discovery from a reused `dsh web` server (environment, runtime record, supervisor logs, `/proc` stdout, journal).
- Onefile packaging with Nuitka for Linux (DEB) and Windows (EXE).

## Requirements

- **Python 3.12** and **uv**.
- Linux: Qt 6 / PySide6 WebEngine (installed by `uv sync`), plus the usual X11/Wayland system libraries.
- Windows/macOS: PySide6-Essentials (installed by `uv sync`) for the tray icon.
- Optional: Node.js + `dsh` for auto-starting the harness. If neither is available, reuse an existing server with `--url`.

## Development

```bash
uv sync --group dev

# Frameless window + tray
uv run main.py

# Debug: webview devtools on 127.0.0.1:9229, verbose logs
uv run main.py --debug

# Direct mode without a tray process (useful in debuggers)
uv run main.py --no-tray --debug

# Backend smoke test (runtime + MCP + skills)
uv run main.py --smoke-test

# Tests and lint
uv run pytest
uv run ruff check .
```

Logs are written to `~/.local/share/dsh-shell/logs/dsh-shell.log` (XDG paths on Linux, `%APPDATA%` on Windows).

### CLI

```text
--url URL              reuse an explicit dsh web URL (token URLs supported)
--port PORT            preferred dsh web port (default 3080)
--profile NAME         DeepSeek Harness profile (default: web)
--dsh-command CMD      dsh executable/command; the profile name is appended
--no-reuse             do not probe or reuse an existing service
--no-spawn             never start dsh; require --url or an existing service
--no-tray              run the webview without the tray host process
--debug                verbose logs + remote debugging port 9229
--smoke-test           print runtime/MCP/skills status without opening a window
--instance-port PORT   single-instance command socket (default 47831)
```

### Reusing an existing 3080 server

`dsh web` prints a URL such as:

```text
dsh web: http://127.0.0.1:3080/?token=...
```

The shell probes `127.0.0.1:3080`. If it receives `401`, it looks for the current
token in this order:

1. `DSH_WEB_TOKEN` / `DSH_WEB_AUTH_TOKEN`
2. the shell's own `runtime.json`
3. supervisor log files (including `stdout_logfile` paths from
   `/etc/supervisor/conf.d/*.conf`)
4. `/var/log/supervisor/dsh-web.log`, `~/dsh-web.log`, `~/.dsh/logs/web.log`
5. the listener process's stdout if it is a regular file
6. `journalctl -u dsh-web`

The token URL is then verified with a request before it is used. If no token can
be found, the shell starts its own instance on a free port instead of failing.

## MCP

The official MCP surface of DeepSeek Harness is the profile patch layer:

```text
$DSH_HOME/profiles/<profile>/cordis.patch.yml
```

Each server is one loader entry:

```yaml
- id: mcp-github
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: github
    transport: stdio
    command: npx
    args: ['-y', '@modelcontextprotocol/server-github']
    env:
      GITHUB_TOKEN: !!js process.env.GITHUB_TOKEN
```

The `web` profile sets `patchReload: live`, so saving from the shell applies
without restarting the harness. The shell:

- preserves comments, unrelated entries, unknown config keys, and untouched
  `!!js` tagged scalars;
- validates `serverName`, transport, stdio `command`, HTTP `url`, timeouts, and
  reconnect numbers;
- rejects duplicate ids and duplicate enabled `serverName` values;
- writes atomically with a `.bak` copy.

The raw YAML editor validates the whole file and writes your exact text.

## Skills

DeepSeek Harness discovers:

- directory bundles: `~/.agents/skills/<name>/SKILL.md`
- flat files: `~/.agents/skills/<name>.md`

A `SKILL.md` needs YAML frontmatter with `name` (kebab-case) and `description`.
Optional fields understood by the harness:

```yaml
---
name: my-skill
description: What this skill does
whenToUse: When to reach for it
disable-model-invocation: true
user-invocable: false
---
```

The shell edits these fields while preserving unknown frontmatter keys.

Enable/disable moves the bundle or file to the sibling
`~/.agents/skills-disabled/` directory, which is outside the scanned root.
Delete moves the entry to
`~/.local/share/dsh-shell/trash/skills/<timestamp>/` so it can be restored
manually. Symlinked entries are listed but never written or moved.

## Frameless window and tray

- A 34 px full-width title bar is reserved above the DSH page (`body` padding +
  `#root` height), so the shell controls never overlap DSH header/plugin
  buttons. Drag the title bar to move the window; double-click to toggle
  maximize.
- Eight transparent HTML handles resize the window from every edge and corner.
  The drag/resize IPC uses absolute screen coordinates and is throttled with
  `requestAnimationFrame`. Starting a resize exits maximized state first.
- Minimize hides the window (or uses Win32 `SW_MINIMIZE` on Windows); showing a
  previously maximized window on Linux returns it to the saved normal geometry
  instead of relying on WM restore.
- The title bar is always visible: status, settings, reload, minimize,
  maximize/restore, close. It does not auto-hide.
- Both windows are opaque and square-cornered; the maximize button switches to a
  restore icon while the window is maximized.
- Close / `×` hides to the tray. If the desktop has no tray support, close exits
  instead (the shell tells the child process that the tray is unavailable).
- On Linux maximize and minimize are simulated deterministically (resize to the
  current monitor; hide/show) because Qt's window-state calls are unreliable on
  Wayland/XWayland. Windows/macOS use native maximize/minimize.
- `--debug` enables CDP on `127.0.0.1:9229` but does **not** open pywebview's
  per-window DevTools windows.
- Tray menu (right-click on Linux): show/hide, MCP/Skills settings, reload, open in browser,
  restart DSH (owned instances only), quit.

The settings window is a second frameless pywebview window loaded from
`ui/settings.html`. It shares the same JS API as the main window.

> Linux note: QtWebEngine is forced to software compositing with
> `QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --no-sandbox"` because the
> Vulkan/GBM fallback can hang the renderer on X11/VM hosts. On Wayland desktops
> with XWayland the shell prefers `QT_QPA_PLATFORM=xcb` for reliable framing and
> window geometry. pywebview runs with its default private profile to avoid a
> two-window QWebEngineProfile deadlock; authentication works through the token
> URL.
>
> Input methods: the PySide6 wheels only ship the `compose` and `ibus` Qt
> input-context plugins; the distro fcitx5 plugin is built against the distro Qt
> and cannot be loaded by the bundled Qt (private-API symbol mismatch). The
> shell therefore selects `QT_IM_MODULES=ibus` / `QT_IM_MODULE=ibus` when the
> session asks for fcitx (or sets nothing), and the Linux build bundles the
> `platforminputcontexts` Qt plugins. When fcitx5 is the session IME it owns
> `org.freedesktop.IBus` through its IBus frontend, so Chinese/Japanese/Korean
> input keeps working in the webview.

## Build

### Artifacts

The repository build outputs are:

```text
dist/dsh-shell                  Linux Nuitka onefile
dist/dsh-shell.exe              Windows Nuitka onefile (PE32+ GUI, x86-64)
dist/dsh-shell_<version>_amd64.deb
```

### Linux onefile + DEB

```bash
./build_linux.sh
```

Outputs:

- `dist/dsh-shell` — Nuitka onefile executable
- `dist/dsh-shell_<version>_amd64.deb`

The DEB installs the onefile binary under `/opt/dsh-shell`, a wrapper at
`/usr/bin/dsh-shell`, a desktop entry, and hicolor icons.

### Windows onefile EXE

On Windows with `uv` (Nuitka downloads its supported MinGW64 toolchain when MSVC
is absent):

```bat
build_windows.bat
```

Output: `dist\dsh-shell.exe`.

The build includes `ui/` and `assets/` as data directories, PySide6 for the
tray, the pywebview plugin, pythonnet/CLR loader data, Python package submodules,
and the application icon.
The script's smoke test runs `dsh-shell.exe --version`; a built binary was
verified on Windows 10 (x86-64) with `dsh-shell 0.0.1`.

### Icons

The PNG/ICO assets under `assets/` are committed. To regenerate them from
`assets/icon-source.png` (Qt-based, no Pillow):

```bash
uv run python scripts/make_icons.py [path-to-source.png]
```

## Layout

```text
main.py                     entrypoint, single instance, tray host, child process
dsh_shell/
  dsh_runtime.py            reuse/probe/token discovery/spawn/stop
  mcp_config.py             cordis.patch.yml CRUD + validation
  skills.py                 ~/.agents/skills discovery + CRUD
  api.py                    pywebview JS API
  webview_app.py            windows, chrome injection, command loop, error page
  tray.py                   Qt system tray menu
  single_instance.py        lock + local command socket
  paths.py                  XDG/DSH paths and bundled resources
ui/
  chrome.js                 injected overlay pill + drag strip
  settings.html/css/js      MCP + skills settings window
tests/                      pytest regression suite
build_linux.sh              Nuitka onefile + DEB
build_windows.bat           Nuitka onefile EXE
```
