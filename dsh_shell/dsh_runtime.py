"""Discover, reuse, or launch a DeepSeek Harness web endpoint.

The shell is intentionally decoupled from the harness: it only talks HTTP and
reads the documented ``$DSH_HOME`` layout.  Existing ``dsh web`` servers are
preferred (including the conventional port 3080); otherwise the configured
``dsh`` binary, or a package runner (npx / pnpm / npm), is used to start one.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any
from urllib.parse import quote, urlsplit

from loguru import logger

from .paths import (
    data_dir,
    dsh_home,
    profile_patch_file,
    runtime_file,
    skills_root,
)

DEFAULT_PORT = 3080
_TOKEN_RE = re.compile(r"token=([A-Za-z0-9_\-]{8,})")
_DSH_URL_RE = re.compile(r"dsh web:\s+(https?://\S+)")
_ANY_LOCAL_URL_RE = re.compile(r"(https?://(?:127\.0\.0\.1|localhost):\d+/?\?token=[A-Za-z0-9_\-]+)")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


class DshError(RuntimeError):
    """Base class for harness startup failures."""


class DshStartError(DshError):
    """No usable harness endpoint could be found or started."""


@dataclass
class DshEndpoint:
    url: str
    port: int
    token: str | None = None
    reused: bool = False
    owned: bool = False
    source: str = "existing"
    launcher: list[str] = field(default_factory=list)
    profile: str = "web"
    pid: int | None = None

    def as_dict(self, mask_secret: bool = True) -> dict[str, Any]:
        token = self.token
        if mask_secret and token:
            token = f"{token[:4]}…{token[-4:]}"
        return {
            "url": mask_url(self.url) if mask_secret else self.url,
            "port": self.port,
            "token": token,
            "reused": self.reused,
            "owned": self.owned,
            "source": self.source,
            "launcher": self.launcher,
            "profile": self.profile,
            "pid": self.pid,
            "healthy": self.healthy,
        }

    @property
    def healthy(self) -> bool:
        return probe_url(self.url)[0] in (200, 303)


def mask_url(url: str) -> str:
    if "token=" not in url:
        return url
    head, _, _ = url.partition("token=")
    return f"{head}token=***"


def port_from_url(url: str) -> int:
    split = urlsplit(url)
    if split.port:
        return split.port
    return 443 if split.scheme == "https" else 80


def probe_url(url: str, timeout: float = 2.0) -> tuple[int, str | None]:
    """Return ``(status, location)`` without following redirects.

    ``401`` means a dsh web token is required; ``303`` means a token URL was
    accepted and the browser has been handed the auth cookie.
    """
    split = urlsplit(url)
    if split.scheme not in ("http", "https"):
        return 0, None
    host = split.hostname or "127.0.0.1"
    port = split.port or (443 if split.scheme == "https" else 80)
    path = split.path or "/"
    if split.query:
        path = f"{path}?{split.query}"
    connection_cls = http.client.HTTPSConnection if split.scheme == "https" else http.client.HTTPConnection
    connection = connection_cls(host, port, timeout=timeout)
    try:
        connection.request("GET", path, headers={"User-Agent": "dsh-shell/1"})
        response = connection.getresponse()
        response.read(512)
        return response.status, response.getheader("Location")
    except (OSError, http.client.HTTPException):
        return 0, None
    finally:
        connection.close()


def build_token_url(port: int, token: str) -> str:
    return f"http://127.0.0.1:{port}/?token={quote(token, safe='-_')}"


def _read_tail(path: Path, size: int = 65536) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            length = handle.tell()
            handle.seek(max(0, length - size))
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _candidate_log_files() -> list[Path]:
    candidates: list[Path] = [
        Path("/var/log/supervisor/dsh-web.log"),
        Path("/var/log/supervisor/dsh-web.err.log"),
        Path.home() / "dsh-web.log",
        Path.home() / "dsh-web.err.log",
        dsh_home() / "logs" / "web.log",
        dsh_home() / "web.log",
        data_dir() / "dsh-web.log",
    ]
    # Supervisord configurations may point at a rotated/custom log location.
    for conf in (
        Path("/etc/supervisor/supervisord.conf"),
        Path("/etc/supervisord.conf"),
        *sorted(Path("/etc/supervisor/conf.d").glob("*.conf")),
        *sorted(Path.home().joinpath(".config/supervisor").glob("*.conf")),
    ):
        text = _read_tail(conf, 32768)
        for match in re.finditer(r"^\s*stdout_logfile\s*=\s*(\S+)", text, re.MULTILINE):
            candidates.append(Path(match.group(1)).expanduser())
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _tokens_from_text(text: str, port: int | None) -> list[str]:
    tokens: list[str] = []
    for match in re.finditer(rf"https?://(?:127\.0\.0\.1|localhost):{port or r'\d+'}/?\?token=([A-Za-z0-9_\-]{{8,}})", text):
        tokens.append(match.group(1))
    for match in re.finditer(r"token=([A-Za-z0-9_\-]{8,})", text):
        tokens.append(match.group(1))
    return tokens


def _find_listener_pids(port: int) -> list[int]:
    """Best-effort POSIX listener lookup without adding psutil."""
    pids: list[int] = []
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text(encoding="utf-8", errors="replace").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A":
                continue
            try:
                local_port = int(fields[1].rsplit(":", 1)[1], 16)
            except (ValueError, IndexError):
                continue
            if local_port != port:
                continue
            inode = fields[9]
            for proc in Path("/proc").iterdir():
                if not proc.name.isdigit():
                    continue
                fd_dir = proc / "fd"
                try:
                    for fd in fd_dir.iterdir():
                        try:
                            if os.readlink(fd) == f"socket:[{inode}]":
                                pids.append(int(proc.name))
                                break
                        except OSError:
                            continue
                except OSError:
                    continue
    return sorted(set(pids))


def _process_stdout_files(pids: list[int]) -> list[Path]:
    files: list[Path] = []
    for pid in pids:
        link = Path(f"/proc/{pid}/fd/1")
        try:
            target = Path(os.readlink(link))
        except OSError:
            continue
        if target.is_file():
            files.append(target)
    return files


def find_web_token(port: int) -> str | None:
    """Locate a usable dsh web token for an already running server."""
    env_token = os.environ.get("DSH_WEB_TOKEN") or os.environ.get("DSH_WEB_AUTH_TOKEN")
    candidates: list[str] = []
    if env_token:
        candidates.append(env_token)

    saved = _load_runtime_record()
    if saved and saved.get("port") == port and saved.get("token"):
        candidates.append(str(saved["token"]))

    for path in _candidate_log_files():
        candidates.extend(reversed(_tokens_from_text(_read_tail(path), port)))

    for path in _process_stdout_files(_find_listener_pids(port)):
        candidates.extend(reversed(_tokens_from_text(_read_tail(path), port)))

    if shutil.which("journalctl"):
        try:
            result = subprocess.run(
                ["journalctl", "-u", "dsh-web", "-n", "300", "--no-pager", "-o", "cat"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            candidates.extend(reversed(_tokens_from_text(result.stdout, port)))
        except (OSError, subprocess.SubprocessError):
            pass

    seen: set[str] = set()
    for token in candidates:
        if not token or token in seen:
            continue
        seen.add(token)
        status, _ = probe_url(build_token_url(port, token))
        if status in (200, 303):
            logger.info("Reusing dsh web token from local runtime/logs on port {}", port)
            return token
    return None


def _load_runtime_record() -> dict[str, Any] | None:
    try:
        return json.loads(runtime_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_runtime_record(endpoint: DshEndpoint) -> None:
    try:
        runtime_file().parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "url": endpoint.url,
            "port": endpoint.port,
            "token": endpoint.token,
            "pid": endpoint.pid,
            "profile": endpoint.profile,
            "launcher": endpoint.launcher,
            "owned": endpoint.owned,
            "saved_at": time.time(),
        }
        runtime_file().write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if os.name == "posix":
            os.chmod(runtime_file(), 0o600)
    except OSError as error:
        logger.warning("Could not persist dsh runtime endpoint: {}", error)


def _clear_runtime_record(pid: int | None = None) -> None:
    record = _load_runtime_record()
    if record and pid is not None and record.get("pid") not in (None, pid):
        return
    try:
        runtime_file().unlink()
    except OSError:
        pass


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "posix":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return str(pid) in result.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def _terminate_tree(process: subprocess.Popen[str], *, force: bool = False) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        args = ["taskkill", "/PID", str(process.pid), "/T"]
        if force:
            args.append("/F")
        try:
            subprocess.run(args, capture_output=True, timeout=8, check=False)
            return
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        if force:
            process.kill()
        else:
            process.terminate()
    except OSError:
        pass


def _port_is_free(port: int) -> bool:
    if port <= 0:
        return True
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _pick_port(preferred: int | None) -> int:
    if preferred is None:
        preferred = DEFAULT_PORT
    if preferred <= 0:
        return 0
    if _port_is_free(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _detect_running_profile() -> str | None:
    if os.name != "posix":
        return None
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            argv = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        args = [item.decode("utf-8", "replace") for item in argv if item]
        if not args or not any(name in Path(args[0]).name for name in ("dsh", "dsh.js", "bin.js")):
            continue
        if "web" not in args:
            continue
        for index, arg in enumerate(args):
            if arg == "--profile" and index + 1 < len(args):
                return args[index + 1]
        if "web" in args:
            return "web"
        known = set(list_profiles())
        for arg in args[1:]:
            if arg in known:
                return arg
        return "web"
    return None


def list_profiles() -> list[str]:
    root = dsh_home() / "profiles"
    profiles: list[str] = []
    try:
        for child in root.iterdir():
            if not child.is_dir() or child.name in ("node_modules", ".bin"):
                continue
            if (child / "cordis.yml").is_file() or (child / "cordis.patch.yml").is_file():
                profiles.append(child.name)
    except OSError:
        return []
    return sorted(profiles)


def _wrap_windows_script(command: list[str]) -> list[str]:
    if os.name != "nt" or not command:
        return command
    suffix = Path(command[0]).suffix.lower()
    if suffix in (".cmd", ".bat"):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", *command]
    return command


def _resolve_launcher(
    profile: str,
    dsh_command: str | None,
) -> tuple[list[str], str]:
    if dsh_command:
        base = shlex.split(dsh_command, posix=os.name != "nt")
        base = _wrap_windows_script(base)
        return [*base, profile], "explicit"

    for name in ("dsh", "dsh.cmd", "dsh.exe"):
        executable = shutil.which(name)
        if executable:
            return [*_wrap_windows_script([executable]), profile], "dsh"

    package = "@deepseek-ai/dsh@latest"
    if executable := shutil.which("npx"):
        return [*_wrap_windows_script([executable]), "-y", package, profile], "npx"
    if executable := shutil.which("pnpm"):
        return [*_wrap_windows_script([executable]), "dlx", package, profile], "pnpm"
    if executable := shutil.which("npm"):
        return [
            *_wrap_windows_script([executable]),
            "exec",
            "--yes",
            "--package",
            package,
            "--",
            "dsh",
            profile,
        ], "npm"
    raise DshStartError(
        "未找到 dsh、npx、pnpm 或 npm。请安装 Node.js/DeepSeek Harness，"
        "或使用 --dsh-command/--url 指定已有服务。"
    )


class DshRuntime:
    """Owns discovery, optional child process lifetime, and endpoint state."""

    def __init__(
        self,
        *,
        explicit_url: str | None = None,
        reuse: bool = True,
        spawn: bool = True,
        profile: str | None = None,
        preferred_port: int | None = DEFAULT_PORT,
        dsh_command: str | None = None,
        startup_timeout: float = 180.0,
    ) -> None:
        self.explicit_url = explicit_url.strip() if explicit_url else None
        self.reuse = reuse
        self.spawn_allowed = spawn
        self.profile = profile or os.environ.get("DSH_PROFILE") or _detect_running_profile() or "web"
        self.preferred_port = preferred_port
        self.dsh_command = dsh_command
        self.startup_timeout = startup_timeout
        self.endpoint: DshEndpoint | None = None
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.RLock()
        self._log_tail: list[str] = []
        self._last_error: str | None = None

    # ------------------------------------------------------------------ public
    def ensure(self) -> DshEndpoint:
        with self._lock:
            if self.endpoint and self.endpoint.healthy:
                return self.endpoint

            if self.explicit_url:
                endpoint = self._use_explicit_url(self.explicit_url)
                if endpoint:
                    self.endpoint = endpoint
                    return endpoint
                if not self.spawn_allowed:
                    raise DshStartError(self._last_error or f"无法连接 {self.explicit_url}")

            if self.reuse:
                endpoint = self._reuse_existing()
                if endpoint:
                    self.endpoint = endpoint
                    return endpoint

            if not self.spawn_allowed:
                raise DshStartError(
                    self._last_error
                    or "未发现可复用的 dsh web 服务，且已禁用自动启动。"
                    "请先运行 `dsh web`，或传入 --url。"
                )

            endpoint = self._spawn()
            self.endpoint = endpoint
            return endpoint

    def health(self) -> bool:
        if not self.endpoint:
            return False
        return self.endpoint.healthy

    def info(self) -> dict[str, Any]:
        endpoint = self.endpoint
        return {
            "endpoint": endpoint.as_dict() if endpoint else None,
            "profile": self.profile,
            "profiles": list_profiles(),
            "mcp_path": str(profile_patch_file(self.profile)),
            "skills_path": str(skills_root()),
            "dsh_home": str(dsh_home()),
            "error": self._last_error,
        }

    def restart(self) -> DshEndpoint:
        with self._lock:
            if self.endpoint and not self.endpoint.owned:
                raise DshStartError("当前连接的是外部复用的 dsh 服务，shell 不会重启它。")
            self.stop()
            self.reuse = False
            return self.ensure()

    def stop(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            if process and process.poll() is None:
                logger.info("Stopping dsh child process pid={}", process.pid)
                try:
                    if os.name == "posix":
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    else:
                        _terminate_tree(process)
                    process.wait(timeout=8)
                except (OSError, subprocess.TimeoutExpired):
                    if os.name == "posix":
                        try:
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        except OSError:
                            pass
                    else:
                        _terminate_tree(process, force=True)
            if self.endpoint and self.endpoint.owned:
                endpoint_pid = self.endpoint.pid
                if endpoint_pid and _pid_alive(endpoint_pid) and process is None:
                    logger.info("Stopping persisted owned dsh pid={}", endpoint_pid)
                    try:
                        if os.name == "posix":
                            os.killpg(os.getpgid(endpoint_pid), signal.SIGTERM)
                        else:
                            os.kill(endpoint_pid, signal.SIGTERM)
                    except OSError:
                        pass
                _clear_runtime_record(endpoint_pid)
            self.endpoint = None

    # ----------------------------------------------------------------- internals
    def _use_explicit_url(self, url: str) -> DshEndpoint | None:
        split = urlsplit(url)
        if not split.scheme:
            url = f"http://{url}"
            split = urlsplit(url)
        port = split.port or (443 if split.scheme == "https" else 80)
        token = None
        match = _TOKEN_RE.search(url)
        if match:
            token = match.group(1)
        status, location = probe_url(url)
        if status in (200, 303):
            self._last_error = None
            return DshEndpoint(
                url=url,
                port=port,
                token=token,
                reused=status != 200,
                owned=False,
                source="explicit",
                profile=self.profile,
            )
        if status == 401:
            token = token or find_web_token(port)
            if token:
                token_url = build_token_url(port, token)
                status, _ = probe_url(token_url)
                if status in (200, 303):
                    self._last_error = None
                    return DshEndpoint(
                        url=token_url,
                        port=port,
                        token=token,
                        reused=True,
                        owned=False,
                        source="explicit",
                        profile=self.profile,
                    )
            self._last_error = f"{url} 需要 dsh web token，但未能从本机日志中找到可用 token。"
        else:
            self._last_error = f"无法连接 {url}（HTTP {status or 'unreachable'}）。"
        return None

    def _reuse_existing(self) -> DshEndpoint | None:
        saved = _load_runtime_record()
        if saved and saved.get("url") and _pid_alive(saved.get("pid")):
            url = str(saved["url"])
            status, _ = probe_url(url)
            if status in (200, 303):
                logger.info("Reusing persisted dsh endpoint on port {}", saved.get("port"))
                saved_pid = saved.get("pid")
                return DshEndpoint(
                    url=url,
                    port=int(saved.get("port") or port_from_url(url)),
                    token=saved.get("token"),
                    reused=True,
                    owned=bool(saved.get("owned")) and isinstance(saved_pid, int),
                    source="runtime-record",
                    profile=str(saved.get("profile") or self.profile),
                    pid=saved_pid if isinstance(saved_pid, int) else None,
                )

        ports: list[int] = []
        if self.preferred_port and self.preferred_port > 0:
            ports.append(self.preferred_port)
        if DEFAULT_PORT not in ports:
            ports.append(DEFAULT_PORT)
        for port in ports:
            base = f"http://127.0.0.1:{port}/"
            status, _ = probe_url(base)
            if status == 200:
                logger.info("Reusing unauthenticated dsh web on port {}", port)
                self._last_error = None
                return DshEndpoint(
                    url=base,
                    port=port,
                    reused=True,
                    owned=False,
                    source="port-probe",
                    profile=self.profile,
                )
            if status == 401:
                token = find_web_token(port)
                if token:
                    token_url = build_token_url(port, token)
                    status, _ = probe_url(token_url)
                    if status in (200, 303):
                        logger.info("Reusing authenticated dsh web on port {}", port)
                        self._last_error = None
                        return DshEndpoint(
                            url=token_url,
                            port=port,
                            token=token,
                            reused=True,
                            owned=False,
                            source="port-probe",
                            profile=self.profile,
                        )
                self._last_error = (
                    f"端口 {port} 上已有 dsh web，但需要 token，且未能在日志中找到。"
                    " 可在设置中粘贴启动时打印的 URL/token。"
                )
        return None

    def _spawn(self) -> DshEndpoint:
        launcher, launcher_kind = _resolve_launcher(self.profile, self.dsh_command)
        port = _pick_port(self.preferred_port)
        command = [*launcher, "--host", "127.0.0.1", "--port", str(port), "--no-open"]
        logger.info("Starting DeepSeek Harness: {}", " ".join(shlex.quote(part) for part in command))
        env = os.environ.copy()
        env.setdefault("NO_COLOR", "1")
        env.setdefault("FORCE_COLOR", "0")
        env.setdefault("BROWSER", "none")
        env["PYTHONUNBUFFERED"] = "1"
        popen_kwargs: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
            "env": env,
        }
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        else:
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        try:
            process = subprocess.Popen(command, **popen_kwargs)
        except OSError as error:
            raise DshStartError(f"无法启动 {launcher_kind}: {error}") from error

        self._process = process
        self._log_tail = []
        ready = threading.Event()
        found: dict[str, str] = {}

        def _reader(stream: IO[str] | None) -> None:
            if stream is None:
                return
            for raw_line in stream:
                line = _ANSI_RE.sub("", raw_line.rstrip())
                if not line:
                    continue
                self._log_tail.append(line)
                del self._log_tail[:-80]
                logger.debug("dsh | {}", line)
                match = _DSH_URL_RE.search(line) or _ANY_LOCAL_URL_RE.search(line)
                if match and not ready.is_set():
                    url = match.group(1).rstrip(").,")
                    found["url"] = url
                    ready.set()
            if not ready.is_set():
                ready.set()

        reader = threading.Thread(target=_reader, args=(process.stdout,), name="dsh-stdout", daemon=True)
        reader.start()
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if ready.wait(0.25) and found.get("url"):
                break
            if process.poll() is not None:
                break

        url = found.get("url")
        status = 0
        if url:
            status, _ = probe_url(url, timeout=3.0)
            if status not in (200, 303):
                # The URL line can be printed before the HTTP listener is ready.
                for _ in range(40):
                    time.sleep(0.25)
                    status, _ = probe_url(url, timeout=2.0)
                    if status in (200, 303):
                        break
        if not url or status not in (200, 303):
            self._terminate_process(process)
            tail = "\n".join(self._log_tail[-15:]) or "无输出"
            raise DshStartError(
                f"dsh 启动失败或超时（{self.startup_timeout:.0f}s）。\n"
                f"命令: {' '.join(shlex.quote(part) for part in command)}\n{tail}"
            )

        token_match = _TOKEN_RE.search(url)
        endpoint = DshEndpoint(
            url=url,
            port=port_from_url(url),
            token=token_match.group(1) if token_match else None,
            reused=False,
            owned=True,
            source=launcher_kind,
            launcher=command,
            profile=self.profile,
            pid=process.pid,
        )
        _save_runtime_record(endpoint)
        self._last_error = None
        logger.info("dsh web ready at {} (pid={})", mask_url(url), process.pid)
        return endpoint

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            else:
                _terminate_tree(process)
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            if os.name == "posix":
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except OSError:
                    pass
            else:
                _terminate_tree(process, force=True)
