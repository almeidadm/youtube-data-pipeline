import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import RetryPolicy

@dataclass(slots=True)
class YouTubeDataConfig:
    api_key: str
    storage_root: str | Path = "./data/raw"
    discovery_cache: bool = False
    # True (default): usa o discovery doc ESTÁTICO embutido na googleapiclient —
    # sem rede no boot, mas endpoints novos (ex.: videos.batchGetStats, jun/2026)
    # só aparecem quando a lib for atualizada. False: busca o doc dinâmico na API
    # (sempre atual); se a busca falhar, o client cai de volta no doc estático.
    static_discovery: bool = True
    log_path: str | Path | None = "youtube_data_pipeline.jsonl"
    run_id: str | None = None
    compress_responses: bool = False
    error_mode: str = "best_effort"
    persist_errors: bool = True
    count_failed_requests: bool = True
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    def normalized_storage_root(self) -> Path:
        return Path(self.storage_root).expanduser().resolve()

    def normalized_log_path(self, run_id: str | None = None) -> Path | None:
        if run_id is None:
            run_id = self.run_id
        if run_id is None:
            if self.log_path is None:
                return None
            return Path(self.log_path).expanduser().resolve()
        return self.resolved_log_path(run_id)

    def resolved_log_path(self, run_id: str) -> Path:
        run_dir = self.normalized_storage_root() / "runs" / run_id
        return run_dir / self._log_filename(run_id)

    @staticmethod
    def _hash_run_id(run_id: str) -> str:
        return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]

    def _log_filename(self, run_id: str) -> str:
        run_hash = self._hash_run_id(run_id)
        raw_name = self.log_path or "youtube_data_pipeline.jsonl"
        name = Path(raw_name).name or "youtube_data_pipeline.jsonl"
        suffixes = Path(name).suffixes
        if suffixes:
            suffix = "".join(suffixes)
            stem = name
            for part in reversed(suffixes):
                if stem.endswith(part):
                    stem = stem[: -len(part)]
        else:
            suffix = ".jsonl"
            stem = name
        stem = stem or "youtube_data_pipeline"
        return f"{stem}-{run_hash}{suffix}"
