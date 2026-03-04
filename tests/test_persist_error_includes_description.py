import json
from typing import Any

import pytest

googleapiclient = pytest.importorskip("googleapiclient")
from googleapiclient.errors import HttpError

from youtube_data_pipeline.client import YouTubeDataClient
from youtube_data_pipeline.storage import ResponseStorage


class _DummyResp(dict):
    def __init__(self, status: int, reason: str, headers: dict[str, Any] | None = None) -> None:
        super().__init__(headers or {})
        self.status = status
        self.reason = reason


class _FailingExecutable:
    def __init__(self, error: HttpError) -> None:
        self.error = error

    def execute(self) -> dict[str, Any]:
        raise self.error


class _Resource:
    def __init__(self, error: HttpError) -> None:
        self.error = error

    def list(self, **_: dict) -> _FailingExecutable:
        return _FailingExecutable(self.error)


class _Service:
    def __init__(self, error: HttpError) -> None:
        self._resource = _Resource(error)

    def comments(self) -> _Resource:
        return self._resource


def _make_http_error() -> HttpError:
    payload = {
        "error": {
            "code": 404,
            "message": "Not found",
            "errors": [
                {
                    "reason": "commentNotFound",
                    "message": "Not found",
                    "domain": "global",
                }
            ],
        }
    }
    content = json.dumps(payload).encode("utf-8")
    resp = _DummyResp(status=404, reason="Not Found")
    return HttpError(resp=resp, content=content, uri="https://example.com?key=TEST")


def test_persist_error_includes_description(tmp_path) -> None:
    error = _make_http_error()
    service = _Service(error)
    client = YouTubeDataClient(
        service=service,
        storage=ResponseStorage(root=tmp_path),
        run_id="run-test",
    )

    result = client.fetch(
        "comments",
        {"part": "snippet", "parentId": "parent"},
        max_pages=1,
    )

    assert result["errors"], "expected errors to be recorded"

    manifest_path = tmp_path / "runs" / client.run_id / "manifest.jsonl"
    entry = json.loads(manifest_path.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["entry_type"] == "error"
    assert entry["error_description"] is not None

    error_file = tmp_path / "runs" / client.run_id / entry["file"]
    payload = json.loads(error_file.read_text(encoding="utf-8"))
    assert payload["description"] is not None
