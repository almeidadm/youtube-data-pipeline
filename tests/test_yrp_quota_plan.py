from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from youtube_data_pipeline import (
    YRP_ALLOWED_ENDPOINTS,
    YRP_GENERAL_CREDIT_LIMIT,
    YRP_SEARCH_CREDIT_LIMIT,
    CreditMonitor,
    YouTubeDataClient,
    YouTubeDataConfig,
    bucket_for_endpoint,
    current_pacific_quota_day,
    pacific_quota_day_window_utc,
    resolve_api_key,
    seconds_until_pacific_quota_reset,
)
from youtube_data_pipeline.endpoints.definitions import DEFAULT_ENDPOINTS
from youtube_data_pipeline.storage import ResponseStorage

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script_module(module_name: str, filename: str) -> Any:
    path = REPO_ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


COLLECT_TERMS_MOD = _load_script_module(
    "collect_videos_from_terms_resumable",
    "collect_videos_from_terms_resumable.py",
)
PREPARE_SHARDS_MOD = _load_script_module(
    "prepare_frozen_seed_shards",
    "prepare_frozen_seed_shards.py",
)
IMPORT_PILOT_MOD = _load_script_module(
    "import_pilot_to_resumable_run",
    "import_pilot_to_resumable_run.py",
)
LONGITUDINAL_MOD = _load_script_module(
    "collect_longitudinal_waves",
    "collect_longitudinal_waves.py",
)
RECIDIVIST_MOD = _load_script_module(
    "collect_recidivist_channels_playlists",
    "collect_recidivist_channels_playlists.py",
)
COMMENTS_MOD = _load_script_module(
    "collect_comments_resumable",
    "collect_comments_resumable.py",
)
EXPORT_MOD = _load_script_module(
    "export_yrp_dehydrated",
    "export_yrp_dehydrated.py",
)


class _Executable:
    def __init__(self, payload: dict[str, Any] | Exception) -> None:
        self.payload = payload

    def execute(self) -> dict[str, Any]:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class _QueueResource:
    def __init__(self, payloads: list[dict[str, Any] | Exception]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict[str, Any]] = []

    def list(self, **params: Any) -> _Executable:
        if not self.payloads:
            raise AssertionError(f"Unexpected call with params={params}")
        self.calls.append(dict(params))
        return _Executable(self.payloads.pop(0))


class _DynamicService:
    def __init__(self, resources: dict[str, list[dict[str, Any] | Exception]]) -> None:
        self.resources = {name: _QueueResource(items) for name, items in resources.items()}

    def __getattr__(self, item: str) -> Any:
        if item not in self.resources:
            raise AttributeError(item)
        return lambda: self.resources[item]


def test_default_endpoints_include_yrp_promotions_and_optional_params() -> None:
    assert "playlistItems" in DEFAULT_ENDPOINTS
    assert DEFAULT_ENDPOINTS["playlistItems"].base_credit_cost == 1
    assert DEFAULT_ENDPOINTS["playlistItems"].required_params == {"part", "playlistId"}
    assert {"id", "maxResults", "pageToken", "videoId"} <= (
        DEFAULT_ENDPOINTS["playlistItems"].optional_params
    )

    assert "videoCategories" in DEFAULT_ENDPOINTS
    assert DEFAULT_ENDPOINTS["videoCategories"].base_credit_cost == 1
    assert DEFAULT_ENDPOINTS["videoCategories"].required_params == {"part"}
    assert {"id", "regionCode", "hl"} <= DEFAULT_ENDPOINTS["videoCategories"].optional_params

    assert {"order", "safeSearch", "eventType", "videoDuration"} <= (
        DEFAULT_ENDPOINTS["search"].optional_params
    )
    assert "hl" in DEFAULT_ENDPOINTS["videos"].optional_params
    assert {"order", "textFormat", "searchTerms", "moderationStatus"} <= (
        DEFAULT_ENDPOINTS["commentThreads"].optional_params
    )
    assert "textFormat" in DEFAULT_ENDPOINTS["comments"].optional_params


