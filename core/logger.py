"""
CENTRALIZED LOGGING MODULE
Provides a consistent logger for the entire application
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_logger(name: str) -> logging.Logger:
    """
    Get or create a logger with the specified name.
    All loggers share the same handlers and format.
    
    Args:
        name: The logger name (typically __name__)
        
    Returns:
        A configured logger instance
    """
    logger = logging.getLogger(name)
    
    # Only configure once (if this is the first call)
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        
        # Define formatter
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-4s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        
        # File handler with rotation
        log_file = Path(__file__).parent.parent / "app_unified.log"
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    return logger


# Root logger for the application
app_logger = get_logger("AUTODO")
