"""Longitudinal wave collector (`videos.list` T0..T+180d and `channels.list` T0/M3/M6).

Reads unique video and channel IDs from discovery runs (`seen_video_ids` in checkpoints,
`cluster_sightings.jsonl`, `pilot_channel_ids.json`, and raw response payloads) or from
an explicit `--ids-file`, executes strict 50-ID batch sweeps via `YouTubeDataClient`,
and detects takedowns/bans by comparing requested IDs against returned `items[].id`,
logging missing or removed entities to `takedowns_ledger.jsonl`.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from youtube_data_pipeline import YouTubeDataClient, YouTubeDataConfig

CHECKPOINT_NAME = "longitudinal_wave_checkpoint.json"
SUMMARY_NAME = "longitudinal_wave_summary.json"
TAKEDOWNS_LEDGER_NAME = "takedowns_ledger.jsonl"

DEFAULT_VIDEOS_PART = "snippet,contentDetails,statistics,status,topicDetails"
DEFAULT_CHANNELS_PART = "snippet,statistics,contentDetails,status,topicDetails"


def iso_now() -> str:
    """Return current UTC timestamp in ISO 8601."""
    return datetime.now(timezone.utc).isoformat()


def chunked(items: Sequence[str], size: int = 50) -> Iterable[list[str]]:
    """Yield fixed-size chunks preserving input order (strict max 50 IDs)."""
    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def load_json_payload(path: Path) -> dict[str, Any]:
    """Load plain `.json` or compressed `.json.gz`."""
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8"))


def should_abort(result: dict[str, Any]) -> bool:
    """Return True when endpoint error policy requires stopping the run."""
    for error in result.get("errors", []):
        if error.get("action") == "stop":
            return True
    return False


def discover_ids_from_runs(
    source_runs: Sequence[Path],
    *,
    entity_type: str,
) -> list[str]:
    """Extract ordered unique `video` or `channel` IDs from one or more run directories."""
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(raw_id: str | None) -> None:
        if not raw_id:
            return
        val = str(raw_id).strip()
        if val and val not in seen:
            seen.add(val)
            ordered.append(val)

    for run_dir in source_runs:
        if not run_dir.exists():
            continue
        if entity_type == "video":
            cp_path = run_dir / "terms_collection_checkpoint.json"
            if cp_path.exists():
                cp = load_json_payload(cp_path)
                for vid in cp.get("seen_video_ids", []):
                    _add(vid)
            sightings_path = run_dir / "cluster_sightings.jsonl"
            if sightings_path.exists():
                for line in sightings_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    _add(row.get("video_id"))
            for ep_name in ("endpoint=search", "endpoint=playlistItems", "endpoint=videos"):
                ep_dir = run_dir / ep_name
                if not ep_dir.exists():
                    continue
                for page_file in sorted(ep_dir.rglob("page_*.json*")):
                    payload = load_json_payload(page_file)
                    for item in payload.get("items", []):
                        if not isinstance(item, Mapping):
                            continue
                        if ep_name == "endpoint=search":
                            _add((item.get("id") or {}).get("videoId"))
                        elif ep_name == "endpoint=playlistItems":
                            _add((item.get("contentDetails") or {}).get("videoId"))
                        else:
                            _add(item.get("id") if isinstance(item.get("id"), str) else None)
        elif entity_type == "channel":
            pilot_channels_path = run_dir / "pilot_channel_ids.json"
            if pilot_channels_path.exists():
                payload = load_json_payload(pilot_channels_path)
                for cid in payload.get("channel_ids", []):
                    _add(cid)
            sightings_path = run_dir / "cluster_sightings.jsonl"
            if sightings_path.exists():
                for line in sightings_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    _add(row.get("channel_id"))
            for ep_name in ("endpoint=search", "endpoint=videos", "endpoint=channels"):
                ep_dir = run_dir / ep_name
                if not ep_dir.exists():
                    continue
                for page_file in sorted(ep_dir.rglob("page_*.json*")):
                    payload = load_json_payload(page_file)
                    for item in payload.get("items", []):
                        if not isinstance(item, Mapping):
                            continue
                        if ep_name == "endpoint=channels":
                            _add(item.get("id") if isinstance(item.get("id"), str) else None)
                        else:
                            _add((item.get("snippet") or {}).get("channelId"))
        else:
            raise ValueError(f"entity_type inválido: {entity_type}")

    return ordered


def read_ids_file(path: Path, *, entity_type: str) -> list[str]:
    """Read unique video or channel IDs from `.txt` or `.jsonl`."""
    if not path.exists():
        raise FileNotFoundError(f"Arquivo de IDs não encontrado: {path}")
    ordered: list[str] = []
    seen: set[str] = set()
    key_candidates = (
        ("video_id", "videoId", "id")
        if entity_type == "video"
        else ("channel_id", "channelId", "id")
    )
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if path.suffix.lower() == ".jsonl" or line.startswith("{"):
            row = json.loads(line)
            val = ""
            for k in key_candidates:
                if row.get(k):
                    val = str(row[k]).strip()
                    break
        else:
            val = line
        if val and val not in seen:
            seen.add(val)
            ordered.append(val)
    return ordered


def detect_batch_takedowns(
    *,
    requested_ids: Sequence[str],
    responses: Sequence[Mapping[str, Any]],
    entity_type: str,
    wave: str,
    run_id: str,
    request_id: str,
    batch_index: int,
    detected_at_utc: str | None = None,
) -> list[dict[str, Any]]:
    """Compare 50 requested IDs against returned `items[].id` and flag takedowns/bans."""
    ts = detected_at_utc or iso_now()
    returned_by_id: dict[str, Mapping[str, Any]] = {}
    for resp in responses:
        for item in resp.get("items", []):
            if not isinstance(item, Mapping):
                continue
            item_id = item.get("id")
            if isinstance(item_id, str) and item_id.strip():
                returned_by_id[item_id.strip()] = item

    ledger_entries: list[dict[str, Any]] = []
    for req_id in requested_ids:
        item = returned_by_id.get(req_id)
        reason: str | None = None
        privacy_status: str | None = None
        upload_status: str | None = None
        if item is None:
            reason = "missing_from_batch_response"
        else:
            status_obj = item.get("status") if isinstance(item.get("status"), Mapping) else {}
            privacy_status = status_obj.get("privacyStatus")
            upload_status = status_obj.get("uploadStatus")
            if upload_status in {"rejected", "deleted"}:
                reason = f"upload_status_{upload_status}"
            elif privacy_status == "private":
                reason = "privacy_status_private"

        if reason is not None:
            entry: dict[str, Any] = {
                "entity_type": entity_type,
                "entity_id": req_id,
                "video_id": req_id if entity_type == "video" else None,
                "channel_id": req_id if entity_type == "channel" else None,
                "wave": wave,
                "reason": reason,
                "privacy_status": privacy_status,
                "upload_status": upload_status,
                "detected_at_utc": ts,
                "run_id": run_id,
                "request_id": request_id,
                "batch_index": batch_index,
            }
            ledger_entries.append(entry)
    return ledger_entries


def append_takedowns_ledger(path: Path, entries: Sequence[Mapping[str, Any]]) -> None:
    """Append detected takedown/ban records to `takedowns_ledger.jsonl`."""
    if not entries:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(dict(entry), ensure_ascii=False) + "\n")


def collect_longitudinal_wave(
    client: YouTubeDataClient,
    *,
    entity_ids: Sequence[str],
    entity_type: str = "video",
    wave: str = "T0",
    storage_root: Path = Path("./data/raw"),
    part: str | None = None,
    credit_limit: int | None = None,
    general_credit_limit: int | None = None,
    ledger_path: Path | None = None,
    summary_name: str = SUMMARY_NAME,
) -> tuple[dict[str, Any], Path]:
    """Execute a resumable 50-ID batch longitudinal sweep for `videos` or `channels`."""
    if entity_type not in {"video", "channel"}:
        raise ValueError(f"entity_type deve ser 'video' ou 'channel', recebido: {entity_type}")

    endpoint = "videos" if entity_type == "video" else "channels"
    effective_part = part or (
        DEFAULT_VIDEOS_PART if entity_type == "video" else DEFAULT_CHANNELS_PART
    )
    run_root = Path(storage_root).expanduser().resolve() / "runs" / client.run_id
    run_root.mkdir(parents=True, exist_ok=True)

    checkpoint_path = run_root / CHECKPOINT_NAME
    summary_path = run_root / summary_name
    effective_ledger_path = ledger_path or (run_root / TAKEDOWNS_LEDGER_NAME)

    unique_ids = list(dict.fromkeys(str(x).strip() for x in entity_ids if str(x).strip()))
    batches = list(chunked(unique_ids, 50))

    if checkpoint_path.exists():
        state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        completed_batches: set[int] = {int(x) for x in state.get("completed_batches", [])}
        takedowns_count = int(state.get("takedowns_count", 0))
        returned_count = int(state.get("returned_count", 0))
        started_at = str(state.get("started_at", iso_now()))
        credit_state = state.get("credit_state", {})
        client.credit_monitor.total_spent = int(credit_state.get("total_spent", 0))
        client.credit_monitor.per_endpoint = dict(credit_state.get("per_endpoint", {}))
        client.request_seq = int(state.get("request_seq", 0))
    else:
        completed_batches = set()
        takedowns_count = 0
        returned_count = 0
        started_at = iso_now()

    stop_reason: str | None = None

    for batch_index, batch in enumerate(batches, start=1):
        if batch_index in completed_batches:
            continue

        origin = json.dumps(
            {
                "phase": f"longitudinal_{endpoint}",
                "wave": wave,
                "batch": batch_index,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        result = client.fetch(
            endpoint,
            {
                "part": effective_part,
                "id": ",".join(batch),
                "maxResults": 50,
            },
            max_pages=1,
            credit_limit=credit_limit,
            general_credit_limit=general_credit_limit,
            origin=origin,
        )

        if result.get("limit_reached"):
            stop_reason = result.get("stopped_reason") or "credit_limit"
            break
        if should_abort(result):
            stop_reason = f"stop_action_on_{endpoint}"
            break

        responses = result.get("responses", [])
        for resp in responses:
            returned_count += len(resp.get("items", []))

        request_id = f"{client.run_id}-{client.request_seq:06d}"
        takedowns = detect_batch_takedowns(
            requested_ids=batch,
            responses=responses,
            entity_type=entity_type,
            wave=wave,
            run_id=client.run_id,
            request_id=request_id,
            batch_index=batch_index,
        )
        append_takedowns_ledger(effective_ledger_path, takedowns)
        if effective_ledger_path != (run_root / TAKEDOWNS_LEDGER_NAME):
            append_takedowns_ledger(run_root / TAKEDOWNS_LEDGER_NAME, takedowns)
        takedowns_count += len(takedowns)

        completed_batches.add(batch_index)
        now_ts = iso_now()
        atomic_write_json(
            checkpoint_path,
            {
                "schema_version": 1,
                "run_id": client.run_id,
                "entity_type": entity_type,
                "endpoint": endpoint,
                "wave": wave,
                "status": "running",
                "started_at": started_at,
                "updated_at": now_ts,
                "finished_at": None,
                "entities_total": len(unique_ids),
                "batches_total": len(batches),
                "completed_batches": sorted(completed_batches),
                "returned_count": returned_count,
                "takedowns_count": takedowns_count,
                "request_seq": client.request_seq,
                "credit_state": client.credit_monitor.snapshot(),
                "stop_reason": None,
            },
        )

    finished_at = iso_now()
    all_done = len(completed_batches) >= len(batches) and stop_reason is None
    status = "completed" if all_done else "stopped"

    final_checkpoint = {
        "schema_version": 1,
        "run_id": client.run_id,
        "entity_type": entity_type,
        "endpoint": endpoint,
        "wave": wave,
        "status": status,
        "started_at": started_at,
        "updated_at": finished_at,
        "finished_at": finished_at,
        "entities_total": len(unique_ids),
        "batches_total": len(batches),
        "completed_batches": sorted(completed_batches),
        "returned_count": returned_count,
        "takedowns_count": takedowns_count,
        "request_seq": client.request_seq,
        "credit_state": client.credit_monitor.snapshot(),
        "stop_reason": stop_reason,
    }
    atomic_write_json(checkpoint_path, final_checkpoint)

    summary_payload = {
        **final_checkpoint,
        "limit_reached": stop_reason
        in {
            "credit_limit",
            "search_credit_limit",
            "general_credit_limit",
            "endpoint_call_limit",
        },
        "takedowns_ledger_file": str(effective_ledger_path),
    }
    atomic_write_json(summary_path, summary_payload)
    return summary_payload, summary_path


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Coleta ondas longitudinais (T0..T+180d de videos.list e T0/M3/M6 de channels.list) "
            "em lotes estritos de 50 IDs, registrando remoções em takedowns_ledger.jsonl."
        )
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("YOUTUBE_API_KEY") or os.getenv("YOUTUBE_YRP_API_KEY"),
        help="API key do YouTube (ou defina YOUTUBE_API_KEY / YOUTUBE_YRP_API_KEY).",
    )
    parser.add_argument(
        "--entity-type",
        choices=("video", "channel"),
        default="video",
        help="Tipo de entidade a varrer em lotes de 50: 'video' ou 'channel'.",
    )
    parser.add_argument(
        "--wave",
        default="T0",
        help="Rótulo da onda longitudinal (ex.: T0, T+7d, T+30d, T+60d, T+120d, T+180d, M3, M6).",
    )
    parser.add_argument(
        "--source-run",
        action="append",
        default=[],
        help="Diretório de run ou run_id de onde extrair IDs descobertos (repita se necessário).",
    )
    parser.add_argument(
        "--ids-file",
        type=Path,
        default=None,
        help="Arquivo opcional (.txt ou .jsonl) com IDs de vídeo ou canal.",
    )
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=Path("./data/raw"),
        help="Diretório raiz de armazenamento (padrão: ./data/raw).",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Identificador da run longitudinal (reutilize para retomar).",
    )
    parser.add_argument(
        "--part",
        default=None,
        help="Parâmetro part customizado de videos.list ou channels.list.",
    )
    parser.add_argument(
        "--credit-limit",
        type=int,
        default=None,
        help="Limite total de créditos da sessão.",
    )
    parser.add_argument(
        "--general-credit-limit",
        type=int,
        default=None,
        help="Limite do bucket general_read (ex.: 50000 no perfil YRP).",
    )
    parser.add_argument(
        "--max-entities",
        type=int,
        default=None,
        help="Limita a quantidade de entidades processadas (útil para testes).",
    )
    parser.add_argument(
        "--compress-responses",
        action="store_true",
        help="Salvar respostas em .json.gz.",
    )
    return parser.parse_args()


def resolve_source_run_dirs(
    raw_runs: Sequence[str],
    storage_root: Path,
) -> list[Path]:
    """Resolve `--source-run` values as direct paths or run_ids under `storage_root/runs`."""
    dirs: list[Path] = []
    for item in raw_runs:
        candidate = Path(item).expanduser()
        if candidate.exists():
            dirs.append(candidate.resolve())
        else:
            under_root = storage_root.expanduser().resolve() / "runs" / item
            if under_root.exists():
                dirs.append(under_root)
    return dirs


def main() -> None:
    """Run longitudinal wave collection CLI."""
    args = parse_args()
    if not args.api_key:
        raise SystemExit("API key não informada. Use --api-key ou YOUTUBE_API_KEY.")

    entity_ids: list[str] = []
    if args.ids_file is not None:
        entity_ids.extend(read_ids_file(args.ids_file.expanduser(), entity_type=args.entity_type))
    if args.source_run:
        run_dirs = resolve_source_run_dirs(args.source_run, args.storage_root)
        entity_ids.extend(discover_ids_from_runs(run_dirs, entity_type=args.entity_type))

    entity_ids = list(dict.fromkeys(entity_ids))
    if args.max_entities is not None:
        entity_ids = entity_ids[: args.max_entities]
    if not entity_ids:
        raise SystemExit("Nenhum ID de entidade encontrado em --ids-file ou --source-run.")

    run_id = args.run_id or f"yrp_wave_{args.entity_type}_{args.wave.lower().replace('+', 'p')}"
    config = YouTubeDataConfig.for_yrp(
        api_key=args.api_key,
        storage_root=args.storage_root,
        run_id=run_id,
        compress_responses=args.compress_responses,
        general_credit_limit=args.general_credit_limit,
    )
    client = YouTubeDataClient.from_config(config)
    summary, summary_path = collect_longitudinal_wave(
        client,
        entity_ids=entity_ids,
        entity_type=args.entity_type,
        wave=args.wave,
        storage_root=args.storage_root,
        part=args.part,
        credit_limit=args.credit_limit,
        general_credit_limit=args.general_credit_limit,
    )
    print("=== ONDA LONGITUDINAL CONCLUÍDA ===")
    print(f"Run ID             : {summary['run_id']}")
    print(f"Entidade / Onda    : {summary['entity_type']} ({summary['wave']})")
    print(f"Status             : {summary['status']}")
    print(f"Lotes (50 IDs)     : {len(summary['completed_batches'])}/{summary['batches_total']}")
    print(f"Itens retornados   : {summary['returned_count']}/{summary['entities_total']}")
    print(f"Takedowns/Bans     : {summary['takedowns_count']}")
    print(f"Summary            : {summary_path}")


if __name__ == "__main__":
    main()
