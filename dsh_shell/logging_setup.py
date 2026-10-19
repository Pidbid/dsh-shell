"""loguru configuration shared by the tray host and the webview process."""

from __future__ import annotations

import logging
import sys

from loguru import logger

from .paths import APP_NAME, ensure_data_dirs, logs_dir


class _InterceptHandler(logging.Handler):
    """Route stdlib/pywebview logs through loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(debug: bool = False, console: bool = True) -> None:
    ensure_data_dirs()
    logger.remove()
    if console:
        logger.add(
            sys.stderr,
            level="DEBUG" if debug else "INFO",
            colorize=sys.stderr.isatty(),
            backtrace=debug,
            diagnose=debug,
        )
    logger.add(
        logs_dir() / f"{APP_NAME}.log",
        level="DEBUG" if debug else "INFO",
        rotation="5 MB",
        retention=3,
        encoding="utf-8",
        enqueue=True,
        backtrace=debug,
        diagnose=debug,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {process.name}:{thread.name} | "
            "{name}:{function}:{line} | {message}"
        ),
    )
    logging.basicConfig(handlers=[_InterceptHandler()], level=logging.DEBUG if debug else logging.INFO)
    for noisy in ("urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
