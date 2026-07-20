"""Testes do flag static_discovery em YouTubeDataClient.from_config.

Cobrem: default estático (sem o kwarg static_discovery na build), opt-in dinâmico
(static_discovery=False repassado) e degradação para o doc estático quando a busca
dinâmica falha (rede/chave indisponível no boot).
"""

from pathlib import Path

import pytest

from youtube_data_pipeline.client import YouTubeDataClient
from youtube_data_pipeline.config import YouTubeDataConfig


class _FakeService:
    pass


def _patch_build(monkeypatch: pytest.MonkeyPatch, recorded: list[dict], *, fail_dynamic: bool = False):
    import googleapiclient.discovery as discovery

    def fake_build(api: str, version: str, **kwargs):
        recorded.append(dict(kwargs))
        if fail_dynamic and kwargs.get("static_discovery") is False:
            raise OSError("sem rede")
        return _FakeService()

    monkeypatch.setattr(discovery, "build", fake_build)


def _config(tmp_path: Path, **overrides) -> YouTubeDataConfig:
    return YouTubeDataConfig(api_key="k", storage_root=tmp_path, **overrides)


def test_default_usa_discovery_estatico(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    _patch_build(monkeypatch, calls)
    YouTubeDataClient.from_config(_config(tmp_path))
    assert len(calls) == 1
    assert "static_discovery" not in calls[0]  # comportamento antigo preservado


def test_opt_in_dinamico_repassa_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    _patch_build(monkeypatch, calls)
    YouTubeDataClient.from_config(_config(tmp_path, static_discovery=False))
    assert len(calls) == 1
    assert calls[0]["static_discovery"] is False


def test_dinamico_degrada_para_estatico_em_falha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    _patch_build(monkeypatch, calls, fail_dynamic=True)
    client = YouTubeDataClient.from_config(_config(tmp_path, static_discovery=False))
    assert client is not None
    assert len(calls) == 2
    assert calls[0]["static_discovery"] is False  # 1ª tentativa: dinâmico
    assert "static_discovery" not in calls[1]  # fallback: doc estático local
