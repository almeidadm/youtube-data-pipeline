import json
import logging
import re
from pathlib import Path

from youtube_data_pipeline.client import YouTubeDataClient
from youtube_data_pipeline.logger import configure_logging, get_logger
from youtube_data_pipeline.storage import ResponseStorage


class _Executable:
    def __init__(self, payload: dict):
        self.payload = payload

    def execute(self) -> dict:
        return self.payload


class _Resource:
    def __init__(self, pages: list[dict]):
        self.pages = pages
        self.calls = 0

    def list(self, **_: dict) -> _Executable:
        payload = self.pages[min(self.calls, len(self.pages) - 1)]
        self.calls += 1
        return _Executable(payload)


class _Service:
    def __init__(self, resources: dict[str, _Resource]):
        self._resources = resources

    def __getattr__(self, item: str):
        if item not in self._resources:
            raise AttributeError(item)

        def _factory():
            return self._resources[item]

        return _factory


def _reset_logging() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)


def _read_last_json(log_path: Path) -> dict:
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert lines, "expected at least one log line"
    return json.loads(lines[-1])


def test_configure_logging_writes_jsonl(tmp_path: Path) -> None:
    _reset_logging()
    log_path = tmp_path / "logs.jsonl"

    configure_logging(log_path=log_path)
    logger = get_logger("test").bind(run_id="run-1")
    logger.info("youtube_api_request", endpoint="search", cost=100)

    payload = _read_last_json(log_path)
    assert payload["event"] == "youtube_api_request"
    assert payload["run_id"] == "run-1"
    assert payload["endpoint"] == "search"
    assert payload["cost"] == 100


def test_client_fetch_emits_log_fields(tmp_path: Path) -> None:
    _reset_logging()
    log_path = tmp_path / "client_logs.jsonl"

    configure_logging(log_path=log_path)
    service = _Service({"search": _Resource([{"items": [1]}])})
    client = YouTubeDataClient(
        service=service,
        storage=ResponseStorage(root=tmp_path),
        run_id="run-logs",
    )

    client.fetch("search", {"part": "snippet", "q": "abc"}, max_pages=1)

    payload = _read_last_json(log_path)
    assert payload["event"] == "youtube_api_request"
    assert payload["run_id"] == "run-logs"
    assert payload["endpoint"] == "search"
    assert payload["origin"] == "abc"
    assert payload["origin_field"] == "q"
    assert payload["cost"] == 100
    assert payload["cumulative_credits"] == 100
    assert payload["per_endpoint"] == {"search": 100}
    assert payload["page"] == 1
    assert payload["file"].endswith("page_001.json")
    assert "endpoint=search" in Path(payload["file"]).parts
    assert re.match(r"run-logs-\d{6}$", payload["request_id"]) is not None
