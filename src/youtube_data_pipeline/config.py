from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .credits import (
    YRP_GENERAL_CREDIT_LIMIT,
    YRP_SEARCH_CREDIT_LIMIT,
)
from .errors import RetryPolicy

YRP_ALLOWED_ENDPOINTS: frozenset[str] = frozenset(
    {
        "search",
        "videos",
        "channels",
        "playlistItems",
        "commentThreads",
        "comments",
        "videoCategories",
    }
)


def resolve_api_key(api_key: str | None = None) -> str:
    """Resolve API key from explicit value, YOUTUBE_API_KEY, or YOUTUBE_YRP_API_KEY.

    Always points to a single institutional Google Cloud Project (zero key rotation).
    """
    if api_key and api_key.strip():
        return api_key.strip()
    env_primary = os.getenv("YOUTUBE_API_KEY")
    if env_primary and env_primary.strip():
        return env_primary.strip()
    env_yrp = os.getenv("YOUTUBE_YRP_API_KEY")
    if env_yrp and env_yrp.strip():
        return env_yrp.strip()
    return ""


@dataclass(slots=True)
class YouTubeDataConfig:
    api_key: str = ""
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
    failed_request_credit_cost: int | None = None
    allow_endpoints: frozenset[str] | None = None
    search_credit_limit: int | None = None
    general_credit_limit: int | None = None
    per_endpoint_call_limits: dict[str, int] = field(default_factory=dict)
    yrp_mode: bool = False
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key = resolve_api_key()
        if self.allow_endpoints is not None and not isinstance(self.allow_endpoints, frozenset):
            self.allow_endpoints = frozenset(self.allow_endpoints)
        if self.yrp_mode:
            if self.allow_endpoints is None:
                self.allow_endpoints = YRP_ALLOWED_ENDPOINTS
            if self.failed_request_credit_cost is None:
                self.failed_request_credit_cost = 1
            if self.search_credit_limit is None:
                self.search_credit_limit = YRP_SEARCH_CREDIT_LIMIT
            if self.general_credit_limit is None:
                self.general_credit_limit = YRP_GENERAL_CREDIT_LIMIT

    @classmethod
    def for_yrp(
        cls,
        api_key: str | None = None,
        *,
        allow_endpoints: Iterable[str] | None = None,
        **kwargs: Any,
    ) -> YouTubeDataConfig:
        """Create a YouTubeDataConfig pre-configured for the YRP 6-month profile."""
        allowed = (
            frozenset(allow_endpoints) if allow_endpoints is not None else YRP_ALLOWED_ENDPOINTS
        )
        return cls(
            api_key=resolve_api_key(api_key),
            allow_endpoints=allowed,
            yrp_mode=True,
            **kwargs,
        )

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
