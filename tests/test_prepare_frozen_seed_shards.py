import csv
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from youtube_data_pipeline.client import YouTubeDataClient
from youtube_data_pipeline.storage import ResponseStorage

PREPARE_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "prepare_frozen_seed_shards.py"
)
PREPARE_SPEC = importlib.util.spec_from_file_location(
    "prepare_frozen_seed_shards",
    PREPARE_SCRIPT_PATH,
)
assert PREPARE_SPEC is not None and PREPARE_SPEC.loader is not None
PREPARE_MODULE = importlib.util.module_from_spec(PREPARE_SPEC)
sys.modules[PREPARE_SPEC.name] = PREPARE_MODULE
PREPARE_SPEC.loader.exec_module(PREPARE_MODULE)

COLLECT_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "collect_videos_from_terms_resumable.py"
)
COLLECT_SPEC = importlib.util.spec_from_file_location(
    "collect_videos_from_terms_resumable",
    COLLECT_SCRIPT_PATH,
)
assert COLLECT_SPEC is not None and COLLECT_SPEC.loader is not None
COLLECT_MODULE = importlib.util.module_from_spec(COLLECT_SPEC)
sys.modules[COLLECT_SPEC.name] = COLLECT_MODULE
COLLECT_SPEC.loader.exec_module(COLLECT_MODULE)

UNIFY_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "unify_runs.py"
)
UNIFY_SPEC = importlib.util.spec_from_file_location(
    "unify_runs",
    UNIFY_SCRIPT_PATH,
)
assert UNIFY_SPEC is not None and UNIFY_SPEC.loader is not None
UNIFY_MODULE = importlib.util.module_from_spec(UNIFY_SPEC)
sys.modules[UNIFY_SPEC.name] = UNIFY_MODULE
UNIFY_SPEC.loader.exec_module(UNIFY_MODULE)


def test_build_option_2b_query_strips_debunk_and_stopwords_preserving_numbers() -> None:
    query, source_field = PREPARE_MODULE.build_option_2b_query(
        central_claim=(
            "É falso que o governo vai taxar movimentações no Pix "
            "acima de R$ 5 mil em 27,5% em 2026"
        ),
        claim_text="Texto secundário",
        title="Título secundário - Aos Fatos",
        max_tokens=20,
    )
    assert source_field == "central_claim"
    assert "falso" not in query.lower()
    assert "que" not in query.lower().split()
    assert "governo" in query
    assert "taxar" in query
    assert "Pix" in query
    assert "R$" in query
    assert "5" in query
    assert "mil" in query
    assert "27,5%" in query
    assert "2026" in query


def test_build_option_2b_query_fallbacks_when_central_claim_empty() -> None:
    query, source_field = PREPARE_MODULE.build_option_2b_query(
        central_claim="",
        claim_text="Posts enganam ao dizer que Banco Central confiscou poupança em 2025",
        title="Checagem - Lupa",
        max_tokens=20,
    )
    assert source_field == "claim_text"
    assert query == "Banco Central confiscou poupança 2025"


def test_compute_date_window_45d_lookback_90d_lookahead() -> None:
    after, before = PREPARE_MODULE.compute_date_window(
        "2026-06-15T12:00:00+00:00",
        lookback_days=45,
        lookahead_days=90,
    )
    assert after == "2026-05-01T12:00:00Z"
    assert before == "2026-09-13T12:00:00Z"

    none_after, none_before = PREPARE_MODULE.compute_date_window(None)
    assert none_after is None
    assert none_before is None