def test_credit_monitor_dual_bucket_and_pacific_time_utilities() -> None:
    monitor = CreditMonitor.for_yrp(
        search_credit_limit=200,
        general_credit_limit=2,
        per_endpoint_call_limits={"search": 2, "videos": 1},
    )
    assert bucket_for_endpoint("search") == "search_list"
    assert bucket_for_endpoint("videos") == "general_read"

    assert monitor.check_allowance("search", 100) is None
    monitor.register("search", 100)
    assert monitor.check_allowance("search", 100) is None
    monitor.register("search", 100)
    # 3rd search call hits search_credit_limit (200)
    assert monitor.check_allowance("search", 100) == "search_credit_limit"

    # General bucket is still untouched and allows videos.list
    assert monitor.check_allowance("videos", 1) is None
    monitor.register("videos", 1)
    # 2nd videos call hits per_endpoint_call_limits["videos"] == 1
    assert monitor.check_allowance("videos", 1) == "endpoint_call_limit"

    # Other general endpoint hits general_credit_limit after 2 units
    assert monitor.check_allowance("channels", 1) is None
    monitor.register("channels", 1)
    assert monitor.check_allowance("channels", 1) == "general_credit_limit"

    snap = monitor.snapshot()
    assert snap["per_bucket"] == {"search_list": 200, "general_read": 2}
    assert snap["calls_per_endpoint"] == {"search": 2, "videos": 1, "channels": 1}

    # Verify Pacific Time window reconciliation around 00:00 PT (08:00 UTC in November PST)
    ref_utc = datetime(2026, 11, 18, 7, 30, 0, tzinfo=timezone.utc)
    assert current_pacific_quota_day(ref_utc) == "2026-11-17"
    start_utc, reset_utc = pacific_quota_day_window_utc(ref_utc)
    assert reset_utc == datetime(2026, 11, 18, 8, 0, 0, tzinfo=timezone.utc)
    assert (reset_utc - start_utc).total_seconds() == 86400
    assert seconds_until_pacific_quota_reset(ref_utc) == 1800.0


def test_yrp_config_blocks_captions_and_charges_1_unit_on_failed_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("googleapiclient")
    from googleapiclient.errors import HttpError

    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.setenv("YOUTUBE_YRP_API_KEY", "yrp-institutional-key")
    assert resolve_api_key() == "yrp-institutional-key"

    cfg = YouTubeDataConfig.for_yrp(storage_root=tmp_path)
    assert cfg.api_key == "yrp-institutional-key"
    assert cfg.allow_endpoints == YRP_ALLOWED_ENDPOINTS
    assert "captions" not in (cfg.allow_endpoints or frozenset())
    assert cfg.failed_request_credit_cost == 1
    assert cfg.search_credit_limit == YRP_SEARCH_CREDIT_LIMIT
    assert cfg.general_credit_limit == YRP_GENERAL_CREDIT_LIMIT

    class _DummyResp(dict):
        def __init__(self, status: int, reason: str) -> None:
            super().__init__()
            self.status = status
            self.reason = reason

    err_payload = {
        "error": {
            "code": 400,
            "message": "Invalid filter",
            "errors": [
                {"reason": "invalidSearchFilter", "message": "Invalid", "domain": "youtube.search"}
            ],
        }
    }
    http_err = HttpError(
        resp=_DummyResp(400, "Bad Request"),
        content=json.dumps(err_payload).encode("utf-8"),
        uri="https://www.googleapis.com/youtube/v3/search?key=SECRET",
    )

    service = _DynamicService({"search": [http_err], "captions": [{"items": []}]})
    client = YouTubeDataClient(
        service=service,
        storage=ResponseStorage(root=tmp_path, run_id="yrp-policy-test"),
        credit_monitor=CreditMonitor.for_yrp(),
        run_id="yrp-policy-test",
        failed_request_credit_cost=cfg.failed_request_credit_cost,
        allow_endpoints=cfg.allow_endpoints,
    )

    with pytest.raises(ValueError, match="Endpoint 'captions' bloqueado"):
        client.fetch("captions", {"part": "snippet", "videoId": "v1"})

    res = client.fetch("search", {"part": "snippet", "q": "pix"})
    assert res["errors"]
    # Failed search request charged 1 administrative unit instead of 100
    assert client.credit_monitor.total_spent == 1
    assert client.credit_monitor.search_spent == 1


