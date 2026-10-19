#!/usr/bin/env python3
"""dsh-shell — a frameless pywebview shell for DeepSeek Harness."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import queue
import signal
import subprocess
import sys
import threading
from typing import Any

from loguru import logger

from dsh_shell.dsh_runtime import DshRuntime, _pid_alive
from dsh_shell.ipc import OPEN_SETTINGS, QUIT, SHOW_MAIN, TRAY_UNAVAILABLE
from dsh_shell.logging_setup import setup_logging
from dsh_shell.mcp_config import McpConfigManager
from dsh_shell.paths import runtime_file
from dsh_shell.single_instance import SingleInstance
from dsh_shell.skills import SkillsManager
from dsh_shell.tray import TrayHost
from dsh_shell.version import __version__


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dsh-shell",
        description="Frameless DeepSeek Harness desktop shell with MCP and skills management.",
    )
    parser.add_argument("--url", help="reuse an explicit dsh web URL (token URLs supported)")
    parser.add_argument("--port", type=int, default=3080, help="preferred dsh web port (default: 3080)")
    parser.add_argument("--profile", help="DeepSeek Harness profile (default: web)")
    parser.add_argument("--dsh-command", help="dsh executable/command; the profile name is appended")
    parser.add_argument("--no-reuse", action="store_true", help="do not probe or reuse an existing dsh web")
    parser.add_argument("--no-spawn", action="store_true", help="never spawn dsh; require --url or an existing service")
    parser.add_argument("--no-tray", action="store_true", help="run the webview in this process without a tray")
    parser.add_argument("--debug", action="store_true", help="verbose logs and webview devtools")
    parser.add_argument("--smoke-test", action="store_true", help="check runtime, MCP and skills without opening a window")
    parser.add_argument("--instance-port", type=int, default=int(os.environ.get("DSH_SHELL_INSTANCE_PORT", "47831")))
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def _config_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "url": args.url,
        "port": args.port,
        "profile": args.profile,
        "dsh_command": args.dsh_command,
        "reuse": not args.no_reuse,
        "spawn": not args.no_spawn,
        "debug": args.debug,
        "instance_port": args.instance_port,
    }


def _webview_child(config: dict[str, Any], commands: Any) -> None:
    setup_logging(
        bool(config.get("debug", False)),
        console=bool(config.get("debug", False)) or not _is_frozen(),
    )
    from dsh_shell.webview_app import run_webview_process

    try:
        run_webview_process(config, commands)
    except Exception:
        logger.exception("Webview process crashed")
        raise


def _cleanup_owned_dsh() -> None:
    """Best-effort cleanup when the GUI child was force-terminated."""
    try:
        record = json.loads(runtime_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    pid = record.get("pid")
    if not record.get("owned") or not isinstance(pid, int) or not _pid_alive(pid):
        return
    logger.info("Terminating owned dsh process pid={}", pid)
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        else:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=8,
                check=False,
            )
    except (OSError, subprocess.SubprocessError):
        pass


def run_smoke(config: dict[str, Any]) -> int:
    setup_logging(bool(config.get("debug", False)), console=True)
    runtime = DshRuntime(
        explicit_url=config.get("url"),
        reuse=bool(config.get("reuse", True)),
        spawn=bool(config.get("spawn", True)),
        profile=config.get("profile"),
        preferred_port=config.get("port", 3080),
        dsh_command=config.get("dsh_command"),
    )
    endpoint = None
    error = None
    try:
        endpoint = runtime.ensure()
    except Exception as exc:
        error = str(exc)
    mcp = McpConfigManager(profile=runtime.profile)
    skills = SkillsManager()
    try:
        mcp_data = mcp.list()
    except Exception as exc:
        mcp_data = {"path": str(mcp.path), "error": str(exc), "entries": [], "profiles": []}
    try:
        skills_data = skills.list()
    except Exception as exc:
        skills_data = {"root": str(skills.root), "error": str(exc), "counts": {}, "skills": []}
    payload = {
        "ok": bool(endpoint and not error),
        "version": __version__,
        "runtime": {
            "endpoint": endpoint.as_dict() if endpoint else None,
            "profile": runtime.profile,
            "error": error,
        },
        "mcp": {
            "profile": mcp.profile,
            "path": mcp_data.get("path"),
            "profiles": mcp_data.get("profiles", []),
            "entries": len(mcp_data.get("entries", [])),
            "error": mcp_data.get("error"),
        },
        "skills": {
            "root": skills_data.get("root"),
            "counts": skills_data.get("counts", {}),
            "error": skills_data.get("error"),
        },
    }
    runtime.stop()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


def _start_tray(config: dict[str, Any], instance: SingleInstance) -> int:
    ctx = multiprocessing.get_context("spawn")
    commands = ctx.Queue()
    child_lock = threading.Lock()
    child: multiprocessing.Process | None = None
    quitting = threading.Event()

    def start_child() -> multiprocessing.Process:
        process = ctx.Process(
            target=_webview_child,
            args=(config, commands),
            name="dsh-shell-webview",
            daemon=False,
        )
        process.start()
        logger.info("Started webview child pid={}", process.pid)
        return process

    def ensure_child() -> None:
        nonlocal child
        with child_lock:
            if child is None or not child.is_alive():
                child = start_child()

    def send(command: str) -> None:
        try:
            commands.put(command)
        except Exception as error:
            logger.warning("Could not send {} to webview child: {}", command, error)

    def on_quit() -> None:
        quitting.set()

    ensure_child()
    assert child is not None

    tray = TrayHost(send, on_quit=on_quit, ensure_child=ensure_child)

    def hard_exit() -> None:
        _cleanup_owned_dsh()
        logger.info("Force exiting tray host")
        os._exit(0)

    def monitor() -> None:
        process = child
        if process is None:
            return
        process.join()
        if quitting.is_set():
            return
        if process.exitcode == 0:
            logger.info("Webview child closed normally")
        else:
            logger.warning("Webview child exited unexpectedly (code={})", process.exitcode)
        # The tray loop can fail to return after a child-driven shutdown;
        # never leave the tray host orphaned.
        threading.Timer(2.0, hard_exit).start()
        tray.stop()

    threading.Thread(target=monitor, name="webview-monitor", daemon=True).start()

    def instance_command(command: str) -> None:
        mapping = {
            "show": SHOW_MAIN,
            "settings": OPEN_SETTINGS,
            "quit": QUIT,
        }
        send(mapping.get(command, SHOW_MAIN))

    instance.start_server(instance_command)
    tray_ok = tray.run()
    logger.info("Tray loop ended (started={})", tray_ok)
    if not tray_ok:
        logger.warning("No system tray available; close buttons will quit instead of hiding")
        send(TRAY_UNAVAILABLE)
        with child_lock:
            process = child
        if process is not None:
            process.join()

    quitting.set()
    send(QUIT)
    with child_lock:
        process = child
    if process is not None:
        process.join(timeout=10)
        if process.is_alive():
            logger.warning("Webview child did not exit; terminating")
            process.terminate()
            process.join(timeout=5)
            _cleanup_owned_dsh()
    try:
        commands.close()
    except Exception:
        pass
    return 0


def _run_direct(config: dict[str, Any], instance: SingleInstance) -> int:
    commands: queue.Queue[str] = queue.Queue()
    commands.put(TRAY_UNAVAILABLE)

    def instance_command(command: str) -> None:
        mapping = {"show": SHOW_MAIN, "settings": OPEN_SETTINGS, "quit": QUIT}
        commands.put(mapping.get(command, SHOW_MAIN))

    instance.start_server(instance_command)
    from dsh_shell.webview_app import run_webview_process

    try:
        return run_webview_process(config, commands)
    finally:
        instance.close()


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    args = parse_args(argv)
    config = _config_from_args(args)
    setup_logging(
        args.debug,
        console=args.debug or not _is_frozen(),
    )
    logger.info("dsh-shell v{} starting (pid={})", __version__, os.getpid())

    if args.smoke_test:
        return run_smoke(config)

    instance = SingleInstance(args.instance_port)
    if not instance.acquire():
        logger.info("Another dsh-shell instance is running")
        if instance.notify_primary("show"):
            return 0
        logger.warning("Primary instance did not answer on port {}", args.instance_port)
        return 2

    try:
        if args.no_tray:
            return _run_direct(config, instance)
        return _start_tray(config, instance)
    finally:
        instance.close()
        logger.info("dsh-shell stopped")


if __name__ == "__main__":
    sys.exit(main())
