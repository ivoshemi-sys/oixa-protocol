"""
Persistent logging setup for OIXA Protocol.
Writes to LOG_DIR/oixa.log (rotating, 10MB max, 5 backups).
Also outputs to console via rich.
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from rich.logging import RichHandler
from config import LOG_DIR, OIXA_DEBUG


def setup_logging():
    log_dir = Path(LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "oixa.log"

    level = logging.DEBUG if OIXA_DEBUG else logging.INFO

    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers
    root.handlers.clear()

    # Console handler via rich
    console_handler = RichHandler(
        rich_tracebacks=True,
        show_time=True,
        show_path=OIXA_DEBUG,
    )
    console_handler.setLevel(level)
    root.addHandler(console_handler)

    # Rotating file handler
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    file_handler.setFormatter(file_formatter)
    root.addHandler(file_handler)

    # Quieten noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    logger = logging.getLogger("oixa")
    logger.info(f"Logging initialized — log file: {log_file}")
    return logger
