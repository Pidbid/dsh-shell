<div align="right">

[English](README.en.md) · **简体中文**

</div>

<p align="center">
  <img src="assets/icon-128.png" width="88" alt="dsh-shell" />
</p>

<h1 align="center">dsh-shell</h1>

<p align="center"><b>DeepSeek Harness 的桌面外壳</b> · 无边框窗口、托盘常驻、MCP 与 Skills 一站式管理</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" />
  <img alt="pywebview" src="https://img.shields.io/badge/pywebview-6.1%2B-4B8BBE" />
  <img alt="平台" src="https://img.shields.io/badge/平台-Linux%20%7C%20Windows-6E7681" />
  <img alt="版本" src="https://img.shields.io/badge/版本-0.0.1-2EA043" />
</p>

<p align="center">
  <img src="docs/screenshots/main-window.png" alt="dsh-shell 主窗口" width="900" />
</p>

## ✨ 特性

- 🪟 **无边框原生外壳**：自绘 34px 标题栏与八向缩放手柄，兼容 X11 / Wayland / Windows。
- 🔁 **复用已有服务**：优先接管 `127.0.0.1:3080` 上的 `dsh web`（含 Token 鉴权），否则自动拉起或退回 `npx` / `pnpm` / `npm`。
- 🧩 **MCP 编辑器**：直接编辑官方 `cordis.patch.yml`，保留注释、未知字段与 `!!js` 表达式，表单与原始 YAML 双模式。
- 🛠️ **Skills 管理器**：目录包与平铺 Markdown 统一管理，支持启用/停用、frontmatter 编辑与安全删除。
- 🖥️ **托盘常驻**：显示/隐藏、重载、浏览器打开、重启 DSH、退出；无托盘环境自动退化为关闭即退出。
- 📦 **一键分发包**：Nuitka onefile，Linux 提供 DEB，Windows 提供单一 EXE。

## 🖼️ 界面

<p align="center">
  <img src="docs/screenshots/settings-overview.png" alt="设置 · 概览" width="640" />
</p>

| MCP 服务器 | Skills |
| :---: | :---: |
| ![MCP 管理](docs/screenshots/settings-mcp.png) | ![Skills 管理](docs/screenshots/settings-skills.png) |

## 🚀 快速开始

需要 **Python 3.12** 与 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync --group dev

# 启动外壳（无边框窗口 + 托盘）
uv run main.py

# 调试模式：详细日志 + 127.0.0.1:9229 远程调试
uv run main.py --debug

# 不启用托盘，直接在调试器里运行
uv run main.py --no-tray --debug

# 后端自检（runtime / MCP / skills）
uv run main.py --smoke-test
```

已有一个运行中的 `dsh web`？外壳会直接复用；也可以显式指定：

```bash
uv run main.py --url "http://127.0.0.1:3080/?token=..."
```

### ⌨️ 常用参数

| 参数 | 说明 |
| --- | --- |
| `--url URL` | 复用指定的 `dsh web`（支持带 Token 的 URL） |
| `--port PORT` | 首选 `dsh web` 端口，默认 `3080` |
| `--profile NAME` | Harness profile，默认 `web` |
| `--no-reuse` | 不探测、不复用已有服务 |
| `--no-spawn` | 禁止自动拉起，必须提供 `--url` 或已有服务 |
| `--no-tray` | 不启动托盘宿主进程 |
| `--debug` | 详细日志并开启 CDP 调试端口 `9229` |
| `--smoke-test` | 打印 runtime / MCP / skills 状态后退出 |

> [!TIP]
> 服务返回 `401` 时，外壳会依次从环境变量、`runtime.json`、supervisor 日志、`/proc` 与 `journalctl` 中找回 Token，验证通过后才复用。
>
> 日志位于 `~/.local/share/dsh-shell/logs/dsh-shell.log`（Windows 为 `%APPDATA%`）。

## 🧩 MCP

Harness 的 MCP 扩展面是 profile 的 patch 层：

```text
$DSH_HOME/profiles/<profile>/cordis.patch.yml
```

每个服务器是一条 loader 条目：

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

保存时保留注释与未触及的 `!!js` 标签，校验 `serverName`、transport、超时等字段，拒绝重复 id / 重复 `serverName`，并以 `.bak` + 原子写入落盘。`web` profile 开启 `patchReload: live`，改完即生效。

## 🛠️ Skills

Harness 会扫描以下位置：

```text
~/.agents/skills/<name>/SKILL.md   # 目录包
~/.agents/skills/<name>.md         # 平铺文件
```

`SKILL.md` 需要包含 `name`（kebab-case）与 `description` 的 YAML frontmatter，可选 `whenToUse`、`disable-model-invocation`、`user-invocable`。停用会把条目移到 `skills-disabled/`，删除则移入 `~/.local/share/dsh-shell/trash/skills/`，随时可以手动找回。

## 📦 构建

| 平台 | 命令 | 产物 |
| --- | --- | --- |
| Linux | `./build_linux.sh` | `dist/dsh-shell`、`dist/dsh-shell_<version>_amd64.deb` |
| Windows | `build_windows.bat` | `dist/dsh-shell.exe` |

推送 `v*` 标签会触发 GitHub Actions：校验版本号、跑 lint 与测试、构建三个产物并生成 Release。

```bash
git tag v0.0.1
git push origin v0.0.1
```

图标由 `scripts/make_icons.py` 从 `assets/icon-source.png` 重新生成：

```bash
uv run python scripts/make_icons.py
```

## 🧪 质量

```bash
uv run pytest        # 测试套件
uv run ruff check .  # 静态检查
```

## 📁 目录结构

```text
main.py                 入口：单实例、托盘宿主、子进程
dsh_shell/
  dsh_runtime.py        服务复用 / 探测 / Token 发现 / 启停
  mcp_config.py         cordis.patch.yml 读写与校验
  skills.py             Skills 发现与增删改
  api.py                pywebview JS API
  webview_app.py        窗口、chrome 注入、命令循环
  tray.py / single_instance.py / paths.py
ui/                     标题栏注入脚本与设置界面
tests/                  pytest 回归套件
docs/screenshots/       README 截图
```
