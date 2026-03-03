import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from .logger import get_logger


@dataclass(slots=True)
class RecurringCollector:
    interval_seconds: int

    def run(self, job: Callable[[], None], iterations: int | None = None) -> None:
        logger = get_logger("youtube_data_pipeline.orchestration")
        cycle = 0
        while iterations is None or cycle < iterations:
            cycle += 1
            started_at = datetime.now(timezone.utc).isoformat()
            logger.info("scheduled_job_started", cycle=cycle, started_at=started_at)
            job()
            logger.info("scheduled_job_finished", cycle=cycle)
            if iterations is not None and cycle >= iterations:
                break
            time.sleep(self.interval_seconds)
