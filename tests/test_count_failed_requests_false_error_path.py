"""Regressão: com count_failed_requests=False, um erro de API logo na 1ª página não
pode levantar UnboundLocalError. O caminho de erro (logger + manifest) usa `credit_state`,
que só era atribuído quando count_failed_requests=True; sem nenhum sucesso prévio para
ligá-lo, o primeiro request falho derrubava o fetch inteiro.
"""

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
            "errors": [{"reason": "commentNotFound", "message": "Not found", "domain": "global"}],
        }
    }
    resp = _DummyResp(status=404, reason="Not Found")
    return HttpError(resp=resp, content=json.dumps(payload).encode("utf-8"), uri="https://x?key=T")


def test_error_on_first_page_with_count_failed_requests_false(tmp_path) -> None:
    client = YouTubeDataClient(
        service=_Service(_make_http_error()),
        storage=ResponseStorage(root=tmp_path),
        run_id="run-test",
        count_failed_requests=False,
    )

    # Não deve levantar UnboundLocalError mesmo o erro ocorrendo no 1º request.
    result = client.fetch("comments", {"part": "snippet", "parentId": "p"}, max_pages=1)

    assert result["errors"], "o erro deve ser registrado, não propagado como crash"
    # count_failed_requests=False => o request falho não cobra cota.
    assert client.credit_monitor.total_spent == 0
