"""Cross-platform single-instance guard with a tiny local command socket."""

from __future__ import annotations

import os
import socket
import threading
from collections.abc import Callable

from loguru import logger

from .paths import data_dir

INSTANCE_PORT = int(os.environ.get("DSH_SHELL_INSTANCE_PORT", "47831"))


class SingleInstance:
    """Try to become the primary instance; a secondary instance pings it."""

    def __init__(self, port: int = INSTANCE_PORT) -> None:
        self.port = port
        self._lock_handle = None
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def acquire(self) -> bool:
        data_dir().mkdir(parents=True, exist_ok=True)
        lock_path = data_dir() / "instance.lock"
        try:
            self._lock_handle = lock_path.open("a+")
        except OSError as error:
            logger.warning("Could not open instance lock: {}", error)
            return True
        try:
            if os.name == "nt":
                import msvcrt

                self._lock_handle.seek(0)
                if self._lock_handle.read(1) == "":
                    self._lock_handle.write("0")
                    self._lock_handle.flush()
                self._lock_handle.seek(0)
                msvcrt.locking(self._lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_handle.seek(0)
            self._lock_handle.truncate()
            self._lock_handle.write(str(os.getpid()))
            self._lock_handle.flush()
            return True
        except OSError:
            self._lock_handle.close()
            self._lock_handle = None
            return False

    def notify_primary(self, command: str = "show", timeout: float = 1.5) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as sock:
                sock.sendall(f"{command}\n".encode())
            return True
        except OSError:
            return False

    def start_server(self, callback: Callable[[str], None]) -> bool:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("127.0.0.1", self.port))
            server.listen(8)
            server.settimeout(0.5)
        except OSError as error:
            logger.warning("Could not start instance command socket on {}: {}", self.port, error)
            server.close()
            return False
        self._server = server

        def _serve() -> None:
            while not self._stop.is_set():
                try:
                    connection, _ = server.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                try:
                    with connection:
                        connection.settimeout(1.0)
                        data = connection.recv(256).decode("utf-8", "replace").strip()
                        if data:
                            logger.debug("Instance command received: {}", data)
                            callback(data)
                except OSError:
                    continue

        self._thread = threading.Thread(target=_serve, name="instance-server", daemon=True)
        self._thread.start()
        return True

    def close(self) -> None:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None
        if self._lock_handle is not None:
            try:
                if os.name == "nt":
                    import msvcrt

                    self._lock_handle.seek(0)
                    msvcrt.locking(self._lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                self._lock_handle.close()
            except OSError:
                pass
            self._lock_handle = None
