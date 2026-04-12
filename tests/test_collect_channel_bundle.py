import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

from youtube_data_pipeline.client import YouTubeDataClient
from youtube_data_pipeline.storage import ResponseStorage

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "collect_channel_bundle.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location("collect_channel_bundle", SCRIPT_PATH)
if SCRIPT_SPEC is None or SCRIPT_SPEC.loader is None:  # pragma: no cover - defensive
    raise RuntimeError(f"Não foi possível carregar o script em {SCRIPT_PATH}")
SCRIPT_MODULE = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(SCRIPT_MODULE)

collect_channel_bundle = SCRIPT_MODULE.collect_channel_bundle
register_script_endpoints = SCRIPT_MODULE.register_script_endpoints


class FakeCreditMonitor:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"total_spent": 0, "per_endpoint": {}}

    def snapshot(self) -> dict[str, Any]:
        return self.state


class FakeClient:
    def __init__(self, scripted_calls: list[tuple[str, dict[str, Any]]]) -> None:
        self.scripted_calls = scripted_calls
        self.index = 0
        self.calls: list[dict[str, Any]] = []
        self.registered_endpoints: list[str] = []
        self.credit_monitor = FakeCreditMonitor()
        self.run_id = "fake-run-001"

    def register_endpoint(self, definition: Any) -> None:
        self.registered_endpoints.append(definition.name)

    def fetch(
        self,
        endpoint: str,
        params: dict[str, Any],
        *,
        max_pages: int = 1,
        origin: str | None = None,
        credit_limit: int | None = None,
    ) -> dict[str, Any]:
        expected_endpoint, result = self.scripted_calls[self.index]
        assert endpoint == expected_endpoint
        self.index += 1
        self.calls.append(
            {
                "endpoint": endpoint,
                "params": params,
                "max_pages": max_pages,
                "origin": origin,
                "credit_limit": credit_limit,
            }
        )

        credit_state = result.get("credit_state")
        if isinstance(credit_state, dict):
            self.credit_monitor.state = credit_state
        return result


def build_result(
    responses: list[dict[str, Any]] | None = None,
    *,
    limit_reached: bool = False,
    errors: list[dict[str, Any]] | None = None,
    total_spent: int = 0,
) -> dict[str, Any]:
    return {
        "responses": responses or [],
        "files": [],
        "limit_reached": limit_reached,
        "stopped_reason": "credit_limit" if limit_reached else None,
        "errors": errors or [],
        "credit_state": {"total_spent": total_spent, "per_endpoint": {}},
    }


