import logging
import sys
from typing import Any

try:
    import structlog
except ModuleNotFoundError:  # pragma: no cover - fallback para ambientes mínimos
    structlog = None


def configure_logging() -> None:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
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
    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)

    def bind(self, **_: Any) -> "_FallbackLogger":
        return self

    def info(self, event: str, **kwargs: Any) -> None:
        self._logger.info("%s | %s", event, kwargs)


def get_logger(name: str = "youtube_data_pipeline") -> Any:
    if structlog is None:
        return _FallbackLogger(name)
    return structlog.get_logger(name)
