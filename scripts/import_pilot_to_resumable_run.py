"""Bootstrap pilot `search_responses.jsonl` into a standard `youtube-api` resumable run.

Converts the pilot `search.list` responses (e.g., 3,952 propagation queries / 58,886 unique
videos / 24,203 channels from `fact-checking-scrape/data/runs/youtube_collection/`)
into a standard run directory under `{storage_root}/runs/{run_id}/` (default:
`yrp_pilot_propagacao_p1`), generating:
1) `endpoint=search/origin=q:{hash}/page_{idx:03d}.json(.gz)`
2) `manifest.jsonl`
3) `cluster_sightings.jsonl`
4) `terms_collection_checkpoint.json` and `terms_collection_summary.json`
5) `pilot_channel_ids.json` (unique channel IDs ready for `channels.list` T0 batching)
6) `pagination_followup_terms.jsonl` (clusters whose page 1 had `nextPageToken`, avoiding
   re-executing page 1 when expanding to pages 2 and 3).
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from youtube_data_pipeline.storage import ResponseStorage

CHECKPOINT_SCHEMA_VERSION = 1
CHECKPOINT_NAME = "terms_collection_checkpoint.json"
SUMMARY_NAME = "terms_collection_summary.json"
CLUSTER_SIGHTINGS_NAME = "cluster_sightings.jsonl"
FOLLOWUP_SHARD_NAME = "pagination_followup_terms.jsonl"
CHANNEL_IDS_NAME = "pilot_channel_ids.json"


def iso_now() -> str:
    """Return current UTC timestamp in ISO 8601."""
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def build_search_origin(
    *,
    term: str,
    page_index: int,
    claim_cluster_id: str | None = None,
    arm: str | None = None,
    query_id: str | None = None,
    video_duration: str | None = None,
    wave_type: str | None = None,
) -> str:
    """Build deterministic origin string compatible with `collect_videos_from_terms_resumable`."""
    payload: dict[str, Any] = {
        "phase": "search_page",
        "term": term,
        "page": page_index,
    }
    if claim_cluster_id:
        payload["claim_cluster_id"] = claim_cluster_id
    if arm:
        payload["arm"] = arm
    if query_id:
        payload["query_id"] = query_id
    if video_duration:
        payload["video_duration"] = video_duration
    if wave_type:
        payload["wave_type"] = wave_type
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def compute_spec_hash_key(spec: Mapping[str, Any]) -> str:
    """Compute hash key matching `TermSpec.hash_key()` in `collect_videos_from_terms_resumable`."""
    payload: dict[str, Any] = {
        "term": spec["term"],
        "claim_cluster_id": spec.get("claim_cluster_id"),
        "representative_item_id": spec.get("representative_item_id"),
        "published_after": spec.get("published_after"),
        "published_before": spec.get("published_before"),
    }
    for opt_key in ("arm", "query_id", "video_duration", "wave_type"):
        if spec.get(opt_key) is not None:
            payload[opt_key] = spec[opt_key]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def load_reference_terms(terms_file: Path | None) -> dict[str, dict[str, Any]]:
    """Index reference `TermSpec` dicts from a frozen `.jsonl` by `claim_cluster_id` and `term`."""
    by_key: dict[str, dict[str, Any]] = {}
    if terms_file is None or not terms_file.exists():
        return by_key
    for raw_line in terms_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        payload = json.loads(line)
        cluster_id = str(payload.get("claim_cluster_id") or "").strip()
        term = str(payload.get("term") or payload.get("query_text") or "").strip()
        if cluster_id:
            by_key[f"cluster:{cluster_id}"] = payload
        if term:
            by_key[f"term:{term}"] = payload
    return by_key


def extract_response_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the YouTube API `search.list` response dict from a pilot JSONL record."""
    for key in ("response", "payload", "raw_response", "api_response"):
        candidate = record.get(key)
        if isinstance(candidate, Mapping) and "items" in candidate:
            return dict(candidate)
    if "items" in record and isinstance(record.get("items"), list):
        return {
            k: v
            for k, v in record.items()
            if k
            in {"kind", "etag", "nextPageToken", "prevPageToken", "regionCode", "pageInfo", "items"}
        }
    return {"items": []}


