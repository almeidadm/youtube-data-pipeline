"""Human-friendly monitoring and logging for the YouTube collection application.

This module provides structured logging across the application with:
- Console output for real-time feedback
- File-based logs for audit trail and post-run analysis
- Structured information about collection progress, quota usage, and errors
- Timestamps, levels (DEBUG, INFO, WARNING, ERROR) for easy filtering
"""
import logging
import logging.handlers
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


# Global logger instance
_logger: Optional[logging.Logger] = None


def setup_monitoring(
    log_dir: str = "data/logs",
    level: int = logging.INFO,
    run_id: Optional[str] = None,
) -> logging.Logger:
    """Initialize the monitoring system with console and file handlers.
    
    Args:
        log_dir: Directory for log files (created if doesn't exist).
        level: Logging level (DEBUG, INFO, WARNING, ERROR).
        run_id: Optional run ID to include in log filename for traceability.
    
    Returns:
        Configured logger instance.
    """
    global _logger
    
    if _logger is not None:
        return _logger
    
    # Create log directory
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    # Create logger
    _logger = logging.getLogger("youtube_collector")
    _logger.setLevel(level)
    _logger.propagate = False
    
    # Console handler: human-friendly output
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_format = logging.Formatter(
        "[%(levelname)-8s] %(asctime)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(console_format)
    
    # File handler: detailed log with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if run_id:
        log_filename = f"collection_{timestamp}_{run_id[:8]}.log"
    else:
        log_filename = f"collection_{timestamp}.log"
    
    log_file = log_path / log_filename
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10_000_000,  # 10 MB
        backupCount=5,
    )
    file_handler.setLevel(logging.DEBUG)  # File captures everything
    file_format = logging.Formatter(
        "[%(levelname)-8s] %(asctime)s | %(funcName)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_format)
    
    # Add handlers
    _logger.addHandler(console_handler)
    _logger.addHandler(file_handler)
    
    return _logger


def get_logger() -> logging.Logger:
    """Get the global logger instance, initializing if needed."""
    global _logger
    if _logger is None:
        _logger = setup_monitoring()
    return _logger


def log_collection_start(run_id: str, incremental: bool) -> None:
    """Log the start of a collection run."""
    logger = get_logger()
    mode = "incremental" if incremental else "full"
    logger.info(f"Starting {mode} collection run: {run_id}")


def log_collection_end(run_id: str, status: str, error: Optional[str] = None) -> None:
    """Log the end of a collection run."""
    logger = get_logger()
    if status == "success":
        logger.info(f"Collection run {run_id} completed successfully")
    else:
        logger.error(f"Collection run {run_id} failed: {error}")


def log_channel_processing(channel_id: str, channel_name: Optional[str] = None) -> None:
    """Log when processing a channel."""
    logger = get_logger()
    info = f"channel_id={channel_id}"
    if channel_name:
        info += f" (title: {channel_name})"
    logger.info(f"Processing {info}")


def log_channel_summary(
    channel_id: str,
    videos_count: int,
    quota_used: int,
) -> None:
    """Log summary of a channel processing."""
    logger = get_logger()
    logger.info(
        f"Channel {channel_id}: collected {videos_count} videos, "
        f"quota used: {quota_used}"
    )


def log_request(
    resource: str,
    method: str = "GET",
    params: Optional[dict] = None,
    quota_estimate: int = 0,
) -> None:
    """Log an API request."""
    logger = get_logger()
    param_str = ""
    if params:
        key_params = {k: v for k, v in params.items() if k in ["id", "q", "playlistId", "videoId"]}
        if key_params:
            param_str = " | " + ", ".join(f"{k}={v}" for k, v in key_params.items())
    logger.debug(f"API {method} {resource}{param_str} [quota: {quota_estimate}]")


def log_retry(
    resource: str,
    reason: str,
    attempt: int,
    backoff_seconds: int,
) -> None:
    """Log a retry attempt."""
    logger = get_logger()
    logger.warning(
        f"Retrying {resource} (attempt {attempt}): {reason}, "
        f"backing off {backoff_seconds}s"
    )


def log_quota_checkpoint(quota_used: int, quota_limit: int) -> None:
    """Log quota usage checkpoint."""
    logger = get_logger()
    percentage = (quota_used / quota_limit * 100) if quota_limit > 0 else 0
    logger.info(f"Quota checkpoint: {quota_used}/{quota_limit} ({percentage:.1f}%)")


def log_quota_limit_reached(quota_used: int, quota_limit: int, soft_limit: int) -> None:
    """Log when quota limit is reached."""
    logger = get_logger()
    logger.error(
        f"Quota limit reached: {quota_used} used, "
        f"soft limit was {soft_limit}, hard limit is {quota_limit}"
    )


def log_error(message: str, exception: Optional[Exception] = None) -> None:
    """Log an error with optional exception details."""
    logger = get_logger()
    if exception:
        logger.error(f"{message}: {type(exception).__name__}: {exception}")
    else:
        logger.error(message)


def log_info(message: str) -> None:
    """Log an informational message."""
    logger = get_logger()
    logger.info(message)


def get_log_files_info() -> dict:
    """Return information about current log files."""
    log_dir = Path("data/logs")
    if not log_dir.exists():
        return {"log_dir": str(log_dir), "exists": False, "files": []}
    
    files = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {
        "log_dir": str(log_dir),
        "exists": True,
        "files": [
            {
                "name": f.name,
                "size_bytes": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            }
            for f in files
        ],
    }
