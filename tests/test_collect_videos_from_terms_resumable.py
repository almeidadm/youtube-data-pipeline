import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from youtube_data_pipeline.client import YouTubeDataClient
from youtube_data_pipeline.storage import ResponseStorage

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "collect_videos_from_terms_resumable.py"
)
SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "collect_videos_from_terms_resumable",
    SCRIPT_PATH,
)
if SCRIPT_SPEC is None or SCRIPT_SPEC.loader is None:  # pragma: no cover - defensive
    raise RuntimeError(f"Não foi possível carregar o script em {SCRIPT_PATH}")
SCRIPT_MODULE = importlib.util.module_from_spec(SCRIPT_SPEC)
sys.modules[SCRIPT_SPEC.name] = SCRIPT_MODULE
SCRIPT_SPEC.loader.exec_module(SCRIPT_MODULE)

CHECKPOINT_NAME = SCRIPT_MODULE.CHECKPOINT_NAME
collect_videos_from_terms_resumable = SCRIPT_MODULE.collect_videos_from_terms_resumable
origin_hash = SCRIPT_MODULE.origin_hash


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


def make_search_page(video_ids: list[str], next_page_token: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "items": [{"id": {"videoId": video_id}} for video_id in video_ids],
    }
    if next_page_token is not None:
        payload["nextPageToken"] = next_page_token
    return payload


def make_videos_page(video_ids: list[str]) -> dict[str, Any]:
    return {
        "items": [
            {
                "id": video_id,
                "snippet": {},
                "statistics": {},
                "contentDetails": {},
            }
            for video_id in video_ids
        ]
    }


def write_terms_file(tmp_path: Path, terms: list[str]) -> Path:
    path = tmp_path / "terms.txt"
    path.write_text("\n".join(terms) + "\n", encoding="utf-8")
    return path