def normalize_pilot_record(
    record: Mapping[str, Any],
    *,
    reference_by_key: Mapping[str, dict[str, Any]],
    default_arm: str | None = None,
) -> dict[str, Any]:
    """Normalize one pilot search record and enrich from reference shard if available."""
    cluster_id = (
        str(
            record.get("claim_cluster_id")
            or record.get("cluster_id")
            or record.get("item_id")
            or ""
        ).strip()
        or None
    )
    term = str(
        record.get("query_text")
        or record.get("term")
        or record.get("query")
        or record.get("q")
        or ""
    ).strip()

    ref: dict[str, Any] = {}
    if cluster_id and f"cluster:{cluster_id}" in reference_by_key:
        ref = reference_by_key[f"cluster:{cluster_id}"]
    elif term and f"term:{term}" in reference_by_key:
        ref = reference_by_key[f"term:{term}"]

    if not term and ref:
        term = str(ref.get("term") or ref.get("query_text") or "").strip()
    if not cluster_id and ref:
        cluster_id = str(ref.get("claim_cluster_id") or "").strip() or None
    if not term:
        raise ValueError(f"Registro do piloto sem query_text/term: {record}")

    rep_item_id = (
        str(record.get("representative_item_id") or ref.get("representative_item_id") or "").strip()
        or cluster_id
    )
    raw_item_ids = record.get("item_ids") or ref.get("item_ids")
    if isinstance(raw_item_ids, list) and raw_item_ids:
        item_ids = [str(x) for x in raw_item_ids if str(x).strip()]
    elif rep_item_id:
        item_ids = [rep_item_id]
    else:
        item_ids = []

    raw_rank = record.get("rank") if record.get("rank") is not None else ref.get("rank")
    rank_val = int(raw_rank) if raw_rank is not None and str(raw_rank).strip() != "" else None

    agency_id = str(record.get("agency_id") or ref.get("agency_id") or "").strip() or None
    published_after = (
        str(
            record.get("published_after")
            or record.get("publishedAfter")
            or ref.get("published_after")
            or ""
        ).strip()
        or None
    )
    published_before = (
        str(
            record.get("published_before")
            or record.get("publishedBefore")
            or ref.get("published_before")
            or ""
        ).strip()
        or None
    )
    arm = str(record.get("arm") or ref.get("arm") or default_arm or "").strip() or None
    query_id = str(record.get("query_id") or ref.get("query_id") or "").strip() or None
    video_duration = (
        str(
            record.get("video_duration")
            or record.get("videoDuration")
            or ref.get("video_duration")
            or ""
        ).strip()
        or None
    )
    wave_type = str(record.get("wave_type") or ref.get("wave_type") or "").strip() or None
    page = int(record.get("page") or 1)
    collected_at = (
        str(record.get("collected_at") or record.get("timestamp") or "").strip() or iso_now()
    )
    response_payload = extract_response_payload(record)

    return {
        "term": term,
        "query_text": term,
        "claim_cluster_id": cluster_id,
        "representative_item_id": rep_item_id,
        "item_ids": item_ids,
        "rank": rank_val,
        "agency_id": agency_id,
        "published_after": published_after,
        "published_before": published_before,
        "arm": arm,
        "query_id": query_id,
        "video_duration": video_duration,
        "wave_type": wave_type,
        "page": page,
        "collected_at": collected_at,
        "response": response_payload,
    }


def resolve_pilot_files(
    pilot_paths: Sequence[str | Path],
    pilot_glob: str | None = None,
) -> list[Path]:
    """Resolve explicit pilot JSONL files and optional glob patterns."""
    resolved: list[Path] = []
    seen: set[Path] = set()
    for raw in pilot_paths:
        p = Path(raw).expanduser().resolve()
        if p.is_dir():
            for child in sorted(p.rglob("search_responses.jsonl")):
                if child not in seen:
                    seen.add(child)
                    resolved.append(child)
        elif p.exists() and p not in seen:
            seen.add(p)
            resolved.append(p)
    if pilot_glob:
        for matched in sorted(glob.glob(str(Path(pilot_glob).expanduser()), recursive=True)):
            p = Path(matched).resolve()
            if p.is_file() and p not in seen:
                seen.add(p)
                resolved.append(p)
    return resolved


