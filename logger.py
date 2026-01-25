"""
Centralized Logging Configuration

Provides a logger that outputs to console by default.
File logging can be enabled via enable_file_logging() for production use.
Log files are stored in the logs/ directory with Eastern time timestamps.
"""

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo


# Eastern timezone for timestamps
ET = ZoneInfo("America/New_York")

# Log directory
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

# Cache of loggers
_loggers: dict[str, logging.Logger] = {}

# Global file logging state
_file_logging_enabled = False
_log_file_path: str | None = None


class EasternTimeFormatter(logging.Formatter):
    """Custom formatter that uses Eastern time for timestamps."""
    
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=ET)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%H:%M:%S")


def _get_formatter() -> EasternTimeFormatter:
    """Get the standard formatter for all handlers."""
    return EasternTimeFormatter(
        fmt="[%(asctime)s] [%(name)s] %(message)s",
        datefmt="%H:%M:%S"
    )


def _ensure_log_dir():
    """Ensure the logs directory exists."""
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)


def get_daily_log_path() -> str:
    """
    Get a unique log file path for this run (Eastern time).
    
    Each run creates a new log file with timestamp to distinguish
    multiple runs on the same day.
    
    Returns:
        Path to log file (logs/YYYY-MM-DD_HH-MM-SS.log)
    """
    _ensure_log_dir()
    now = datetime.now(ET).strftime("%Y-%m-%d_%H-%M-%S")
    return os.path.join(LOG_DIR, f"{now}.log")


def enable_file_logging(log_file: str = None):
    """
    Enable file logging for all loggers (existing and future).
    
    Call this at the start of your application (e.g., in main_today.py)
    to enable logging to a file in addition to console output.
    
    Args:
        log_file: Path to log file. If None, uses daily log file (logs/YYYY-MM-DD.log)
    """
    global _file_logging_enabled, _log_file_path
    
    if _file_logging_enabled:
        return  # Already enabled
    
    _file_logging_enabled = True
    _log_file_path = log_file or get_daily_log_path()
    
    # Add file handler to all existing loggers
    formatter = _get_formatter()
    file_handler = logging.FileHandler(_log_file_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    
    for logger in _loggers.values():
        logger.addHandler(file_handler)


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Create or retrieve a configured logger.
    
    Loggers output to console by default. Call enable_file_logging() 
    at application startup to also write to a file.
    
    Args:
        name: Logger name (e.g., 'ENGINE', 'PAPER', 'SCHWAB')
        level: Logging level (default: INFO)
        
    Returns:
        Configured logger instance
    """
    # Return cached logger if exists
    if name in _loggers:
        return _loggers[name]
    
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Prevent duplicate handlers
    if logger.handlers:
        _loggers[name] = logger
        return logger
    
    formatter = _get_formatter()
    
    # Console handler (always added)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler (only if file logging is enabled)
    if _file_logging_enabled and _log_file_path:
        file_handler = logging.FileHandler(_log_file_path, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    # Prevent propagation to root logger
    logger.propagate = False

    # Cache the logger
    _loggers[name] = logger

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get an existing logger or create a new one.
    
    This is an alias for setup_logger for convenience.
    
    Args:
        name: Logger name
        
    Returns:
        Logger instance
    """
    return setup_logger(name)


def get_log_file_path() -> str | None:
    """
    Get the current log file path if file logging is enabled.
    
    Returns:
        Path to log file, or None if file logging is not enabled.
    """
    return _log_file_path