def make_args(tmp_path: Path, terms_file: Path, **overrides: Any) -> argparse.Namespace:
    values: dict[str, Any] = {
        "api_key": "dummy",
        "terms_file": str(terms_file),
        "storage_root": str(tmp_path),
        "log_path": "youtube_data_pipeline.jsonl",
        "run_id": "run-resume-001",
        "compress_responses": False,
        "region_code": "BR",
        "search_pages": 3,
        "search_max_results": 50,
        "credit_limit": 10_000,
        "max_terms": None,
        "summary_name": "terms_collection_summary.json",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def make_client(tmp_path: Path, run_id: str, service: _Service) -> YouTubeDataClient:
    return YouTubeDataClient(
        service=service,
        storage=ResponseStorage(root=tmp_path, run_id=run_id),
        run_id=run_id,
    )


def read_manifest(run_root: Path) -> list[dict[str, Any]]:
    manifest_path = run_root / "manifest.jsonl"
    return [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def run_root(tmp_path: Path, run_id: str) -> Path:
    return tmp_path / "runs" / run_id


def test_resumable_terms_happy_path(tmp_path: Path) -> None:
    terms = ["alpha", "beta"]
    terms_file = write_terms_file(tmp_path, terms)
    args = make_args(tmp_path, terms_file, run_id="run-happy-001")
    service = _Service(
        search_payloads=[
            make_search_page(["v1", "v2"]),
            make_search_page(["v3"]),
        ],
        videos_payloads=[
            make_videos_page(["v1", "v2"]),
            make_videos_page(["v3"]),
        ],
    )
    client = make_client(tmp_path, args.run_id, service)

    summary, summary_path = collect_videos_from_terms_resumable(client, args, terms)

    assert summary["status"] == "completed"
    assert summary["terms_processed"] == 2
    assert summary["next_term_index"] == 2
    assert summary["resume_supported"] is True
    assert summary["checkpoint_file"] == CHECKPOINT_NAME
    assert summary_path.exists()

    checkpoint_path = run_root(tmp_path, args.run_id) / CHECKPOINT_NAME
    checkpoint_payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint_payload["active_term"] is None
    assert checkpoint_payload["status"] == "completed"

    manifest = read_manifest(run_root(tmp_path, args.run_id))
    assert len(manifest) == 4
    videos_origins = [entry["origin"] for entry in manifest if entry["endpoint"] == "videos"]
    assert len(set(videos_origins)) == 2
    saved_pages = sorted(run_root(tmp_path, args.run_id).rglob("page_*.json"))
    assert len(saved_pages) == 4


def test_resumable_terms_resume_after_credit_limit_before_next_term(tmp_path: Path) -> None:
    terms = ["alpha", "beta"]
    terms_file = write_terms_file(tmp_path, terms)
    args = make_args(tmp_path, terms_file, run_id="run-resume-next-term", credit_limit=101)
    client = make_client(
        tmp_path,
        args.run_id,
        _Service(
            search_payloads=[make_search_page(["v1"])],
            videos_payloads=[make_videos_page(["v1"])],
        ),
    )

    first_summary, _ = collect_videos_from_terms_resumable(client, args, terms)

    assert first_summary["status"] == "stopped"
    assert first_summary["stop_reason"] == "credit_limit"
    assert first_summary["terms_processed"] == 1
    assert first_summary["next_term_index"] == 1

    resume_service = _Service(
        search_payloads=[make_search_page(["v2"])],
        videos_payloads=[make_videos_page(["v2"])],
    )
    resumed_client = make_client(tmp_path, args.run_id, resume_service)
    resumed_args = make_args(tmp_path, terms_file, run_id=args.run_id, credit_limit=500)

    resumed_summary, _ = collect_videos_from_terms_resumable(resumed_client, resumed_args, terms)

    assert resumed_summary["status"] == "completed"
    assert resumed_summary["terms_processed"] == 2
    manifest = read_manifest(run_root(tmp_path, args.run_id))
    assert len(manifest) == 4
    assert manifest[-1]["request_id"].endswith("000004")
    assert resume_service.search_resource.calls == [
        {
            "part": "snippet",
            "q": "beta",
            "type": "video",
            "maxResults": 50,
            "regionCode": "BR",
        }
    ]


def test_resumable_terms_resume_mid_search(tmp_path: Path) -> None:
    terms = ["alpha"]
    terms_file = write_terms_file(tmp_path, terms)
    args = make_args(
        tmp_path,
        terms_file,
        run_id="run-mid-search",
        credit_limit=100,
        search_pages=2,
    )
    first_client = make_client(
        tmp_path,
        args.run_id,
        _Service(search_payloads=[make_search_page(["v1"], next_page_token="token-2")]),
    )

    first_summary, _ = collect_videos_from_terms_resumable(first_client, args, terms)

    assert first_summary["status"] == "stopped"
    assert first_summary["terms_processed"] == 0
    assert first_summary["active_term"]["completed_search_pages"] == [1]
    assert first_summary["active_term"]["search_complete"] is False

    resume_service = _Service(
        search_payloads=[make_search_page(["v2"])],
        videos_payloads=[make_videos_page(["v1", "v2"])],
    )
    resumed_client = make_client(tmp_path, args.run_id, resume_service)
    resumed_args = make_args(
        tmp_path,
        terms_file,
        run_id=args.run_id,
        credit_limit=500,
        search_pages=2,
    )

    resumed_summary, _ = collect_videos_from_terms_resumable(
        resumed_client,
        resumed_args,
        terms,
    )

    assert resumed_summary["status"] == "completed"
    assert resumed_summary["terms_processed"] == 1
    assert resumed_summary["per_term"][0]["videos_calls"] == 1
    assert resume_service.search_resource.calls == [
        {
            "part": "snippet",
            "q": "alpha",
            "type": "video",
            "maxResults": 50,
            "regionCode": "BR",
            "pageToken": "token-2",
        }
    ]
    manifest = read_manifest(run_root(tmp_path, args.run_id))
    assert len(manifest) == 3


def test_resumable_terms_resume_mid_videos_batch(tmp_path: Path) -> None:
    terms = ["alpha"]
    terms_file = write_terms_file(tmp_path, terms)
    video_ids = [f"v{index:03d}" for index in range(60)]
    args = make_args(tmp_path, terms_file, run_id="run-mid-videos", credit_limit=101)
    first_client = make_client(
        tmp_path,
        args.run_id,
        _Service(
            search_payloads=[make_search_page(video_ids)],
            videos_payloads=[make_videos_page(video_ids[:50])],
        ),
    )

    first_summary, _ = collect_videos_from_terms_resumable(first_client, args, terms)

    assert first_summary["status"] == "stopped"
    assert first_summary["active_term"]["search_complete"] is True
    assert first_summary["active_term"]["completed_video_batches"] == [1]

    resume_service = _Service(videos_payloads=[make_videos_page(video_ids[50:])])
    resumed_client = make_client(tmp_path, args.run_id, resume_service)
    resumed_args = make_args(tmp_path, terms_file, run_id=args.run_id, credit_limit=500)

    resumed_summary, _ = collect_videos_from_terms_resumable(
        resumed_client,
        resumed_args,
        terms,
    )

    assert resumed_summary["status"] == "completed"
    assert resumed_summary["per_term"][0]["videos_calls"] == 2
    assert resume_service.search_resource.calls == []
    assert len(resume_service.videos_resource.calls) == 1
    manifest = read_manifest(run_root(tmp_path, args.run_id))
    assert len(manifest) == 3


def test_resumable_terms_no_op_rerun_after_completion(tmp_path: Path) -> None:
    terms = ["alpha"]
    terms_file = write_terms_file(tmp_path, terms)
    args = make_args(tmp_path, terms_file, run_id="run-noop-001")
    first_client = make_client(
        tmp_path,
        args.run_id,
        _Service(
            search_payloads=[make_search_page(["v1"])],
            videos_payloads=[make_videos_page(["v1"])],
        ),
    )

    collect_videos_from_terms_resumable(first_client, args, terms)
    manifest_before = read_manifest(run_root(tmp_path, args.run_id))

    noop_service = _Service()
    noop_client = make_client(tmp_path, args.run_id, noop_service)
    second_summary, _ = collect_videos_from_terms_resumable(noop_client, args, terms)

    assert second_summary["status"] == "completed"
    assert noop_service.search_resource.calls == []
    assert noop_service.videos_resource.calls == []
    assert read_manifest(run_root(tmp_path, args.run_id)) == manifest_before


def test_resumable_terms_bootstraps_legacy_run_when_terms_are_complete(tmp_path: Path) -> None:
    run_id = "legacy-run-001"
    first_term = "legacy-alpha"
    second_term = "legacy-beta"
    terms = [first_term, second_term]
    terms_file = write_terms_file(tmp_path, terms)
    args = make_args(tmp_path, terms_file, run_id=run_id, credit_limit=500)
    run_dir = run_root(tmp_path, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    search_dir = run_dir / "endpoint=search" / f"origin=q:{origin_hash(first_term)}"
    search_dir.mkdir(parents=True, exist_ok=True)
    (search_dir / "page_001.json").write_text(
        json.dumps(make_search_page(["v1", "v2", "shared"]), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary_payload = {
        "run_id": run_id,
        "started_at": "2026-03-14T00:00:00+00:00",
        "finished_at": "2026-03-14T00:10:00+00:00",
        "duration_seconds": 600.0,
        "terms_total": 2,
        "terms_processed": 1,
        "limit_reached": True,
        "stop_reason": "credit_limit",
        "unique_videos_global": 3,
        "credit_state": {
            "total_spent": 101,
            "per_endpoint": {"search": 100, "videos": 1},
        },
        "per_term": [
            {
                "term": first_term,
                "search_pages_collected": 1,
                "videos_found": 3,
                "videos_calls": 1,
                "new_unique_videos_global": 3,
            }
        ],
    }
    (run_dir / args.summary_name).write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest_lines = [
        {
            "entry_type": "response",
            "request_id": f"{run_id}-000001",
            "endpoint": "search",
            "origin": first_term,
            "origin_field": "q",
            "origin_hash": origin_hash(first_term),
            "params_hash": "search-hash",
            "page": 1,
            "file": f"endpoint=search/origin=q:{origin_hash(first_term)}/page_001.json",
            "timestamp": "20260314T000000Z",
            "cost": 100,
            "cumulative_credits": 100,
            "per_endpoint": {"search": 100},
        },
        {
            "entry_type": "response",
            "request_id": f"{run_id}-000002",
            "endpoint": "videos",
            "origin": first_term,
            "origin_field": "explicit",
            "origin_hash": origin_hash(first_term),
            "params_hash": "videos-hash",
            "page": 1,
            "file": f"endpoint=videos/origin=explicit:{origin_hash(first_term)}/page_001.json",
            "timestamp": "20260314T000001Z",
            "cost": 1,
            "cumulative_credits": 101,
            "per_endpoint": {"search": 100, "videos": 1},
        },
    ]
    (run_dir / "manifest.jsonl").write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in manifest_lines) + "\n",
        encoding="utf-8",
    )

    resumed_client = make_client(
        tmp_path,
        run_id,
        _Service(
            search_payloads=[make_search_page(["shared", "v4"])],
            videos_payloads=[make_videos_page(["shared", "v4"])],
        ),
    )
    resumed_summary, _ = collect_videos_from_terms_resumable(resumed_client, args, terms)

    assert resumed_summary["status"] == "completed"
    assert resumed_summary["terms_processed"] == 2
    assert resumed_summary["per_term"][1]["new_unique_videos_global"] == 1
    manifest = read_manifest(run_dir)
    assert manifest[-1]["request_id"].endswith("000004")
    assert (run_dir / CHECKPOINT_NAME).exists()


def test_resumable_terms_rejects_legacy_partial_term(tmp_path: Path) -> None:
    run_id = "legacy-run-partial"
    terms = ["legacy-alpha", "legacy-beta"]
    terms_file = write_terms_file(tmp_path, terms)
    args = make_args(tmp_path, terms_file, run_id=run_id)
    run_dir = run_root(tmp_path, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    summary_payload = {
        "run_id": run_id,
        "started_at": "2026-03-14T00:00:00+00:00",
        "finished_at": "2026-03-14T00:10:00+00:00",
        "duration_seconds": 600.0,
        "terms_total": 2,
        "terms_processed": 1,
        "limit_reached": True,
        "stop_reason": "credit_limit",
        "unique_videos_global": 60,
        "credit_state": {
            "total_spent": 101,
            "per_endpoint": {"search": 100, "videos": 1},
        },
        "per_term": [
            {
                "term": "legacy-alpha",
                "search_pages_collected": 1,
                "videos_found": 60,
                "videos_calls": 1,
                "new_unique_videos_global": 60,
            }
        ],
    }
    (run_dir / args.summary_name).write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "manifest.jsonl").write_text(
        json.dumps(
            {
                "entry_type": "response",
                "request_id": f"{run_id}-000001",
                "endpoint": "search",
                "origin": "legacy-alpha",
                "origin_field": "q",
                "origin_hash": origin_hash("legacy-alpha"),
                "params_hash": "search-hash",
                "page": 1,
                "file": "endpoint=search/origin=q:legacy/page_001.json",
                "timestamp": "20260314T000000Z",
                "cost": 100,
                "cumulative_credits": 100,
                "per_endpoint": {"search": 100},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    client = make_client(tmp_path, run_id, _Service())
    with pytest.raises(SystemExit, match="Legacy partial term cannot be resumed exactly"):
        collect_videos_from_terms_resumable(client, args, terms)
