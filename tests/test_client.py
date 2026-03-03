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