def test_multi_arm_and_video_duration_in_terms_collector(tmp_path: Path) -> None:
    shard_path = tmp_path / "three_arms.jsonl"
    rows = [
        {
            "term": "governo taxar pix 2026",
            "claim_cluster_id": "cluster_001",
            "representative_item_id": "item_001",
            "item_ids": ["item_001"],
            "rank": 1,
            "arm": "propagacao",
            "query_id": "cluster_001:propagacao:short",
            "video_duration": "short",
            "wave_type": "backfill",
        },
        {
            "term": "governo taxar pix 2026 verdade falso",
            "claim_cluster_id": "cluster_001",
            "representative_item_id": "item_001",
            "item_ids": ["item_001"],
            "rank": 1,
            "arm": "combate",
            "query_id": "cluster_001:combate:short",
            "video_duration": "short",
            "wave_type": "backfill",
        },
    ]
    shard_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )

    # Both arms of cluster_001 coexist without dedupe_key collision
    specs = COLLECT_TERMS_MOD.read_terms_file(shard_path)
    assert len(specs) == 2
    assert specs[0].arm == "propagacao"
    assert specs[1].arm == "combate"
    assert specs[0].video_duration == "short"

    service = _DynamicService(
        {
            "search": [
                {"items": [{"id": {"videoId": "v_prop"}}]},
                {"items": [{"id": {"videoId": "v_comb"}}]},
            ],
            "videos": [
                {"items": [{"id": "v_prop"}]},
                {"items": [{"id": "v_comb"}]},
            ],
        }
    )
    run_id = "run-multi-arm"
    client = YouTubeDataClient(
        service=service,
        storage=ResponseStorage(root=tmp_path, run_id=run_id),
        run_id=run_id,
    )
    args = argparse.Namespace(
        api_key="dummy",
        terms_file=str(shard_path),
        storage_root=str(tmp_path),
        log_path="youtube_data_pipeline.jsonl",
        run_id=run_id,
        compress_responses=False,
        region_code="BR",
        relevance_language="pt",
        video_duration=None,
        videos_part=None,
        dedupe_video_details=None,
        search_pages=1,
        search_max_results=50,
        credit_limit=10_000,
        max_terms=None,
        summary_name="terms_collection_summary.json",
    )
    summary, _ = COLLECT_TERMS_MOD.collect_videos_from_terms_resumable(client, args, specs)
    assert summary["status"] == "completed"
    assert summary["terms_processed"] == 2
    assert service.resources["search"].calls[0]["videoDuration"] == "short"

    sightings_path = tmp_path / "runs" / run_id / "cluster_sightings.jsonl"
    sightings = [
        json.loads(line) for line in sightings_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(sightings) == 2
    assert sightings[0]["arm"] == "propagacao"
    assert sightings[0]["video_duration"] == "short"
    assert sightings[1]["arm"] == "combate"


def test_import_pilot_to_resumable_run_and_longitudinal_waves_and_export(
    tmp_path: Path,
) -> None:
    # 1) Create pilot search_responses.jsonl with 2 clusters (one with nextPageToken)
    pilot_file = tmp_path / "search_responses.jsonl"
    pilot_records = [
        {
            "claim_cluster_id": "cluster_001",
            "query_text": "golpe pix banco central",
            "rank": 1,
            "agency_id": "aos_fatos",
            "arm": "propagacao",
            "page": 1,
            "response": {
                "nextPageToken": "TOKEN_P2",
                "items": [
                    {"id": {"videoId": "v_active"}, "snippet": {"channelId": "UC_CH1"}},
                    {"id": {"videoId": "v_takedown"}, "snippet": {"channelId": "UC_CH1"}},
                ],
            },
        },
        {
            "claim_cluster_id": "cluster_002",
            "query_text": "inss revisao beneficio",
            "rank": 2,
            "agency_id": "lupa",
            "arm": "propagacao",
            "page": 1,
            "response": {
                "items": [
                    {"id": {"videoId": "v_active"}, "snippet": {"channelId": "UC_CH1"}},
                    {"id": {"videoId": "v_ch2"}, "snippet": {"channelId": "UC_CH2"}},
                ],
            },
        },
    ]
    pilot_file.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in pilot_records) + "\n",
        encoding="utf-8",
    )

    pilot_summary = IMPORT_PILOT_MOD.import_pilot_to_resumable_run(
        pilot_files=[pilot_file],
        storage_root=tmp_path,
        run_id="yrp_pilot_propagacao_p1",
    )
    assert pilot_summary["terms_processed"] == 2
    assert pilot_summary["total_sightings"] == 4
    assert pilot_summary["unique_videos_global"] == 3  # v_active, v_takedown, v_ch2
    assert pilot_summary["unique_channels_global"] == 2  # UC_CH1, UC_CH2
    assert pilot_summary["clusters_with_next_page_token"] == 1

    pilot_run_dir = tmp_path / "runs" / "yrp_pilot_propagacao_p1"
    followup_lines = (
        (pilot_run_dir / "pagination_followup_terms.jsonl").read_text("utf-8").splitlines()
    )
    assert len(followup_lines) == 1
    assert json.loads(followup_lines[0])["initial_page_token"] == "TOKEN_P2"

    # 2) Run longitudinal wave T0 on videos from the imported pilot run:
    # Suppose v_takedown was removed before T0 (only v_active and v_ch2 are returned by videos.list)
    discovered_vids = LONGITUDINAL_MOD.discover_ids_from_runs(
        [pilot_run_dir],
        entity_type="video",
    )
    assert discovered_vids == ["v_active", "v_takedown", "v_ch2"]

    wave_service = _DynamicService(
        {
            "videos": [
                {
                    "items": [
                        {
                            "id": "v_active",
                            "snippet": {
                                "channelId": "UC_CH1",
                                "title": "Vídeo ativo",
                                "defaultAudioLanguage": "pt-BR",
                            },
                            "contentDetails": {"duration": "PT45S", "caption": "false"},
                            "statistics": {"viewCount": "1000", "commentCount": "5"},
                            "status": {"privacyStatus": "public", "uploadStatus": "processed"},
                        },
                        {
                            "id": "v_ch2",
                            "snippet": {
                                "channelId": "UC_CH2",
                                "title": "Vídeo canal 2",
                                "defaultAudioLanguage": "pt",
                            },
                            "contentDetails": {"duration": "PT3M", "caption": "true"},
                            "statistics": {"viewCount": "500", "commentCount": "0"},
                            "status": {"privacyStatus": "public", "uploadStatus": "processed"},
                        },
                    ]
                }
            ]
        }
    )
    wave_client = YouTubeDataClient(
        service=wave_service,
        storage=ResponseStorage(root=tmp_path, run_id="yrp_wave_videos_t0"),
        run_id="yrp_wave_videos_t0",
    )
    wave_summary, _ = LONGITUDINAL_MOD.collect_longitudinal_wave(
        wave_client,
        entity_ids=discovered_vids,
        entity_type="video",
        wave="T0",
        storage_root=tmp_path,
    )
    assert wave_summary["status"] == "completed"
    assert wave_summary["returned_count"] == 2
    assert wave_summary["takedowns_count"] == 1

    wave_run_dir = tmp_path / "runs" / "yrp_wave_videos_t0"
    ledger_rows = [
        json.loads(line)
        for line in (wave_run_dir / "takedowns_ledger.jsonl").read_text("utf-8").splitlines()
    ]
    assert len(ledger_rows) == 1
    assert ledger_rows[0]["video_id"] == "v_takedown"
    assert ledger_rows[0]["reason"] == "missing_from_batch_response"

    # 3) Run recidivist channels playlist collection (UC_CH1 appeared in 2 clusters)
    selected_channels = RECIDIVIST_MOD.select_recidivist_channels_from_runs(
        [pilot_run_dir, wave_run_dir],
        max_channels=10,
    )
    assert len(selected_channels) == 2
    assert selected_channels[0].channel_id == "UC_CH1"
    assert selected_channels[0].uploads_playlist_id == "UU_CH1"
    assert selected_channels[0].role == "recidivist"

    pl_service = _DynamicService(
        {
            "playlistItems": [
                {"items": [{"contentDetails": {"videoId": "v_hard_neg_1"}}]},
                {"items": []},
            ],
            "videos": [
                {
                    "items": [
                        {
                            "id": "v_hard_neg_1",
                            "snippet": {
                                "channelId": "UC_CH1",
                                "title": "Hard negative",
                                "defaultAudioLanguage": "pt",
                            },
                            "statistics": {"viewCount": "200", "commentCount": "0"},
                            "status": {"privacyStatus": "public"},
                        }
                    ]
                }
            ],
        }
    )
    pl_client = YouTubeDataClient(
        service=pl_service,
        storage=ResponseStorage(root=tmp_path, run_id="yrp_recidivist_m2"),
        run_id="yrp_recidivist_m2",
    )
    pl_summary, _ = RECIDIVIST_MOD.collect_recidivist_channels_playlists(
        pl_client,
        channels=selected_channels,
        storage_root=tmp_path,
        playlist_pages=1,
        initial_seen_video_ids=discovered_vids,
    )
    assert pl_summary["status"] == "completed"
    assert pl_summary["new_videos_queued_for_details"] == 1

    # 4) Run comments collection on eligible videos (only v_active has commentCount=5 > 0)
    eligible_for_comments = COMMENTS_MOD.select_eligible_videos_from_runs([wave_run_dir])
    assert [v.video_id for v in eligible_for_comments] == ["v_active"]

    comments_service = _DynamicService(
        {
            "commentThreads": [
                {
                    "items": [
                        {
                            "id": "thread_1",
                            "snippet": {
                                "videoId": "v_active",
                                "totalReplyCount": 2,
                                "topLevelComment": {
                                    "id": "c_top_1",
                                    "snippet": {
                                        "videoId": "v_active",
                                        "authorChannelId": {"value": "UC_AUTHOR_RAW_1"},
                                        "textDisplay": "Isso é golpe!",
                                        "likeCount": 12,
                                    },
                                },
                            },
                        }
                    ]
                }
            ],
            "comments": [
                {
                    "items": [
                        {
                            "id": "c_reply_1",
                            "snippet": {
                                "parentId": "c_top_1",
                                "authorChannelId": {"value": "UC_AUTHOR_RAW_2"},
                                "textDisplay": "Verdade, quase caí",
                                "likeCount": 3,
                            },
                        }
                    ]
                }
            ],
        }
    )
    comments_client = YouTubeDataClient(
        service=comments_service,
        storage=ResponseStorage(root=tmp_path, run_id="yrp_comments_m2"),
        run_id="yrp_comments_m2",
    )
    comments_summary, _ = COMMENTS_MOD.collect_comments_resumable(
        comments_client,
        videos=eligible_for_comments,
        storage_root=tmp_path,
        comment_pages=2,
        reply_pages=1,
    )
    assert comments_summary["status"] == "completed"
    assert comments_summary["threads_collected_total"] == 1
    assert comments_summary["replies_collected_total"] == 1

    # 5) Export internal pseudonymized tables and public dehydrated release
    export_dir = tmp_path / "export_out"
    export_manifest = EXPORT_MOD.export_yrp_datasets(
        run_dirs=[
            pilot_run_dir,
            wave_run_dir,
            tmp_path / "runs" / "yrp_recidivist_m2",
            tmp_path / "runs" / "yrp_comments_m2",
        ],
        output_dir=export_dir,
        salt="test-secret-salt-2026",
        default_split="train",
    )
    # 4 total videos (v_active, v_takedown, v_ch2, v_hard_neg_1), 1 excluded -> 3 public
    assert export_manifest["counts"]["internal_videos_total"] == 4
    assert export_manifest["counts"]["excluded_unavailable_videos"] == 1
    assert export_manifest["counts"]["public_dehydrated_videos_exported"] == 3
    assert export_manifest["counts"]["internal_comments_total"] == 2
    assert export_manifest["counts"]["public_dehydrated_comments_exported"] == 2

    # Verify public dehydrated videos contain ONLY allowed ID/label fields and excludes v_takedown
    pub_videos = [
        json.loads(line)
        for line in (export_dir / "public_dehydrated_videos.jsonl").read_text("utf-8").splitlines()
    ]
    pub_video_ids = {r["video_id"] for r in pub_videos}
    assert "v_takedown" not in pub_video_ids
    assert pub_video_ids == {"v_active", "v_ch2", "v_hard_neg_1"}
    for row in pub_videos:
        assert set(row.keys()) <= EXPORT_MOD.PUBLIC_VIDEO_ALLOWED_FIELDS
        assert "title" not in row
        assert "description" not in row

    # Verify internal comments pseudonymized authorChannelId with HMAC-SHA256 and stripped raw ID
    int_comments = [
        json.loads(line)
        for line in (export_dir / "internal_comments.jsonl").read_text("utf-8").splitlines()
    ]
    assert len(int_comments) == 2
    for crow in int_comments:
        assert crow["author_channel_id_hash"] is not None
        assert "UC_AUTHOR_RAW" not in json.dumps(crow)