def _write_sample_seed_csv(path: Path) -> None:
    fieldnames = [
        "rank",
        "claim_cluster_id",
        "representative_item_id",
        "agency_id",
        "published_at",
        "published_dt",
        "year",
        "source_url",
        "max_llm_confidence",
        "max_lex_score",
        "source",
        "n_items_in_cluster",
        "item_ids_json",
        "title",
        "claim_text",
        "central_claim",
        "economic_object",
        "body_snippet",
    ]
    rows = [
        {
            "rank": str(i),
            "claim_cluster_id": f"cluster_{i:03d}",
            "representative_item_id": f"item_{i:03d}_a",
            "agency_id": "aos_fatos",
            "published_at": f"2026-05-{i:02d}T10:00:00Z" if i < 6 else "",
            "published_dt": f"2026-05-{i:02d}" if i < 6 else "",
            "year": "2026",
            "source_url": f"https://example.org/{i}",
            "max_llm_confidence": "3" if i <= 4 else "2",
            "max_lex_score": str(10.0 - i),
            "source": "llm_and_broad" if i % 2 == 1 else "llm_only",
            "n_items_in_cluster": "2",
            "item_ids_json": json.dumps([f"item_{i:03d}_a", f"item_{i:03d}_b"]),
            "title": f"É falso que imposto subiu {i}% - Aos Fatos",
            "claim_text": f"Imposto sobre renda subiu {i}% em 2026",
            "central_claim": f"Governo aumentou imposto sobre renda em {i}% em 2026",
            "economic_object": "tributo",
            "body_snippet": "Trecho.",
        }
        for i in range(1, 7)
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _create_mock_monitoring_db(db_path: Path, collected_item_ids: list[str]) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE yt_queries "
            "(query_id INTEGER PRIMARY KEY, item_id TEXT, last_collected_at TEXT)"
        )
        for idx, item_id in enumerate(collected_item_ids, start=1):
            conn.execute(
                "INSERT INTO yt_queries (query_id, item_id, last_collected_at) VALUES (?, ?, ?)",
                (idx, item_id, "2026-10-01T00:00:00Z"),
            )
        conn.commit()
    finally:
        conn.close()


def test_prepare_shards_disjoint_interleaved_and_db_exclusion(tmp_path: Path) -> None:
    seed_csv = tmp_path / "seed_20261107.csv"
    _write_sample_seed_csv(seed_csv)

    db_path = tmp_path / "monitoring.db"
    # Mark cluster_002's second item as already collected in monitoring.db
    _create_mock_monitoring_db(db_path, ["item_002_b"])

    out_dir = tmp_path / "shards_out"
    manifest = PREPARE_MODULE.prepare_shards(
        seed_csv=seed_csv,
        output_dir=out_dir,
        prefix="test_snap",
        num_shards=2,
        shard_strategy="interleaved",
        search_pages=3,
        daily_credit_limit=10_000,
        monitoring_db=db_path,
    )

    assert manifest["counts"]["clusters_active_total"] == 5
    assert manifest["counts"]["clusters_excluded_already_collected"] == 1
    assert manifest["counts"]["items_covered_by_active_clusters"] == 10
    assert manifest["counts"]["clusters_with_date_window"] == 4
    assert manifest["counts"]["clusters_without_date_window"] == 1

    shard_files = [Path(s["file"]) for s in manifest["shards"]]
    assert len(shard_files) == 2

    s1_rows = [json.loads(line) for line in shard_files[0].read_text("utf-8").splitlines()]
    s2_rows = [json.loads(line) for line in shard_files[1].read_text("utf-8").splitlines()]

    s1_ids = {r["claim_cluster_id"] for r in s1_rows}
    s2_ids = {r["claim_cluster_id"] for r in s2_rows}

    # Disjoint and complete (excluding cluster_002)
    assert s1_ids.isdisjoint(s2_ids)
    assert s1_ids | s2_ids == {
        "cluster_001",
        "cluster_003",
        "cluster_004",
        "cluster_005",
        "cluster_006",
    }
    # Interleaved: shard 1 gets positions 0, 2, 4 -> cluster_001, cluster_004, cluster_006
    assert [r["claim_cluster_id"] for r in s1_rows] == [
        "cluster_001",
        "cluster_004",
        "cluster_006",
    ]
    assert [r["claim_cluster_id"] for r in s2_rows] == [
        "cluster_003",
        "cluster_005",
    ]