def make_args(tmp_path: Path, **overrides: Any) -> argparse.Namespace:
    values: dict[str, Any] = {
        "channel_id": "UC_TEST_001",
        "api_key": "dummy",
        "storage_root": str(tmp_path),
        "log_path": "youtube_data_pipeline.jsonl",
        "run_id": None,
        "compress_responses": False,
        "credit_limit": 10_000,
        "activities_pages": 5,
        "activities_max_results": 50,
        "video_pages": 10,
        "max_videos": 200,
        "comment_pages": 2,
        "reply_pages": 1,
        "summary_name": "channel_collection_summary.json",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_collect_channel_bundle_happy_path(tmp_path: Path) -> None:
    fake_client = FakeClient(
        [
            (
                "channels",
                build_result(
                    [
                        {
                            "items": [
                                {
                                    "id": "UC_TEST_001",
                                    "contentDetails": {
                                        "relatedPlaylists": {"uploads": "UU_UPLOADS_001"}
                                    },
                                }
                            ]
                        }
                    ],
                    total_spent=1,
                ),
            ),
            (
                "activities",
                build_result(
                    [{"items": [{"id": "A1"}, {"id": "A2"}]}],
                    total_spent=2,
                ),
            ),
            (
                "playlistItems",
                build_result(
                    [{"items": [{"contentDetails": {"videoId": "v1"}}]}],
                    total_spent=3,
                ),
            ),
            (
                "videos",
                build_result(
                    [
                        {
                            "items": [
                                {
                                    "id": "v1",
                                    "statistics": {
                                        "viewCount": "10",
                                        "likeCount": "2",
                                        "commentCount": "3",
                                    },
                                }
                            ]
                        }
                    ],
                    total_spent=4,
                ),
            ),
            (
                "commentThreads",
                build_result(
                    [
                        {
                            "items": [
                                {
                                    "snippet": {
                                        "topLevelComment": {"id": "c1"}
                                    }
                                }
                            ]
                        }
                    ],
                    total_spent=5,
                ),
            ),
            (
                "comments",
                build_result([{"items": [{"id": "r1"}]}], total_spent=6),
            ),
        ]
    )
    register_script_endpoints(fake_client)

    summary, summary_path = collect_channel_bundle(fake_client, make_args(tmp_path))

    assert {"activities", "playlistItems"}.issubset(fake_client.registered_endpoints)
    assert [call["endpoint"] for call in fake_client.calls] == [
        "channels",
        "activities",
        "playlistItems",
        "videos",
        "commentThreads",
        "comments",
    ]
    assert fake_client.calls[3]["origin"] == "UC_TEST_001:videos_batch:0001"

    assert summary["limit_reached"] is False
    assert summary["stop_reason"] is None
    assert summary["counts"]["playlist_unique_videos"] == 1
    assert summary["counts"]["videos_items_collected"] == 1
    assert summary["counts"]["top_level_comments_collected"] == 1
    assert summary["totals"] == {
        "view_count_total": 10,
        "like_count_total": 2,
        "comment_count_total": 3,
    }
    assert summary_path.exists()

    written_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert written_summary["run_id"] == "fake-run-001"


def test_collect_channel_bundle_stops_on_credit_limit(tmp_path: Path) -> None:
    fake_client = FakeClient(
        [
            (
                "channels",
                build_result(
                    [
                        {
                            "items": [
                                {
                                    "contentDetails": {
                                        "relatedPlaylists": {"uploads": "UU_UPLOADS_001"}
                                    }
                                }
                            ]
                        }
                    ],
                    total_spent=1,
                ),
            ),
            ("activities", build_result([{"items": []}], total_spent=2)),
            (
                "playlistItems",
                build_result(
                    [{"items": [{"contentDetails": {"videoId": "v1"}}]}],
                    total_spent=3,
                ),
            ),
            ("videos", build_result(limit_reached=True, total_spent=3)),
        ]
    )

    summary, _ = collect_channel_bundle(fake_client, make_args(tmp_path))

    assert summary["limit_reached"] is True
    assert summary["stop_reason"] == "credit_limit"
    assert [call["endpoint"] for call in fake_client.calls] == [
        "channels",
        "activities",
        "playlistItems",
        "videos",
    ]


def test_collect_channel_bundle_continues_when_engagement_error_is_skip(tmp_path: Path) -> None:
    fake_client = FakeClient(
        [
            (
                "channels",
                build_result(
                    [
                        {
                            "items": [
                                {
                                    "contentDetails": {
                                        "relatedPlaylists": {"uploads": "UU_UPLOADS_001"}
                                    }
                                }
                            ]
                        }
                    ]
                ),
            ),
            ("activities", build_result([{"items": []}])),
            (
                "playlistItems",
                build_result(
                    [
                        {
                            "items": [
                                {"contentDetails": {"videoId": "v1"}},
                                {"contentDetails": {"videoId": "v2"}},
                            ]
                        }
                    ]
                ),
            ),
            ("videos", build_result([{"items": [{"id": "v1"}, {"id": "v2"}]}])),
            (
                "commentThreads",
                build_result(errors=[{"action": "skip", "reason": "commentsDisabled"}]),
            ),
            ("commentThreads", build_result([{"items": []}])),
        ]
    )

    summary, _ = collect_channel_bundle(fake_client, make_args(tmp_path))

    assert summary["stop_reason"] is None
    assert [call["endpoint"] for call in fake_client.calls].count("commentThreads") == 2


def test_collect_channel_bundle_stops_when_engagement_error_is_stop(tmp_path: Path) -> None:
    fake_client = FakeClient(
        [
            (
                "channels",
                build_result(
                    [
                        {
                            "items": [
                                {
                                    "contentDetails": {
                                        "relatedPlaylists": {"uploads": "UU_UPLOADS_001"}
                                    }
                                }
                            ]
                        }
                    ]
                ),
            ),
            ("activities", build_result([{"items": []}])),
            (
                "playlistItems",
                build_result(
                    [
                        {
                            "items": [
                                {"contentDetails": {"videoId": "v1"}},
                                {"contentDetails": {"videoId": "v2"}},
                            ]
                        }
                    ]
                ),
            ),
            ("videos", build_result([{"items": [{"id": "v1"}, {"id": "v2"}]}])),
            (
                "commentThreads",
                build_result(errors=[{"action": "stop", "reason": "forbidden"}]),
            ),
        ]
    )

    summary, _ = collect_channel_bundle(fake_client, make_args(tmp_path))

    assert summary["stop_reason"] == "stop_action_on_comment_threads"
    assert [call["endpoint"] for call in fake_client.calls] == [
        "channels",
        "activities",
        "playlistItems",
        "videos",
        "commentThreads",
    ]


def test_collect_channel_bundle_respects_dedupe_and_max_videos(tmp_path: Path) -> None:
    fake_client = FakeClient(
        [
            (
                "channels",
                build_result(
                    [
                        {
                            "items": [
                                {
                                    "contentDetails": {
                                        "relatedPlaylists": {"uploads": "UU_UPLOADS_001"}
                                    }
                                }
                            ]
                        }
                    ]
                ),
            ),
            ("activities", build_result([{"items": []}])),
            (
                "playlistItems",
                build_result(
                    [
                        {
                            "items": [
                                {"contentDetails": {"videoId": "v1"}},
                                {"contentDetails": {"videoId": "v2"}},
                                {"contentDetails": {"videoId": "v1"}},
                                {"contentDetails": {"videoId": "v3"}},
                            ]
                        }
                    ]
                ),
            ),
            ("videos", build_result([{"items": [{"id": "v1"}, {"id": "v2"}]}])),
            ("commentThreads", build_result([{"items": []}])),
            ("commentThreads", build_result([{"items": []}])),
        ]
    )

    args = make_args(tmp_path, max_videos=2)
    summary, _ = collect_channel_bundle(fake_client, args)

    videos_call = next(call for call in fake_client.calls if call["endpoint"] == "videos")
    assert videos_call["params"]["id"] == "v1,v2"
    assert summary["counts"]["playlist_unique_videos"] == 2


class _Executable:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def execute(self) -> dict[str, Any]:
        return self.payload


class _Resource:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.calls = 0

    def list(self, **_: dict[str, Any]) -> _Executable:
        payload = self.pages[min(self.calls, len(self.pages) - 1)]
        self.calls += 1
        return _Executable(payload)


class _Service:
    def __init__(self, resources: dict[str, _Resource]) -> None:
        self._resources = resources

    def __getattr__(self, item: str):
        if item not in self._resources:
            raise AttributeError(item)

        def _factory() -> _Resource:
            return self._resources[item]

        return _factory


def test_collect_channel_bundle_persists_manifest_with_run_scoped_paths(
    tmp_path: Path,
) -> None:
    service = _Service(
        {
            "channels": _Resource(
                [
                    {
                        "items": [
                            {
                                "id": "UC_TEST_001",
                                "contentDetails": {
                                    "relatedPlaylists": {"uploads": "UU_UPLOADS_001"}
                                },
                            }
                        ]
                    }
                ]
            ),
            "activities": _Resource([{"items": [{"id": "A1"}]}]),
            "playlistItems": _Resource(
                [{"items": [{"contentDetails": {"videoId": "v1"}}]}]
            ),
            "videos": _Resource(
                [
                    {
                        "items": [
                            {
                                "id": "v1",
                                "statistics": {
                                    "viewCount": "11",
                                    "likeCount": "4",
                                    "commentCount": "5",
                                },
                            }
                        ]
                    }
                ]
            ),
            "commentThreads": _Resource([{"items": []}]),
            "comments": _Resource([{"items": []}]),
        }
    )

    run_id = "run-manifest-001"
    client = YouTubeDataClient(
        service=service,
        storage=ResponseStorage(root=tmp_path, run_id=run_id),
        run_id=run_id,
    )
    register_script_endpoints(client)

    args = make_args(
        tmp_path,
        channel_id="UC_TEST_001",
        run_id=run_id,
        max_videos=1,
        comment_pages=1,
    )

    summary, _ = collect_channel_bundle(client, args)

    manifest_path = tmp_path / "runs" / run_id / "manifest.jsonl"
    assert manifest_path.exists()

    lines = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines()]
    assert lines
    assert any(
        line["endpoint"] == "videos"
        and line["file"].startswith("endpoint=videos/origin=explicit:")
        for line in lines
    )
    assert summary["totals"]["view_count_total"] == 11
