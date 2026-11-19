"""Collect uploads playlists (`playlistItems.list`) for recidivist and control channels.

Implements §6.3.4 of the YRP 6-month plan:
1) Selects up to 7,500 high-recurrence financial channels + matched control channels from
   `channels.list` and discovery runs (or from `--channels-file`), resolving each channel's
   `uploads_playlist_id` (`contentDetails.relatedPlaylists.uploads` or `UC...` -> `UU...`).
2) Collects up to `--playlist-pages` (default 3 pages = 150 videos/channel in Month 2 backfill;
   1 page in Months 4 and 6 incremental refreshes) via `playlistItems.list` (1 unit/call,
   100x cheaper than `search.list`).
3) Deduplicates discovered video IDs against `seen_video_ids` and resolves new videos in
   batches of 50 via `videos.list`.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from youtube_data_pipeline import YouTubeDataClient, YouTubeDataConfig

CHECKPOINT_NAME = "recidivist_playlists_checkpoint.json"
SUMMARY_NAME = "recidivist_playlists_summary.json"
PLAYLIST_SIGHTINGS_NAME = "channel_playlist_sightings.jsonl"
DEFAULT_VIDEOS_PART = "snippet,contentDetails,statistics,status,topicDetails"


@dataclass(slots=True)
class ChannelSpec:
    """Normalized specification for a recidivist or matched control channel."""

    channel_id: str
    uploads_playlist_id: str
    role: str = "recidivist"
    cluster_count: int = 0
    video_count_in_discovery: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "uploads_playlist_id": self.uploads_playlist_id,
            "role": self.role,
            "cluster_count": self.cluster_count,
            "video_count_in_discovery": self.video_count_in_discovery,
        }


def iso_now() -> str:
    """Return current UTC timestamp in ISO 8601."""
    return datetime.now(timezone.utc).isoformat()


def chunked(items: Sequence[str], size: int = 50) -> Iterable[list[str]]:
    """Yield fixed-size chunks preserving input order."""
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


def derive_uploads_playlist_id(channel_id: str, explicit_playlist_id: str | None = None) -> str:
    """Return explicit `uploads_playlist_id` or canonical `UU...` derived from `UC...`."""
    if explicit_playlist_id and explicit_playlist_id.strip():
        return explicit_playlist_id.strip()
    cid = channel_id.strip()
    if cid.startswith("UC") and len(cid) > 2:
        return "UU" + cid[2:]
    return cid


def coerce_channel_spec(entry: str | ChannelSpec | Mapping[str, Any]) -> ChannelSpec:
    """Coerce a string, dict, or ChannelSpec into a normalized ChannelSpec."""
    if isinstance(entry, ChannelSpec):
        return entry
    if isinstance(entry, str):
        cid = entry.strip()
        if not cid:
            raise ValueError("channel_id vazio")
        return ChannelSpec(
            channel_id=cid,
            uploads_playlist_id=derive_uploads_playlist_id(cid),
        )
    if isinstance(entry, Mapping):
        cid = str(
            entry.get("channel_id") or entry.get("channelId") or entry.get("id") or ""
        ).strip()
        if not cid:
            raise ValueError(f"Entrada de canal sem channel_id: {entry}")
        explicit_pl = (
            entry.get("uploads_playlist_id")
            or entry.get("uploadsPlaylistId")
            or (
                ((entry.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
                if isinstance(entry.get("contentDetails"), Mapping)
                else None
            )
        )
        return ChannelSpec(
            channel_id=cid,
            uploads_playlist_id=derive_uploads_playlist_id(
                cid,
                str(explicit_pl) if explicit_pl else None,
            ),
            role=str(entry.get("role") or "recidivist").strip(),
            cluster_count=int(entry.get("cluster_count") or 0),
            video_count_in_discovery=int(entry.get("video_count_in_discovery") or 0),
        )
    raise TypeError(f"Tipo de canal não suportado: {type(entry)!r}")


def read_channels_file(path: Path) -> list[ChannelSpec]:
    """Read channels from `.txt` or `.jsonl` file."""
    if not path.exists():
        raise FileNotFoundError(f"Arquivo de canais não encontrado: {path}")
    specs: list[ChannelSpec] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if path.suffix.lower() == ".jsonl" or line.startswith("{"):
            spec = coerce_channel_spec(json.loads(line))
        else:
            spec = coerce_channel_spec(line)
        if spec.channel_id in seen:
            continue
        seen.add(spec.channel_id)
        specs.append(spec)
    return specs


def select_recidivist_channels_from_runs(
    source_runs: Sequence[Path],
    *,
    max_channels: int = 7500,
) -> list[ChannelSpec]:
    """Rank and select top recidivist + control channels from discovery and channels runs."""
    uploads_by_channel: dict[str, str] = {}
    clusters_by_channel: dict[str, set[str]] = {}
    videos_by_channel: dict[str, set[str]] = {}
    video_to_channel: dict[str, str] = {}

    for run_dir in source_runs:
        if not run_dir.exists():
            continue
        # 1) Read channels.list payloads for uploads_playlist_id
        ch_dir = run_dir / "endpoint=channels"
        if ch_dir.exists():
            for page_file in sorted(ch_dir.rglob("page_*.json*")):
                payload = load_json_payload(page_file)
                for item in payload.get("items", []):
                    if not isinstance(item, Mapping):
                        continue
                    cid = str(item.get("id") or "").strip()
                    if not cid:
                        continue
                    uploads = (
                        (item.get("contentDetails") or {}).get("relatedPlaylists") or {}
                    ).get("uploads")
                    if uploads:
                        uploads_by_channel[cid] = str(uploads).strip()
                    clusters_by_channel.setdefault(cid, set())
                    videos_by_channel.setdefault(cid, set())

        # 2) Read videos.list and search.list payloads for video -> channel mapping
        for ep_name in ("endpoint=videos", "endpoint=search"):
            ep_dir = run_dir / ep_name
            if not ep_dir.exists():
                continue
            for page_file in sorted(ep_dir.rglob("page_*.json*")):
                payload = load_json_payload(page_file)
                for item in payload.get("items", []):
                    if not isinstance(item, Mapping):
                        continue
                    if ep_name == "endpoint=search":
                        vid = str((item.get("id") or {}).get("videoId") or "").strip()
                    else:
                        vid = str(item.get("id") or "").strip()
                    cid = str((item.get("snippet") or {}).get("channelId") or "").strip()
                    if vid and cid:
                        video_to_channel[vid] = cid
                        videos_by_channel.setdefault(cid, set()).add(vid)
                        clusters_by_channel.setdefault(cid, set())

        # 3) Read cluster_sightings.jsonl for cluster recurrence per channel
        sightings_path = run_dir / "cluster_sightings.jsonl"
        if sightings_path.exists():
            for line in sightings_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                vid = str(row.get("video_id") or "").strip()
                cid = str(row.get("channel_id") or video_to_channel.get(vid) or "").strip()
                cluster_id = str(row.get("claim_cluster_id") or "").strip()
                if cid:
                    clusters_by_channel.setdefault(cid, set())
                    videos_by_channel.setdefault(cid, set())
                    if vid:
                        videos_by_channel[cid].add(vid)
                    if cluster_id:
                        clusters_by_channel[cid].add(cluster_id)

    all_channels = sorted(
        clusters_by_channel.keys(),
        key=lambda cid: (
            -len(clusters_by_channel.get(cid, set())),
            -len(videos_by_channel.get(cid, set())),
            cid,
        ),
    )

    selected: list[ChannelSpec] = []
    for cid in all_channels[:max_channels]:
        n_clusters = len(clusters_by_channel.get(cid, set()))
        n_videos = len(videos_by_channel.get(cid, set()))
        role = "recidivist" if n_clusters >= 2 or n_videos >= 2 else "control"
        selected.append(
            ChannelSpec(
                channel_id=cid,
                uploads_playlist_id=derive_uploads_playlist_id(cid, uploads_by_channel.get(cid)),
                role=role,
                cluster_count=n_clusters,
                video_count_in_discovery=n_videos,
            )
        )
    return selected


def extract_playlist_video_ids(responses: Sequence[Mapping[str, Any]]) -> list[str]:
    """Extract unique video IDs from `playlistItems.list` responses preserving order."""
    ordered: list[str] = []
    seen: set[str] = set()
    for resp in responses:
        for item in resp.get("items", []):
            if not isinstance(item, Mapping):
                continue
            vid = str(
                (item.get("contentDetails") or {}).get("videoId")
                or ((item.get("snippet") or {}).get("resourceId") or {}).get("videoId")
                or ""
            ).strip()
            if vid and vid not in seen:
                seen.add(vid)
                ordered.append(vid)
    return ordered


def collect_recidivist_channels_playlists(
    client: YouTubeDataClient,
    *,
    channels: Sequence[str | ChannelSpec | Mapping[str, Any]],
    storage_root: Path = Path("./data/raw"),
    playlist_pages: int = 3,
    resolve_videos: bool = True,
    videos_part: str = DEFAULT_VIDEOS_PART,
    wave: str = "M2_backfill",
    credit_limit: int | None = None,
    general_credit_limit: int | None = None,
    initial_seen_video_ids: Sequence[str] | None = None,
    summary_name: str = SUMMARY_NAME,
) -> tuple[dict[str, Any], Path]:
    """Collect `playlistItems.list` for recidivist/control channels and resolve new videos."""
    specs = [coerce_channel_spec(ch) for ch in channels]
    run_root = Path(storage_root).expanduser().resolve() / "runs" / client.run_id
    run_root.mkdir(parents=True, exist_ok=True)

    checkpoint_path = run_root / CHECKPOINT_NAME
    summary_path = run_root / summary_name
    sightings_path = run_root / PLAYLIST_SIGHTINGS_NAME

    if checkpoint_path.exists():
        state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        next_channel_index = int(state.get("next_channel_index", 0))
        seen_video_ids: list[str] = [str(v) for v in state.get("seen_video_ids", [])]
        pending_video_ids: list[str] = [str(v) for v in state.get("pending_video_ids", [])]
        completed_video_batches: set[int] = {
            int(b) for b in state.get("completed_video_batches", [])
        }
        per_channel: list[dict[str, Any]] = list(state.get("per_channel", []))
        started_at = str(state.get("started_at", iso_now()))
        credit_state = state.get("credit_state", {})
        client.credit_monitor.total_spent = int(credit_state.get("total_spent", 0))
        client.credit_monitor.per_endpoint = dict(credit_state.get("per_endpoint", {}))
        client.request_seq = int(state.get("request_seq", 0))
    else:
        next_channel_index = 0
        seen_video_ids = list(dict.fromkeys(str(v) for v in (initial_seen_video_ids or [])))
        pending_video_ids = []
        completed_video_batches = set()
        per_channel = []
        started_at = iso_now()

    seen_video_set = set(seen_video_ids)
    pending_video_set = set(pending_video_ids)
    stop_reason: str | None = None

    def _save_checkpoint(status: str) -> dict[str, Any]:
        now_ts = iso_now()
        payload = {
            "schema_version": 1,
            "run_id": client.run_id,
            "wave": wave,
            "status": status,
            "started_at": started_at,
            "updated_at": now_ts,
            "finished_at": now_ts if status != "running" else None,
            "channels_total": len(specs),
            "channels_processed": len(per_channel),
            "next_channel_index": next_channel_index,
            "playlist_pages": playlist_pages,
            "seen_video_ids": seen_video_ids,
            "pending_video_ids": pending_video_ids,
            "completed_video_batches": sorted(completed_video_batches),
            "per_channel": per_channel,
            "request_seq": client.request_seq,
            "credit_state": client.credit_monitor.snapshot(),
            "stop_reason": stop_reason,
        }
        atomic_write_json(checkpoint_path, payload)
        return payload

    for idx in range(next_channel_index, len(specs)):
        spec = specs[idx]
        origin = json.dumps(
            {
                "phase": "recidivist_playlist",
                "channel_id": spec.channel_id,
                "playlist_id": spec.uploads_playlist_id,
                "wave": wave,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        pl_result = client.fetch(
            "playlistItems",
            {
                "part": "snippet,contentDetails,status",
                "playlistId": spec.uploads_playlist_id,
                "maxResults": 50,
            },
            max_pages=playlist_pages,
            credit_limit=credit_limit,
            general_credit_limit=general_credit_limit,
            origin=origin,
        )

        if pl_result.get("limit_reached"):
            stop_reason = pl_result.get("stopped_reason") or "credit_limit"
            break
        if should_abort(pl_result):
            stop_reason = "stop_action_on_playlist_items"
            break

        responses = pl_result.get("responses", [])
        discovered_ids = extract_playlist_video_ids(responses)
        new_ids: list[str] = []
        for vid in discovered_ids:
            if vid not in seen_video_set:
                seen_video_set.add(vid)
                seen_video_ids.append(vid)
                new_ids.append(vid)
            if resolve_videos and vid not in pending_video_set and vid in new_ids:
                pending_video_set.add(vid)
                pending_video_ids.append(vid)

        if discovered_ids:
            collected_at = iso_now()
            with sightings_path.open("a", encoding="utf-8") as handle:
                for rank_in_channel, vid in enumerate(discovered_ids, start=1):
                    handle.write(
                        json.dumps(
                            {
                                "run_id": client.run_id,
                                "collected_at": collected_at,
                                "wave": wave,
                                "channel_id": spec.channel_id,
                                "uploads_playlist_id": spec.uploads_playlist_id,
                                "role": spec.role,
                                "cluster_count": spec.cluster_count,
                                "rank_in_channel": rank_in_channel,
                                "video_id": vid,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

        per_channel.append(
            {
                "channel_index": idx,
                "channel_id": spec.channel_id,
                "uploads_playlist_id": spec.uploads_playlist_id,
                "role": spec.role,
                "pages_collected": len(responses),
                "videos_discovered": len(discovered_ids),
                "new_unique_videos": len(new_ids),
            }
        )
        next_channel_index = idx + 1
        _save_checkpoint("running")

    # Resolve new videos in batches of 50 via videos.list
    video_batches = list(chunked(pending_video_ids, 50)) if resolve_videos else []
    if stop_reason is None and resolve_videos and video_batches:
        for batch_idx, batch in enumerate(video_batches, start=1):
            if batch_idx in completed_video_batches:
                continue
            v_origin = json.dumps(
                {
                    "phase": "recidivist_videos_batch",
                    "wave": wave,
                    "batch": batch_idx,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            v_result = client.fetch(
                "videos",
                {
                    "part": videos_part,
                    "id": ",".join(batch),
                    "maxResults": 50,
                },
                max_pages=1,
                credit_limit=credit_limit,
                general_credit_limit=general_credit_limit,
                origin=v_origin,
            )
            if v_result.get("limit_reached"):
                stop_reason = v_result.get("stopped_reason") or "credit_limit"
                break
            if should_abort(v_result):
                stop_reason = "stop_action_on_videos"
                break
            completed_video_batches.add(batch_idx)
            _save_checkpoint("running")

    all_done = (
        next_channel_index >= len(specs)
        and (not resolve_videos or len(completed_video_batches) >= len(video_batches))
        and stop_reason is None
    )
    final_status = "completed" if all_done else "stopped"
    final_cp = _save_checkpoint(final_status)

    summary_payload = {
        "run_id": client.run_id,
        "wave": wave,
        "status": final_status,
        "started_at": final_cp["started_at"],
        "finished_at": final_cp["finished_at"],
        "channels_total": len(specs),
        "channels_processed": len(per_channel),
        "unique_videos_global": len(seen_video_ids),
        "new_videos_queued_for_details": len(pending_video_ids),
        "video_batches_total": len(video_batches),
        "video_batches_completed": len(completed_video_batches),
        "limit_reached": stop_reason
        in {
            "credit_limit",
            "search_credit_limit",
            "general_credit_limit",
            "endpoint_call_limit",
        },
        "stop_reason": stop_reason,
        "credit_state": final_cp["credit_state"],
        "per_channel": per_channel,
    }
    atomic_write_json(summary_path, summary_payload)
    return summary_payload, summary_path


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Coleta playlistItems.list para até 7.500 canais reincidentes e controles pareados "
            "(economia de 100x sobre search.list) e resolve metadados via videos.list (batch 50)."
        )
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("YOUTUBE_API_KEY") or os.getenv("YOUTUBE_YRP_API_KEY"),
        help="API key do YouTube (ou defina YOUTUBE_API_KEY / YOUTUBE_YRP_API_KEY).",
    )
    parser.add_argument(
        "--channels-file",
        type=Path,
        default=None,
        help="Arquivo opcional (.txt ou .jsonl) com canais selecionados.",
    )
    parser.add_argument(
        "--source-run",
        action="append",
        default=[],
        help="Runs de descoberta/channels de onde ranquear canais reincidentes.",
    )
    parser.add_argument(
        "--max-channels",
        type=int,
        default=7500,
        help="Número máximo de canais selecionados (padrão: 7500).",
    )
    parser.add_argument(
        "--playlist-pages",
        type=int,
        default=3,
        help="Páginas de playlistItems.list por canal (padrão: 3 no M2; use 1 no M4/M6).",
    )
    parser.add_argument(
        "--wave",
        default="M2_backfill",
        help="Identificador da onda (ex.: M2_backfill, M4_refresh, M6_refresh).",
    )
    parser.add_argument(
        "--no-resolve-videos",
        dest="resolve_videos",
        action="store_false",
        default=True,
        help="Não chamar videos.list após playlistItems.list.",
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
        help="Identificador da run (reutilize para retomar).",
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
        "--compress-responses",
        action="store_true",
        help="Salvar respostas em .json.gz.",
    )
    return parser.parse_args()


def main() -> None:
    """Run recidivist channels playlist collection CLI."""
    args = parse_args()
    if not args.api_key:
        raise SystemExit("API key não informada. Use --api-key ou YOUTUBE_API_KEY.")

    channels: list[ChannelSpec] = []
    if args.channels_file is not None:
        channels.extend(read_channels_file(args.channels_file.expanduser()))
    if args.source_run:
        run_dirs: list[Path] = []
        for item in args.source_run:
            p = Path(item).expanduser()
            if p.exists():
                run_dirs.append(p.resolve())
            else:
                candidate = args.storage_root.expanduser().resolve() / "runs" / item
                if candidate.exists():
                    run_dirs.append(candidate)
        channels.extend(
            select_recidivist_channels_from_runs(run_dirs, max_channels=args.max_channels)
        )

    # Deduplicate preserving order
    deduped: list[ChannelSpec] = []
    seen_cids: set[str] = set()
    for ch in channels:
        if ch.channel_id not in seen_cids:
            seen_cids.add(ch.channel_id)
            deduped.append(ch)
    deduped = deduped[: args.max_channels]
    if not deduped:
        raise SystemExit("Nenhum canal encontrado em --channels-file ou --source-run.")

    run_id = args.run_id or f"yrp_recidivist_playlists_{args.wave.lower()}"
    config = YouTubeDataConfig.for_yrp(
        api_key=args.api_key,
        storage_root=args.storage_root,
        run_id=run_id,
        compress_responses=args.compress_responses,
        general_credit_limit=args.general_credit_limit,
    )
    client = YouTubeDataClient.from_config(config)
    summary, summary_path = collect_recidivist_channels_playlists(
        client,
        channels=deduped,
        storage_root=args.storage_root,
        playlist_pages=args.playlist_pages,
        resolve_videos=args.resolve_videos,
        wave=args.wave,
        credit_limit=args.credit_limit,
        general_credit_limit=args.general_credit_limit,
    )
    print("=== COLETA DE PLAYLISTS DE CANAIS REINCIDENTES CONCLUÍDA ===")
    print(f"Run ID              : {summary['run_id']}")
    print(f"Status              : {summary['status']}")
    print(f"Canais processados  : {summary['channels_processed']}/{summary['channels_total']}")
    print(f"Vídeos únicos       : {summary['unique_videos_global']}")
    v_done = summary["video_batches_completed"]
    v_tot = summary["video_batches_total"]
    print(f"Lotes videos.list   : {v_done}/{v_tot}")
    print(f"Summary             : {summary_path}")


if __name__ == "__main__":
    main()