class _Executable:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def execute(self) -> dict[str, Any]:
        return self.payload


class _QueueResource:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict[str, Any]] = []

    def list(self, **params: Any) -> _Executable:
        if not self.payloads:
            raise AssertionError(f"Unexpected call with params={params}")
        self.calls.append(dict(params))
        return _Executable(self.payloads.pop(0))


class _Service:
    def __init__(
        self,
        *,
        search_payloads: list[dict[str, Any]] | None = None,
        videos_payloads: list[dict[str, Any]] | None = None,
    ) -> None:
        self.search_resource = _QueueResource(search_payloads or [])
        self.videos_resource = _QueueResource(videos_payloads or [])

    def search(self) -> _QueueResource:
        return self.search_resource

    def videos(self) -> _QueueResource:
        return self.videos_resource


def test_collect_videos_from_jsonl_shard_with_3_pages_and_global_video_dedupe(
    tmp_path: Path,
) -> None:
    seed_csv = tmp_path / "seed_20261107.csv"
    _write_sample_seed_csv(seed_csv)
    out_dir = tmp_path / "shards"
    manifest = PREPARE_MODULE.prepare_shards(
        seed_csv=seed_csv,
        output_dir=out_dir,
        prefix="snap",
        num_shards=1,
        max_clusters=2,
        search_pages=3,
    )
    shard_path = Path(manifest["shards"][0]["file"])
    specs = COLLECT_MODULE.read_terms_file(shard_path)
    assert len(specs) == 2

    # Cluster 1 paginates 3 pages (v1, v2, v3); Cluster 2 returns 1 page with (v2, v4)
    # Because v2 was already resolved in Cluster 1, Cluster 2 only resolves [v4] in videos.list!
    service = _Service(
        search_payloads=[
            {"items": [{"id": {"videoId": "v1"}}], "nextPageToken": "p2"},
            {"items": [{"id": {"videoId": "v2"}}], "nextPageToken": "p3"},
            {"items": [{"id": {"videoId": "v3"}}], "nextPageToken": "p4_ignored_at_3"},
            {"items": [{"id": {"videoId": "v2"}}, {"id": {"videoId": "v4"}}]},
        ],
        videos_payloads=[
            {"items": [{"id": "v1"}, {"id": "v2"}, {"id": "v3"}]},
            {"items": [{"id": "v4"}]},
        ],
    )
    run_id = "snap_s01of01"
    client = YouTubeDataClient(
        service=service,
        storage=ResponseStorage(root=tmp_path, run_id=run_id),
        run_id=run_id,
    )
    import argparse

    args = argparse.Namespace(
        api_key="dummy",
        terms_file=str(shard_path),
        storage_root=str(tmp_path),
        log_path="youtube_data_pipeline.jsonl",
        run_id=run_id,
        compress_responses=False,
        region_code="BR",
        relevance_language="pt",
        videos_part=None,
        dedupe_video_details=None,
        search_pages=3,
        search_max_results=50,
        credit_limit=10_000,
        max_terms=None,
        summary_name="terms_collection_summary.json",
    )

    summary, _ = COLLECT_MODULE.collect_videos_from_terms_resumable(client, args, specs)

    assert summary["status"] == "completed"
    assert summary["terms_processed"] == 2
    assert summary["unique_videos_global"] == 4
    # 4 search pages (400) + 2 videos.list calls (2) = 402 credits
    assert summary["credit_state"]["total_spent"] == 402

    # Verify search params included publishedAfter, publishedBefore, relevanceLanguage="pt"
    first_search_call = service.search_resource.calls[0]
    assert first_search_call["relevanceLanguage"] == "pt"
    assert first_search_call["publishedAfter"] == "2026-03-17T10:00:00Z"
    assert first_search_call["publishedBefore"] == "2026-07-30T10:00:00Z"

    # Verify videos.list for Cluster 2 only requested "v4" (v2 deduplicated) and included status
    assert service.videos_resource.calls[0]["id"] == "v1,v2,v3"
    assert service.videos_resource.calls[0]["part"] == "snippet,contentDetails,statistics,status"
    assert service.videos_resource.calls[1]["id"] == "v4"

    # Verify cluster_sightings.jsonl was written with fan-out item_ids
    sightings_file = tmp_path / "runs" / run_id / "cluster_sightings.jsonl"
    assert sightings_file.exists()
    sightings_rows = [
        json.loads(line) for line in sightings_file.read_text("utf-8").splitlines()
    ]
    assert len(sightings_rows) == 5  # 3 in cluster_001 + 2 in cluster_002 (including v2 sighting!)
    assert sightings_rows[0]["claim_cluster_id"] == "cluster_001"
    assert sightings_rows[0]["item_ids"] == ["item_001_a", "item_001_b"]
    assert sightings_rows[3]["claim_cluster_id"] == "cluster_002"
    assert sightings_rows[3]["video_id"] == "v2"


