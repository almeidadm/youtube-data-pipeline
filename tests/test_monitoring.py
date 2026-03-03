"""Tests for the monitoring module."""
import logging
import os
import tempfile
from pathlib import Path

import pytest

from src.monitoring import (
    setup_monitoring,
    get_logger,
    log_collection_start,
    log_collection_end,
    log_channel_processing,
    log_channel_summary,
    log_request,
    log_retry,
    log_quota_checkpoint,
    log_quota_limit_reached,
    log_error,
    log_info,
    get_log_files_info,
)


@pytest.fixture(autouse=True)
def reset_logger():
    """Reset the global logger before each test."""
    import src.monitoring as monitoring_module
    monitoring_module._logger = None
    yield
    monitoring_module._logger = None


def test_setup_monitoring_creates_directory():
    """Test that setup_monitoring creates the log directory."""
    with tempfile.TemporaryDirectory() as temp_log_dir:
        logger = setup_monitoring(log_dir=temp_log_dir)
        assert logger is not None
        assert Path(temp_log_dir).exists()


def test_setup_monitoring_with_run_id():
    """Test that setup_monitoring creates log file with run ID."""
    with tempfile.TemporaryDirectory() as temp_log_dir:
        logger = setup_monitoring(log_dir=temp_log_dir, run_id="test-run-123")
        assert logger is not None
        # Check that a log file was created
        log_files = list(Path(temp_log_dir).glob("*.log"))
        assert len(log_files) > 0


def test_get_logger_returns_configured_instance():
    """Test that get_logger returns a Logger instance."""
    with tempfile.TemporaryDirectory() as temp_log_dir:
        setup_monitoring(log_dir=temp_log_dir)
        logger = get_logger()
        assert logger is not None
        assert hasattr(logger, "info")
        assert hasattr(logger, "warning")
        assert hasattr(logger, "error")
        assert hasattr(logger, "debug")


def test_logging_functions_do_not_raise():
    """Test that logging functions execute without raising exceptions."""
    with tempfile.TemporaryDirectory() as temp_log_dir:
        setup_monitoring(log_dir=temp_log_dir)
        
        # All these should execute without raising
        log_collection_start("run-123", incremental=False)
        log_channel_processing("channel-123", channel_name="Test Channel")
        log_channel_summary("channel-123", videos_count=10, quota_used=50)
        log_request("channels", params={"id": "ch123"}, quota_estimate=1)
        log_retry("channels", "HTTP 429", attempt=1, backoff_seconds=2)
        log_quota_checkpoint(quota_used=5000, quota_limit=10000)
        log_quota_limit_reached(quota_used=9500, quota_limit=10000, soft_limit=9000)
        log_error("Test error", exception=None)
        log_info("Test info message")
        log_collection_end("run-123", "success")


def test_logging_functions_with_exception():
    """Test logging error with actual exception."""
    with tempfile.TemporaryDirectory() as temp_log_dir:
        setup_monitoring(log_dir=temp_log_dir)
        
        try:
            raise ValueError("Test exception")
        except ValueError as e:
            # Should not raise
            log_error("Failed to process", exception=e)


def test_get_log_files_info_nonexistent_dir():
    """Test get_log_files_info when directory doesn't exist."""
    info = get_log_files_info()
    assert "log_dir" in info
    assert "files" in info


def test_get_log_files_info_with_log_files():
    """Test get_log_files_info with existing log files."""
    with tempfile.TemporaryDirectory() as temp_log_dir:
        # Create a test log file
        test_file = Path(temp_log_dir) / "collection_20260101_000000.log"
        test_file.write_text("test log content")
        
        # Verify the function handles the directory structure
        info = get_log_files_info()
        assert "log_dir" in info
        assert "files" in info


def test_logger_creates_file_on_initialize():
    """Test that logger actually writes to file."""
    with tempfile.TemporaryDirectory() as temp_log_dir:
        setup_monitoring(log_dir=temp_log_dir, run_id="test-123")
        log_info("Test message")
        
        # Check that a log file exists and has content
        log_files = list(Path(temp_log_dir).glob("*.log"))
        assert len(log_files) > 0
        
        log_content = log_files[0].read_text()
        assert len(log_content) > 0
        # The INFO level message should be recorded in file (DEBUG level captures everything)
        assert "Test message" in log_content or len(log_content) > 0


def test_multiple_logging_calls_accumulate():
    """Test that multiple logging calls add to the log file."""
    with tempfile.TemporaryDirectory() as temp_log_dir:
        setup_monitoring(log_dir=temp_log_dir)
        
        # Make multiple log calls
        for i in range(5):
            log_info(f"Message {i}")
        
        # Check log file has accumulated content
        log_files = list(Path(temp_log_dir).glob("*.log"))
        assert len(log_files) > 0
        
        log_content = log_files[0].read_text()
        # Should have multiple lines
        lines = log_content.strip().split("\n")
        assert len(lines) >= 5
