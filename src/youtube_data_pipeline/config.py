from dataclasses import dataclass, field
from pathlib import Path

from .errors import RetryPolicy

@dataclass(slots=True)
class YouTubeDataConfig:
    api_key: str
    storage_root: str | Path = "./data/raw"
    discovery_cache: bool = False
    log_path: str | Path | None = "./logs/youtube_data_pipeline.jsonl"
    run_id: str | None = None
    compress_responses: bool = False
    error_mode: str = "best_effort"
    persist_errors: bool = True
    count_failed_requests: bool = True
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    def normalized_storage_root(self) -> Path:
        return Path(self.storage_root).expanduser().resolve()

    def normalized_log_path(self) -> Path | None:
        if self.log_path is None:
            return None
        return Path(self.log_path).expanduser().resolve()
