"""
AirPaste - Production Logging System
Dual output: console + rotating file handlers for runtime.log and errors.log
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler

_initialized = False


def setup_logging(config: dict, app_root: str):
    """Configure production logging with file rotation and console output."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    log_cfg = config.get("logging", {})
    console_level = getattr(logging, log_cfg.get("console_level", "INFO").upper(), logging.INFO)
    file_level = getattr(logging, log_cfg.get("file_level", "DEBUG").upper(), logging.DEBUG)
    max_bytes = log_cfg.get("max_file_size_mb", 5) * 1024 * 1024
    backup_count = log_cfg.get("backup_count", 3)

    debug_mode = config.get("app", {}).get("debug_mode", False)
    if debug_mode:
        console_level = logging.DEBUG

    logs_dir = os.path.join(app_root, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s | %(name)-30s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    # Clear existing handlers
    root.handlers.clear()

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(console_level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # Runtime file handler (all levels)
    rh = RotatingFileHandler(
        os.path.join(logs_dir, "runtime.log"),
        maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8",
    )
    rh.setLevel(file_level)
    rh.setFormatter(fmt)
    root.addHandler(rh)

    # Error file handler (ERROR+ only)
    eh = RotatingFileHandler(
        os.path.join(logs_dir, "errors.log"),
        maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8",
    )
    eh.setLevel(logging.ERROR)
    eh.setFormatter(fmt)
    root.addHandler(eh)

    # Suppress noisy third-party loggers
    for name in ["PIL", "matplotlib", "tensorflow", "absl", "mediapipe"]:
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger("AirPaste").info("Logging system initialized")
