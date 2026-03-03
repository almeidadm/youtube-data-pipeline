import json
import time
from typing import Any, Dict

from .quota import estimate_quota
from .storage import SqliteStore


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class RequestTracker:
    def __init__(self, store: SqliteStore, run_id: str) -> None:
        self.store = store
        self.run_id = run_id

    def on_request(self, resource: str, params: Dict[str, Any]) -> None:
        quota = estimate_quota(resource, params)
        payload = json.dumps(params, ensure_ascii=True)
        self.store.log_request(
            run_id=self.run_id,
            resource=resource,
            requested_at=_utc_now_iso(),
            params_json=payload,
            quota_estimate=quota,
        )
