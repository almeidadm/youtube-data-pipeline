from pathlib import Path

from youtube_data_pipeline.client import YouTubeDataClient
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
        self.kwargs_history: list[dict] = []

    def list(self, **kwargs: dict) -> _Executable:
        self.kwargs_history.append(dict(kwargs))
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


def test_client_fetch_tracks_pagination_and_credits(tmp_path: Path) -> None:
    service = _Service(
        {
            "search": _Resource(
                [
                    {"items": [1], "nextPageToken": "next"},
                    {"items": [2]},
                ]
            )
        }
    )
    client = YouTubeDataClient(service=service, storage=ResponseStorage(root=tmp_path))

    result = client.fetch("search", {"part": "snippet"}, max_pages=2)

    assert len(result["responses"]) == 2
    assert len(result["files"]) == 2
    assert result["credit_state"]["total_spent"] == 200
    assert result["limit_reached"] is False
    assert result["stopped_reason"] is None


def test_client_fetch_propagates_page_token_between_pages(tmp_path: Path) -> None:
    resource = _Resource(
        [
            {"items": [1], "nextPageToken": "tok-2"},
            {"items": [2]},
        ]
    )
    service = _Service({"search": resource})
    client = YouTubeDataClient(service=service, storage=ResponseStorage(root=tmp_path))

    client.fetch("search", {"part": "snippet"}, max_pages=2)

    assert "pageToken" not in resource.kwargs_history[0]
    assert resource.kwargs_history[1]["pageToken"] == "tok-2"


def test_client_fetch_max_pages_zero_nao_chama_api(tmp_path: Path) -> None:
    resource = _Resource([{"items": [1]}])
    service = _Service({"search": resource})
    client = YouTubeDataClient(service=service, storage=ResponseStorage(root=tmp_path))

    result = client.fetch("search", {"part": "snippet"}, max_pages=0)

    assert resource.calls == 0
    assert result["responses"] == []


def test_client_fetch_caps_at_exactly_max_pages(tmp_path: Path) -> None:
    """Regressão do off-by-one: com nextPageToken sempre presente, busca EXATAMENTE max_pages."""
    pages = [{"items": [i], "nextPageToken": "next"} for i in range(10)]
    resource = _Resource(pages)
    service = _Service({"search": resource})
    client = YouTubeDataClient(service=service, storage=ResponseStorage(root=tmp_path))

    result = client.fetch("search", {"part": "snippet"}, max_pages=5)

    assert len(result["responses"]) == 5
    assert resource.calls == 5
    assert result["credit_state"]["total_spent"] == 500


def test_client_fetch_respects_credit_limit_before_first_call(tmp_path: Path) -> None:
    service = _Service({"search": _Resource([{"items": [1]}])})
    client = YouTubeDataClient(service=service, storage=ResponseStorage(root=tmp_path))

    result = client.fetch(
        "search",
        {"part": "snippet"},
        max_pages=1,
        credit_limit=99,
    )

    assert result["responses"] == []
    assert result["files"] == []
    assert result["credit_state"]["total_spent"] == 0
    assert result["limit_reached"] is True
    assert result["stopped_reason"] == "credit_limit"


def test_client_fetch_stops_when_limit_reached_mid_pagination(tmp_path: Path) -> None:
    service = _Service(
        {
            "search": _Resource(
                [
                    {"items": [1], "nextPageToken": "next"},
                    {"items": [2]},
                ]
            )
        }
    )
    client = YouTubeDataClient(service=service, storage=ResponseStorage(root=tmp_path))

    result = client.fetch(
        "search",
        {"part": "snippet"},
        max_pages=2,
        credit_limit=150,
    )

    assert len(result["responses"]) == 1
    assert len(result["files"]) == 1
    assert result["credit_state"]["total_spent"] == 100
    assert result["limit_reached"] is True
    assert result["stopped_reason"] == "credit_limit"
