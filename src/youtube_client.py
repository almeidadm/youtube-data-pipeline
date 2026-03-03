import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, Optional

from .monitoring import log_request, log_retry


RETRY_STATUS = {429, 500, 502, 503, 504}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float_ms(name: str, default_ms: int) -> float:
    value_ms = _env_int(name, default_ms)
    return max(1, value_ms) / 1000.0


class YouTubeApiError(RuntimeError):
    def __init__(self, status: Optional[int], message: str, payload: Optional[str] = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


class YouTubeClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        access_token: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        retry_max: Optional[int] = None,
        tracker: Optional[object] = None,
    ) -> None:
        self.api_key = api_key or os.getenv("YOUTUBE_API_KEY")
        self.access_token = access_token or os.getenv("YOUTUBE_ACCESS_TOKEN")
        self.base_url = (base_url or os.getenv("YOUTUBE_API_BASE_URL") or "https://www.googleapis.com/youtube/v3").rstrip("/")
        self.timeout = timeout if timeout is not None else _env_float_ms("YOUTUBE_TIMEOUT_MS", 30000)
        self.retry_max = retry_max if retry_max is not None else _env_int("YOUTUBE_RETRY_MAX", 3)
        self.tracker = tracker

        if not self.api_key and not self.access_token:
            raise ValueError("Defina YOUTUBE_API_KEY ou YOUTUBE_ACCESS_TOKEN")

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _build_url(self, path: str, params: Dict[str, Any]) -> str:
        clean = {k: v for k, v in params.items() if v is not None}
        if self.api_key and "key" not in clean:
            clean["key"] = self.api_key
        query = urllib.parse.urlencode(clean, doseq=True)
        return f"{self.base_url}/{path}?{query}"

    def request(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        backoff = 1.0
        last_error: Optional[Exception] = None
        
        # Log the request attempt
        try:
            # Estimate quota for logging
            from src.quota import estimate_quota
            quota_est = estimate_quota(path, params)
        except Exception:
            quota_est = 0
        log_request(path, params=params, quota_estimate=quota_est)

        for attempt in range(self.retry_max + 1):
            try:
                if self.tracker is not None:
                    try:
                        self.tracker.on_request(path, params)
                    except Exception:
                        pass
                url = self._build_url(path, params)
                req = urllib.request.Request(url, headers=self._headers())
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    body = response.read().decode("utf-8")
                    return json.loads(body) if body else {}
            except urllib.error.HTTPError as exc:
                status = exc.code
                payload = exc.read().decode("utf-8", errors="ignore")
                if status in RETRY_STATUS and attempt < self.retry_max:
                    backoff_int = int(backoff)
                    log_retry(path, f"HTTP {status}", attempt + 1, backoff_int)
                    time.sleep(backoff)
                    backoff *= 2
                    last_error = exc
                    continue
                raise YouTubeApiError(status, f"HTTP {status}", payload) from exc
            except urllib.error.URLError as exc:
                if attempt < self.retry_max:
                    backoff_int = int(backoff)
                    log_retry(path, "Connection error", attempt + 1, backoff_int)
                    time.sleep(backoff)
                    backoff *= 2
                    last_error = exc
                    continue
                raise YouTubeApiError(None, "Falha de conexao", str(exc)) from exc

        raise YouTubeApiError(None, "Falha apos retries", str(last_error))

    def list(
        self,
        resource: str,
        part: Iterable[str],
        fields: Optional[str] = None,
        **params: Any,
    ) -> Dict[str, Any]:
        payload = dict(params)
        payload["part"] = ",".join(part) if not isinstance(part, str) else part
        if fields:
            payload["fields"] = fields
        return self.request(resource, payload)