def test_unify_disjoint_shard_runs(tmp_path: Path) -> None:
    import argparse

    seed_csv = tmp_path / "seed_20261107.csv"
    _write_sample_seed_csv(seed_csv)
    out_dir = tmp_path / "shards"
    manifest = PREPARE_MODULE.prepare_shards(
        seed_csv=seed_csv,
        output_dir=out_dir,
        prefix="snap",
        num_shards=2,
        shard_strategy="interleaved",
        max_clusters=2,
        search_pages=3,
    )
    shard_1 = Path(manifest["shards"][0]["file"])
    shard_2 = Path(manifest["shards"][1]["file"])

    for shard_idx, (shard_path, vid_id) in enumerate(
        [(shard_1, "vid_s1"), (shard_2, "vid_s2")], start=1
    ):
        specs = COLLECT_MODULE.read_terms_file(shard_path)
        run_id = f"snap_s0{shard_idx}of02"
        service = _Service(
            search_payloads=[{"items": [{"id": {"videoId": vid_id}}]}],
            videos_payloads=[{"items": [{"id": vid_id}]}],
        )
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
            videos_part=None,
            dedupe_video_details=None,
            search_pages=3,
            search_max_results=50,
            credit_limit=10_000,
            max_terms=None,
            summary_name="terms_collection_summary.json",
        )
        COLLECT_MODULE.collect_videos_from_terms_resumable(client, args, specs)

    cp1 = UNIFY_MODULE.load_json(
        tmp_path / "runs" / "snap_s01of02" / "terms_collection_checkpoint.json"
    )
    cp2 = UNIFY_MODULE.load_json(
        tmp_path / "runs" / "snap_s02of02" / "terms_collection_checkpoint.json"
    )
    merged_cp = UNIFY_MODULE.build_unified_checkpoint(
        run_id="snap_unified",
        checkpoints=[cp1, cp2],
        keep_credits=True,
        disjoint_shards=True,
    )
    assert merged_cp["status"] == "completed"
    assert merged_cp["config"]["terms_total"] == 2
    assert len(merged_cp["per_term"]) == 2
    assert [item["claim_cluster_id"] for item in merged_cp["per_term"]] == [
        "cluster_001",
        "cluster_002",
    ]
    assert merged_cp["seen_video_ids"] == ["vid_s1", "vid_s2"]
    assert merged_cp["credit_state"]["total_spent"] == 202

    target_root = tmp_path / "runs" / "snap_unified"
    target_root.mkdir(parents=True, exist_ok=True)
    sightings_count = UNIFY_MODULE.merge_cluster_sightings(
        [tmp_path / "runs" / "snap_s01of02", tmp_path / "runs" / "snap_s02of02"],
        target_root,
    )
    assert sightings_count == 2
