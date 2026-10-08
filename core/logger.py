"""
CENTRALIZED LOGGING MODULE
Configuration unique (logger racine) : un seul RotatingFileHandler, partagé par tous les modules.
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_configured = False


def _setup_root() -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    log_file = Path(os.getenv("LOG_FILE", Path(__file__).parent.parent / "app_unified.log"))
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    ch.setFormatter(fmt)
    root.addHandler(fh)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)      # 1 ligne/10 s par tâche : bruit sans valeur
    root.addHandler(ch)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    _setup_root()
    return logging.getLogger(name)


app_logger = get_logger("AUTODO")
