"""Regressão de concorrência da coleta paralela.

Contexto: a etapa de coleta do schedule_fake_news_monitoring chama
``YouTubeDataClient.fetch`` a partir de várias threads (ThreadPoolExecutor) sobre uma
ÚNICA instância de cliente. O transporte httplib2 do googleapiclient não é thread-safe;
um Http compartilhado entre threads corrompia o heap (SIGABRT 134). Estes testes fixam
o contrato: (1) cada thread recebe um Http próprio (thread-local); (2) sob contenção o
contador de crédito não perde incrementos; (3) request_id nunca duplica.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from youtube_data_pipeline.client import YouTubeDataClient
from youtube_data_pipeline.storage import ResponseStorage


class _RecordingExecutable:
    """Fake de HttpRequest que registra qual Http a thread injetou em .http."""

    def __init__(self, payload: dict, seen_http: list[object], lock: threading.Lock):
        self.payload = payload
        self._seen_http = seen_http
        self._lock = lock
        self.http = None  # _execute_request grava o Http da thread aqui

    def execute(self) -> dict:
        # Espelha HttpRequest.execute(): usaria self.http como transporte. Aqui só
        # registramos a identidade do Http visto, simulando latência de rede para
        # forçar sobreposição real entre as threads.
        time.sleep(0.005)
        with self._lock:
            self._seen_http.append(id(self.http))
        return self.payload


class _RecordingResource:
    def __init__(self, seen_http: list[object], lock: threading.Lock):
        self._seen_http = seen_http
        self._lock = lock

    def list(self, **_: dict) -> _RecordingExecutable:
        return _RecordingExecutable({"items": [1]}, self._seen_http, self._lock)


class _RecordingService:
    def __init__(self, seen_http: list[object], lock: threading.Lock):
        self._resource = _RecordingResource(seen_http, lock)

    def __getattr__(self, item: str):
        def _factory():
            return self._resource

        return _factory


def test_parallel_fetch_is_thread_safe(tmp_path: Path) -> None:
    seen_http: list[object] = []
    lock = threading.Lock()
    service = _RecordingService(seen_http, lock)
    client = YouTubeDataClient(service=service, storage=ResponseStorage(root=tmp_path))

    workers = 6
    n_queries = 60

    def one(i: int) -> dict:
        return client.fetch("search", {"part": "snippet", "q": f"q{i}"}, max_pages=1)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(one, range(n_queries)))

    # 1) Todas as fetches concluíram sem exceção (o crash original era SIGABRT).
    assert len(results) == n_queries
    assert all(len(r["responses"]) == 1 for r in results)

    # 2) Crédito exato sob contenção: 1 chamada (100) por query, sem incrementos perdidos.
    assert client.credit_monitor.total_spent == n_queries * 100
    assert client.credit_monitor.per_endpoint["search"] == n_queries * 100

    # 3) request_id único — o lock de sequência impede colisão de id/arquivo.
    assert client.request_seq == n_queries


def test_each_thread_gets_distinct_http() -> None:
    client = YouTubeDataClient(service=object(), storage=ResponseStorage(root=Path(".")))

    # Mesmo thread: mesmo Http (thread-local reaproveita a conexão).
    assert client._thread_http() is client._thread_http()

    workers = 6
    # Barreira para garantir que as 6 threads estejam VIVAS ao mesmo tempo segurando
    # seu Http — só assim a identidade dos objetos é comparável (sem reuso de id()
    # após GC de uma thread que já encerrou).
    barrier = threading.Barrier(workers)
    seen: list[object] = []
    seen_lock = threading.Lock()

    def grab() -> None:
        http = client._thread_http()
        assert client._thread_http() is http  # reuso dentro da thread
        barrier.wait()  # mantém todas as threads (e seus Http) vivas simultaneamente
        with seen_lock:
            seen.append(http)

    threads = [threading.Thread(target=grab) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Com todas vivas ao mesmo tempo, cada thread tem um Http distinto (nada compartilhado).
    assert len(seen) == workers
    assert len({id(h) for h in seen}) == workers