def import_pilot_to_resumable_run(
    *,
    pilot_files: Sequence[Path],
    storage_root: Path,
    run_id: str = "yrp_pilot_propagacao_p1",
    terms_file: Path | None = None,
    region_code: str = "BR",
    search_pages: int = 1,
    search_max_results: int = 50,
    compress_responses: bool = False,
    default_arm: str | None = None,
) -> dict[str, Any]:
    """Import pilot `search_responses.jsonl` files into a standard resumable run directory."""
    if not pilot_files:
        raise FileNotFoundError("Nenhum arquivo search_responses.jsonl encontrado para importar.")

    storage_root = storage_root.expanduser().resolve()
    run_root = storage_root / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    storage = ResponseStorage(root=storage_root, run_id=run_id, compress=compress_responses)
    reference_by_key = load_reference_terms(terms_file)

    # Group pages by cluster/term preserving order
    grouped_by_key: dict[str, dict[str, Any]] = {}
    ordered_keys: list[str] = []

    for file_path in pilot_files:
        for raw_line in file_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            record = json.loads(line)
            norm = normalize_pilot_record(
                record,
                reference_by_key=reference_by_key,
                default_arm=default_arm,
            )
            cluster_key = str(norm["claim_cluster_id"] or norm["term"])
            if cluster_key not in grouped_by_key:
                grouped_by_key[cluster_key] = {
                    "spec": {
                        k: norm[k]
                        for k in (
                            "term",
                            "query_text",
                            "claim_cluster_id",
                            "representative_item_id",
                            "item_ids",
                            "rank",
                            "agency_id",
                            "published_after",
                            "published_before",
                            "arm",
                            "query_id",
                            "video_duration",
                            "wave_type",
                        )
                    },
                    "pages": {},
                }
                ordered_keys.append(cluster_key)
            page_num = norm["page"]
            if page_num not in grouped_by_key[cluster_key]["pages"]:
                grouped_by_key[cluster_key]["pages"][page_num] = norm

    seen_video_ids: list[str] = []
    seen_video_set: set[str] = set()
    seen_channel_ids: list[str] = []
    seen_channel_set: set[str] = set()

    per_term: list[dict[str, Any]] = []
    followup_specs: list[dict[str, Any]] = []
    total_sightings = 0
    request_seq = 0
    total_spent = 0
    started_at = iso_now()

    sightings_path = run_root / CLUSTER_SIGHTINGS_NAME
    if sightings_path.exists():
        sightings_path.unlink()
    manifest_path = run_root / "manifest.jsonl"
    if manifest_path.exists():
        manifest_path.unlink()

    with sightings_path.open("w", encoding="utf-8") as sightings_handle:
        for term_index, cluster_key in enumerate(ordered_keys):
            entry_group = grouped_by_key[cluster_key]
            spec = entry_group["spec"]
            pages_map: dict[int, dict[str, Any]] = entry_group["pages"]

            term_video_ids: list[str] = []
            term_video_set: set[str] = set()
            new_unique_for_term = 0
            last_next_page_token: str | None = None

            for page_idx in sorted(pages_map.keys()):
                page_record = pages_map[page_idx]
                response_payload = page_record["response"]
                request_seq += 1
                total_spent += 100
                request_id = f"{run_id}-{request_seq:06d}"

                origin = build_search_origin(
                    term=spec["term"],
                    page_index=page_idx,
                    claim_cluster_id=spec.get("claim_cluster_id"),
                    arm=spec.get("arm"),
                    query_id=spec.get("query_id"),
                    video_duration=spec.get("video_duration"),
                    wave_type=spec.get("wave_type"),
                )
                params = {
                    "part": "snippet",
                    "q": spec["term"],
                    "type": "video",
                    "maxResults": search_max_results,
                    "regionCode": region_code,
                    "relevanceLanguage": "pt",
                }
                if spec.get("published_after"):
                    params["publishedAfter"] = spec["published_after"]
                if spec.get("published_before"):
                    params["publishedBefore"] = spec["published_before"]
                if spec.get("video_duration"):
                    params["videoDuration"] = spec["video_duration"]

                save_meta = storage.save_with_metadata(
                    "search",
                    response_payload,
                    page_idx=page_idx,
                    run_id=run_id,
                    origin=origin,
                    origin_field="q",
                    params=params,
                )
                storage.append_manifest(
                    {
                        "entry_type": "response",
                        "request_id": request_id,
                        "endpoint": "search",
                        "origin": save_meta["origin"],
                        "origin_field": "q",
                        "origin_hash": save_meta["origin_hash"],
                        "params_hash": save_meta["params_hash"],
                        "page": page_idx,
                        "file": save_meta["relative_path"],
                        "timestamp": save_meta["timestamp"],
                        "cost": 100,
                        "cumulative_credits": total_spent,
                        "per_endpoint": {"search": total_spent},
                    },
                    run_id=run_id,
                )

                items = response_payload.get("items", [])
                if isinstance(items, list):
                    for rank_in_page, item in enumerate(items, start=1):
                        if not isinstance(item, Mapping):
                            continue
                        video_id = str((item.get("id") or {}).get("videoId") or "").strip()
                        if not video_id:
                            continue
                        channel_id = str((item.get("snippet") or {}).get("channelId") or "").strip()
                        if channel_id and channel_id not in seen_channel_set:
                            seen_channel_set.add(channel_id)
                            seen_channel_ids.append(channel_id)

                        if video_id not in term_video_set:
                            term_video_set.add(video_id)
                            term_video_ids.append(video_id)
                        if video_id not in seen_video_set:
                            seen_video_set.add(video_id)
                            seen_video_ids.append(video_id)
                            new_unique_for_term += 1

                        sighting_row: dict[str, Any] = {
                            "run_id": run_id,
                            "collected_at": page_record["collected_at"],
                            "claim_cluster_id": spec.get("claim_cluster_id"),
                            "representative_item_id": spec.get("representative_item_id"),
                            "item_ids": spec.get("item_ids", []),
                            "rank": spec.get("rank"),
                            "agency_id": spec.get("agency_id"),
                            "query_text": spec["term"],
                            "published_after": spec.get("published_after"),
                            "published_before": spec.get("published_before"),
                            "arm": spec.get("arm"),
                            "query_id": spec.get("query_id"),
                            "video_duration": spec.get("video_duration"),
                            "wave_type": spec.get("wave_type"),
                            "page": page_idx,
                            "rank_in_page": rank_in_page,
                            "video_id": video_id,
                        }
                        if channel_id:
                            sighting_row["channel_id"] = channel_id
                        sightings_handle.write(json.dumps(sighting_row, ensure_ascii=False) + "\n")
                        total_sightings += 1

                last_next_page_token = response_payload.get("nextPageToken")

            term_summary: dict[str, Any] = {
                "term_index": term_index,
                "term": spec["term"],
                "status": "completed",
                "search_pages_collected": len(pages_map),
                "videos_found": len(term_video_ids),
                "videos_calls": 0,
                "new_unique_videos_global": new_unique_for_term,
            }
            for opt_key in (
                "claim_cluster_id",
                "representative_item_id",
                "rank",
                "published_after",
                "published_before",
                "arm",
                "query_id",
                "video_duration",
                "wave_type",
            ):
                if spec.get(opt_key) is not None:
                    term_summary[opt_key] = spec[opt_key]
            per_term.append(term_summary)

            if last_next_page_token:
                followup_entry = {k: v for k, v in spec.items() if v is not None}
                followup_entry["initial_page_token"] = str(last_next_page_token)
                followup_entry["pages_already_collected"] = len(pages_map)
                followup_specs.append(followup_entry)

    # Write imported terms file if no external terms_file was supplied
    effective_terms_file = (
        terms_file.expanduser().resolve()
        if terms_file is not None and terms_file.exists()
        else (run_root / "imported_pilot_terms.jsonl")
    )
    if not effective_terms_file.exists():
        with effective_terms_file.open("w", encoding="utf-8") as handle:
            for cluster_key in ordered_keys:
                spec_out = {
                    k: v for k, v in grouped_by_key[cluster_key]["spec"].items() if v is not None
                }
                handle.write(json.dumps(spec_out, ensure_ascii=False) + "\n")

    digest = hashlib.sha256()
    for cluster_key in ordered_keys:
        digest.update(compute_spec_hash_key(grouped_by_key[cluster_key]["spec"]).encode("utf-8"))
        digest.update(b"\n")
    terms_hash = digest.hexdigest()

    fingerprint_payload = {
        "terms_hash": terms_hash,
        "region_code": region_code,
        "search_pages": search_pages,
        "search_max_results": search_max_results,
        "compress_responses": compress_responses,
        "summary_name": SUMMARY_NAME,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    finished_at = iso_now()
    credit_state = {
        "total_spent": total_spent,
        "per_endpoint": {"search": total_spent},
        "per_bucket": {"search_list": total_spent, "general_read": 0},
        "calls_per_endpoint": {"search": request_seq},
    }
    config_meta = {
        "terms_file": str(effective_terms_file),
        "terms_hash": terms_hash,
        "terms_total": len(ordered_keys),
        "region_code": region_code,
        "search_pages": search_pages,
        "search_max_results": search_max_results,
        "compress_responses": compress_responses,
        "summary_name": SUMMARY_NAME,
        "fingerprint": fingerprint,
    }

    checkpoint_payload = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "run_id": run_id,
        "status": "completed",
        "started_at": started_at,
        "updated_at": finished_at,
        "finished_at": finished_at,
        "config": config_meta,
        "credit_state": credit_state,
        "request_seq": request_seq,
        "seen_video_ids": seen_video_ids,
        "per_term": per_term,
        "active_term": None,
        "next_term_index": len(per_term),
        "stop_reason": None,
        "resume_supported": True,
    }

    followup_path = run_root / FOLLOWUP_SHARD_NAME
    with followup_path.open("w", encoding="utf-8") as handle:
        for item in followup_specs:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    channels_path = run_root / CHANNEL_IDS_NAME
    atomic_write_json(
        channels_path,
        {
            "run_id": run_id,
            "unique_channels_count": len(seen_channel_ids),
            "channel_ids": seen_channel_ids,
        },
    )

    summary_payload = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "updated_at": finished_at,
        "duration_seconds": 0.0,
        "terms_total": len(ordered_keys),
        "terms_processed": len(per_term),
        "limit_reached": False,
        "stop_reason": None,
        "status": "completed",
        "next_term_index": len(per_term),
        "resume_supported": True,
        "checkpoint_file": CHECKPOINT_NAME,
        "unique_videos_global": len(seen_video_ids),
        "unique_channels_global": len(seen_channel_ids),
        "total_sightings": total_sightings,
        "clusters_with_next_page_token": len(followup_specs),
        "followup_shard_file": str(followup_path),
        "channel_ids_file": str(channels_path),
        "credit_state": credit_state,
        "per_term": per_term,
        "active_term": None,
        "summary_file": SUMMARY_NAME,
    }

    atomic_write_json(run_root / CHECKPOINT_NAME, checkpoint_payload)
    atomic_write_json(run_root / SUMMARY_NAME, summary_payload)
    return summary_payload


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Converte search_responses.jsonl do piloto para um diretório de run padrão do "
            "youtube-api (manifest.jsonl, cluster_sightings.jsonl, checkpoint)."
        )
    )
    parser.add_argument(
        "--pilot-jsonl",
        action="append",
        default=[],
        help="Arquivo search_responses.jsonl ou diretório com runs do piloto (repita se preciso).",
    )
    parser.add_argument(
        "--pilot-glob",
        default=None,
        help="Glob opcional para localizar search_responses.jsonl do piloto.",
    )
    parser.add_argument(
        "--terms-file",
        type=Path,
        default=None,
        help="Shard JSONL congelado opcional para enriquecer metadados dos clusters.",
    )
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=Path("./data/raw"),
        help="Diretório raiz de armazenamento (padrão: ./data/raw).",
    )
    parser.add_argument(
        "--run-id",
        default="yrp_pilot_propagacao_p1",
        help="Identificador da run gerada (padrão: yrp_pilot_propagacao_p1).",
    )
    parser.add_argument(
        "--region-code",
        default="BR",
        help="Region code das buscas importadas (padrão: BR).",
    )
    parser.add_argument(
        "--search-pages",
        type=int,
        default=1,
        help="Número de páginas configuradas no fingerprint do checkpoint (padrão: 1).",
    )
    parser.add_argument(
        "--search-max-results",
        type=int,
        default=50,
        help="maxResults configurado nas buscas (padrão: 50).",
    )
    parser.add_argument(
        "--default-arm",
        default=None,
        help="Braço experimental padrão caso o registro não especifique (ex.: propagacao).",
    )
    parser.add_argument(
        "--compress-responses",
        action="store_true",
        help="Salvar respostas importadas em .json.gz.",
    )
    return parser.parse_args()


def main() -> None:
    """Run pilot bootstrap CLI."""
    args = parse_args()
    pilot_files = resolve_pilot_files(args.pilot_jsonl, args.pilot_glob)
    summary = import_pilot_to_resumable_run(
        pilot_files=pilot_files,
        storage_root=args.storage_root,
        run_id=args.run_id,
        terms_file=args.terms_file,
        region_code=args.region_code,
        search_pages=args.search_pages,
        search_max_results=args.search_max_results,
        compress_responses=args.compress_responses,
        default_arm=args.default_arm,
    )
    print("=== BOOTSTRAP DO PILOTO CONCLUÍDO ===")
    print(f"Run ID                         : {summary['run_id']}")
    print(f"Queries importadas             : {summary['terms_processed']}")
    print(f"Pares vídeo-query (sightings)  : {summary['total_sightings']}")
    print(f"Vídeos únicos (seen_video_ids) : {summary['unique_videos_global']}")
    print(f"Canais únicos                  : {summary['unique_channels_global']}")
    print(f"Clusters com nextPageToken     : {summary['clusters_with_next_page_token']}")
    print(f"Follow-up shard (págs 2-3)     : {summary['followup_shard_file']}")


if __name__ == "__main__":
    main()
