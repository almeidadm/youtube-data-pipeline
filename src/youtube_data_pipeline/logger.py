import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import structlog
except ModuleNotFoundError:  # pragma: no cover - fallback para ambientes mínimos
    structlog = None


def configure_logging(log_path: str | Path | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    has_stdout = any(
        isinstance(handler, logging.StreamHandler) and handler.stream is sys.stdout
        for handler in root.handlers
    )
    if not has_stdout:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(stream_handler)

    if log_path is not None:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        for handler in list(root.handlers):
            if isinstance(handler, logging.FileHandler) and getattr(
                handler, "_ydp_managed", False
            ):
                if Path(handler.baseFilename) != log_path:
                    root.removeHandler(handler)
                    handler.close()
        has_file = any(
            isinstance(handler, logging.FileHandler)
            and getattr(handler, "baseFilename", None)
            and Path(handler.baseFilename) == log_path
            for handler in root.handlers
        )
        if not has_file:
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter("%(message)s"))
            file_handler._ydp_managed = True  # type: ignore[attr-defined]
            root.addHandler(file_handler)

    if structlog is not None:
        structlog.configure(
            processors=[
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.add_log_level,
                structlog.processors.JSONRenderer(),
            ],
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )


class _FallbackLogger:
    def __init__(self, name: str, bound: dict[str, Any] | None = None) -> None:
        self._logger = logging.getLogger(name)
        self._bound = dict(bound or {})

    def bind(self, **kwargs: Any) -> "_FallbackLogger":
        merged = dict(self._bound)
        merged.update(kwargs)
        return _FallbackLogger(self._logger.name, merged)

    def info(self, event: str, **kwargs: Any) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "info",
            "event": event,
            **self._bound,
            **kwargs,
        }
        self._logger.info(json.dumps(record, ensure_ascii=False))

    def error(self, event: str, **kwargs: Any) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "error",
            "event": event,
            **self._bound,
            **kwargs,
        }
        self._logger.error(json.dumps(record, ensure_ascii=False))


def get_logger(name: str = "youtube_data_pipeline") -> Any:
    if structlog is None:
        return _FallbackLogger(name)
    return structlog.get_logger(name)
