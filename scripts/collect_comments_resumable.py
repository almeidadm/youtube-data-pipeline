"""Resumable social-reaction collector (`commentThreads.list` + `comments.list`).

Implements §6.3.5 of the YRP 6-month plan:
1) Filters eligible videos from `videos.list` runs where `statistics.commentCount > 0` and
   language matches `pt` (or reads from `--videos-file`).
2) Maintains an atomic checkpoint (`comments_collection_checkpoint.json`), resuming at the
   exact video/page/thread after credit limit stops or interruptions.
3) Collects up to `--comment-pages` (default 3, `maxResults=100`, `order=relevance`) per video
   and chains `comments.list` (`parentId`) for active threads (`totalReplyCount > 0`, up to
   `--max-reply-threads-per-video`, default 5) on the top `--max-reply-videos` (default 40,000)
   high-engagement videos.
4) Respects native `ErrorAction.SKIP` (`commentsDisabled`, `videoNotFound`, `commentNotFound`)
   without aborting the batch run.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from youtube_data_pipeline import YouTubeDataClient, YouTubeDataConfig

CHECKPOINT_NAME = "comments_collection_checkpoint.json"
SUMMARY_NAME = "comments_collection_summary.json"


@dataclass(slots=True)
class VideoCommentSpec:
    """Normalized specification for a video eligible for comment collection."""

    video_id: str
    comment_count: int = 0
    language: str | None = None
    collect_replies: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "comment_count": self.comment_count,
            "language": self.language,
            "collect_replies": self.collect_replies,
        }


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


def load_json_payload(path: Path) -> dict[str, Any]:
    """Load plain `.json` or compressed `.json.gz`."""
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8"))


def should_abort(result: dict[str, Any]) -> bool:
    """Return True only when endpoint error policy requires stopping the run."""
    for error in result.get("errors", []):
        if error.get("action") == "stop":
            return True
    return False


def extract_skip_reason(result: Mapping[str, Any]) -> str | None:
    """Return the skip error reason (e.g., `commentsDisabled`, `videoNotFound`) if present."""
    for error in result.get("errors", []):
        if error.get("action") == "skip":
            return str(error.get("reason") or "skipped")
    return None


def coerce_video_comment_spec(
    entry: str | VideoCommentSpec | Mapping[str, Any],
) -> VideoCommentSpec:
    """Coerce a string, dict, or VideoCommentSpec into a normalized VideoCommentSpec."""
    if isinstance(entry, VideoCommentSpec):
        return entry
    if isinstance(entry, str):
        vid = entry.strip()
        if not vid:
            raise ValueError("video_id vazio")
        return VideoCommentSpec(video_id=vid)
    if isinstance(entry, Mapping):
        vid = str(entry.get("video_id") or entry.get("videoId") or entry.get("id") or "").strip()
        if not vid:
            raise ValueError(f"Entrada sem video_id: {entry}")
        try:
            cc = int(entry.get("comment_count") or entry.get("commentCount") or 0)
        except (TypeError, ValueError):
            cc = 0
        lang = entry.get("language") or entry.get("defaultAudioLanguage")
        collect_replies = bool(entry.get("collect_replies", True))
        return VideoCommentSpec(
            video_id=vid,
            comment_count=cc,
            language=str(lang).strip() if lang else None,
            collect_replies=collect_replies,
        )
    raise TypeError(f"Tipo de vídeo não suportado: {type(entry)!r}")


def read_videos_file(path: Path) -> list[VideoCommentSpec]:
    """Read eligible video IDs from `.txt` or `.jsonl`."""
    if not path.exists():
        raise FileNotFoundError(f"Arquivo de vídeos não encontrado: {path}")
    specs: list[VideoCommentSpec] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if path.suffix.lower() == ".jsonl" or line.startswith("{"):
            spec = coerce_video_comment_spec(json.loads(line))
        else:
            spec = coerce_video_comment_spec(line)
        if spec.video_id in seen:
            continue
        seen.add(spec.video_id)
        specs.append(spec)
    return specs


def select_eligible_videos_from_runs(
    source_runs: Sequence[Path],
    *,
    min_comment_count: int = 1,
    language_prefix: str = "pt",
    allow_missing_language: bool = True,
    max_videos: int = 200_000,
    max_reply_videos: int = 40_000,
) -> list[VideoCommentSpec]:
    """Select eligible videos (`commentCount > 0` and language `pt`) from `videos.list` runs."""
    by_video: dict[str, VideoCommentSpec] = {}
    lang_pref = language_prefix.strip().lower()

    for run_dir in source_runs:
        v_dir = run_dir / "endpoint=videos"
        if not v_dir.exists():
            continue
        for page_file in sorted(v_dir.rglob("page_*.json*")):
            payload = load_json_payload(page_file)
            for item in payload.get("items", []):
                if not isinstance(item, Mapping):
                    continue
                vid = str(item.get("id") or "").strip()
                if not vid:
                    continue
                stats = (
                    item.get("statistics") if isinstance(item.get("statistics"), Mapping) else {}
                )
                raw_cc = stats.get("commentCount")
                try:
                    cc = int(raw_cc) if raw_cc is not None else 0
                except (TypeError, ValueError):
                    cc = 0
                if cc < min_comment_count:
                    continue

                snippet = item.get("snippet") if isinstance(item.get("snippet"), Mapping) else {}
                lang = str(
                    snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage") or ""
                ).strip()
                if lang_pref:
                    if lang:
                        if not lang.lower().startswith(lang_pref):
                            continue
                    elif not allow_missing_language:
                        continue

                existing = by_video.get(vid)
                if existing is None or cc > existing.comment_count:
                    by_video[vid] = VideoCommentSpec(
                        video_id=vid,
                        comment_count=cc,
                        language=lang or None,
                        collect_replies=False,
                    )

    ranked = sorted(
        by_video.values(),
        key=lambda s: (-s.comment_count, s.video_id),
    )[:max_videos]

    for idx, spec in enumerate(ranked):
        spec.collect_replies = idx < max_reply_videos
    return ranked


def extract_active_reply_parent_ids(
    thread_responses: Sequence[Mapping[str, Any]],
    *,
    max_threads: int = 5,
) -> list[str]:
    """Extract top-level comment IDs with `totalReplyCount > 0`, ranked descending."""
    candidates: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    seq = 0
    for resp in thread_responses:
        for item in resp.get("items", []):
            if not isinstance(item, Mapping):
                continue
            snippet = item.get("snippet") if isinstance(item.get("snippet"), Mapping) else {}
            try:
                reply_count = int(snippet.get("totalReplyCount") or 0)
            except (TypeError, ValueError):
                reply_count = 0
            if reply_count <= 0:
                continue
            top_comment = (
                snippet.get("topLevelComment")
                if isinstance(snippet.get("topLevelComment"), Mapping)
                else {}
            )
            parent_id = str(top_comment.get("id") or item.get("id") or "").strip()
            if not parent_id or parent_id in seen:
                continue
            seen.add(parent_id)
            seq += 1
            candidates.append((reply_count, -seq, parent_id))

    candidates.sort(reverse=True)
    return [parent_id for _, _, parent_id in candidates[:max_threads]]


def collect_comments_resumable(
    client: YouTubeDataClient,
    *,
    videos: Sequence[str | VideoCommentSpec | Mapping[str, Any]],
    storage_root: Path = Path("./data/raw"),
    comment_pages: int = 3,
    reply_pages: int = 1,
    max_reply_threads_per_video: int = 5,
    order: str = "relevance",
    wave: str = "M2_social",
    credit_limit: int | None = None,
    general_credit_limit: int | None = None,
    summary_name: str = SUMMARY_NAME,
) -> tuple[dict[str, Any], Path]:
    """Execute resumable collection of `commentThreads.list` and `comments.list`."""
    specs = [coerce_video_comment_spec(v) for v in videos]
    run_root = Path(storage_root).expanduser().resolve() / "runs" / client.run_id
    run_root.mkdir(parents=True, exist_ok=True)

    checkpoint_path = run_root / CHECKPOINT_NAME
    summary_path = run_root / summary_name

    if checkpoint_path.exists():
        state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        next_video_index = int(state.get("next_video_index", 0))
        per_video: list[dict[str, Any]] = list(state.get("per_video", []))
        active_video: dict[str, Any] | None = state.get("active_video")
        started_at = str(state.get("started_at", iso_now()))
        credit_state = state.get("credit_state", {})
        client.credit_monitor.total_spent = int(credit_state.get("total_spent", 0))
        client.credit_monitor.per_endpoint = dict(credit_state.get("per_endpoint", {}))
        client.request_seq = int(state.get("request_seq", 0))
    else:
        next_video_index = 0
        per_video = []
        active_video = None
        started_at = iso_now()

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
            "videos_total": len(specs),
            "videos_processed": len(per_video),
            "next_video_index": next_video_index,
            "comment_pages": comment_pages,
            "reply_pages": reply_pages,
            "max_reply_threads_per_video": max_reply_threads_per_video,
            "per_video": per_video,
            "active_video": active_video,
            "request_seq": client.request_seq,
            "credit_state": client.credit_monitor.snapshot(),
            "stop_reason": stop_reason,
        }
        atomic_write_json(checkpoint_path, payload)
        return payload

    for video_index in range(next_video_index, len(specs)):
        spec = specs[video_index]
        if active_video is None or active_video.get("video_id") != spec.video_id:
            active_video = {
                "video_index": video_index,
                "video_id": spec.video_id,
                "completed_thread_pages": [],
                "threads_complete": False,
                "next_page_token": None,
                "threads_collected": 0,
                "reply_parent_ids": [],
                "completed_reply_parents": [],
                "replies_collected": 0,
                "reply_calls": 0,
                "status": "running",
                "skip_reason": None,
            }
        _save_checkpoint("running")

        # Phase 1: commentThreads.list pagination
        if not active_video["threads_complete"]:
            page_idx = len(active_video["completed_thread_pages"]) + 1
            page_token = active_video["next_page_token"]
            collected_thread_responses: list[dict[str, Any]] = []

            while page_idx <= comment_pages:
                params: dict[str, Any] = {
                    "part": "snippet,replies",
                    "videoId": spec.video_id,
                    "maxResults": 100,
                    "order": order,
                    "textFormat": "plainText",
                }
                if page_token:
                    params["pageToken"] = page_token

                origin = json.dumps(
                    {
                        "phase": "comment_threads_page",
                        "video_id": spec.video_id,
                        "page": page_idx,
                        "wave": wave,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                result = client.fetch(
                    "commentThreads",
                    params,
                    max_pages=1,
                    credit_limit=credit_limit,
                    general_credit_limit=general_credit_limit,
                    origin=origin,
                )

                if result.get("limit_reached"):
                    stop_reason = result.get("stopped_reason") or "credit_limit"
                    _save_checkpoint("running")
                    break

                skip_reason = extract_skip_reason(result)
                if skip_reason is not None:
                    active_video["status"] = "skipped"
                    active_video["skip_reason"] = skip_reason
                    active_video["threads_complete"] = True
                    _save_checkpoint("running")
                    break

                if should_abort(result):
                    stop_reason = "stop_action_on_comment_threads"
                    _save_checkpoint("running")
                    break

                responses = result.get("responses", [])
                if responses:
                    resp = responses[0]
                    collected_thread_responses.append(resp)
                    active_video["completed_thread_pages"].append(page_idx)
                    active_video["threads_collected"] += len(resp.get("items", []))
                    page_token = resp.get("nextPageToken")
                    active_video["next_page_token"] = page_token

                if page_token is None or page_idx >= comment_pages:
                    active_video["threads_complete"] = True
                    if spec.collect_replies and max_reply_threads_per_video > 0:
                        new_parents = extract_active_reply_parent_ids(
                            collected_thread_responses,
                            max_threads=max_reply_threads_per_video,
                        )
                        existing_parents = list(active_video.get("reply_parent_ids", []))
                        for pid in new_parents:
                            if (
                                pid not in existing_parents
                                and len(existing_parents) < max_reply_threads_per_video
                            ):
                                existing_parents.append(pid)
                        active_video["reply_parent_ids"] = existing_parents
                    _save_checkpoint("running")
                    break

                if spec.collect_replies and max_reply_threads_per_video > 0:
                    new_parents = extract_active_reply_parent_ids(
                        collected_thread_responses,
                        max_threads=max_reply_threads_per_video,
                    )
                    existing_parents = list(active_video.get("reply_parent_ids", []))
                    for pid in new_parents:
                        if (
                            pid not in existing_parents
                            and len(existing_parents) < max_reply_threads_per_video
                        ):
                            existing_parents.append(pid)
                    active_video["reply_parent_ids"] = existing_parents

                _save_checkpoint("running")
                page_idx += 1

            if stop_reason is not None:
                break

        # Phase 2: comments.list for active threads on high-engagement videos
        if (
            active_video["status"] != "skipped"
            and spec.collect_replies
            and active_video.get("reply_parent_ids")
        ):
            completed_parents = set(active_video.get("completed_reply_parents", []))
            for parent_id in active_video["reply_parent_ids"]:
                if parent_id in completed_parents:
                    continue
                r_origin = json.dumps(
                    {
                        "phase": "comment_replies",
                        "video_id": spec.video_id,
                        "parent_id": parent_id,
                        "wave": wave,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                r_result = client.fetch(
                    "comments",
                    {
                        "part": "snippet",
                        "parentId": parent_id,
                        "maxResults": 100,
                        "textFormat": "plainText",
                    },
                    max_pages=reply_pages,
                    credit_limit=credit_limit,
                    general_credit_limit=general_credit_limit,
                    origin=r_origin,
                )
                if r_result.get("limit_reached"):
                    stop_reason = r_result.get("stopped_reason") or "credit_limit"
                    _save_checkpoint("running")
                    break
                if extract_skip_reason(r_result) is not None:
                    active_video["completed_reply_parents"].append(parent_id)
                    _save_checkpoint("running")
                    continue
                if should_abort(r_result):
                    stop_reason = "stop_action_on_comments"
                    _save_checkpoint("running")
                    break

                r_responses = r_result.get("responses", [])
                active_video["reply_calls"] += len(r_responses)
                for resp in r_responses:
                    active_video["replies_collected"] += len(resp.get("items", []))
                active_video["completed_reply_parents"].append(parent_id)
                _save_checkpoint("running")

            if stop_reason is not None:
                break

        final_video_status = "skipped" if active_video["status"] == "skipped" else "completed"
        per_video.append(
            {
                "video_index": video_index,
                "video_id": spec.video_id,
                "status": final_video_status,
                "skip_reason": active_video.get("skip_reason"),
                "thread_pages_collected": len(active_video["completed_thread_pages"]),
                "threads_collected": active_video["threads_collected"],
                "reply_calls": active_video["reply_calls"],
                "replies_collected": active_video["replies_collected"],
            }
        )
        active_video = None
        next_video_index = video_index + 1
        _save_checkpoint("running")

    all_done = next_video_index >= len(specs) and active_video is None and stop_reason is None
    final_status = "completed" if all_done else "stopped"
    final_cp = _save_checkpoint(final_status)

    summary_payload = {
        "run_id": client.run_id,
        "wave": wave,
        "status": final_status,
        "started_at": final_cp["started_at"],
        "finished_at": final_cp["finished_at"],
        "videos_total": len(specs),
        "videos_processed": len(per_video),
        "videos_skipped": sum(1 for v in per_video if v["status"] == "skipped"),
        "threads_collected_total": sum(int(v["threads_collected"]) for v in per_video),
        "replies_collected_total": sum(int(v["replies_collected"]) for v in per_video),
        "limit_reached": stop_reason
        in {
            "credit_limit",
            "search_credit_limit",
            "general_credit_limit",
            "endpoint_call_limit",
        },
        "stop_reason": stop_reason,
        "credit_state": final_cp["credit_state"],
        "per_video": per_video,
        "active_video": active_video,
    }
    atomic_write_json(summary_path, summary_payload)
    return summary_payload, summary_path


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Coleta retomável de commentThreads.list e comments.list para vídeos filtrados "
            "(commentCount > 0, idioma pt), com checkpoint atômico e skip para commentsDisabled."
        )
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("YOUTUBE_API_KEY") or os.getenv("YOUTUBE_YRP_API_KEY"),
        help="API key do YouTube (ou defina YOUTUBE_API_KEY / YOUTUBE_YRP_API_KEY).",
    )
    parser.add_argument(
        "--videos-file",
        type=Path,
        default=None,
        help="Arquivo opcional (.txt ou .jsonl) com IDs de vídeos elegíveis.",
    )
    parser.add_argument(
        "--source-run",
        action="append",
        default=[],
        help="Runs de videos.list de onde filtrar vídeos com commentCount > 0 e idioma pt.",
    )
    parser.add_argument(
        "--max-videos",
        type=int,
        default=200_000,
        help="Número máximo de vídeos para coleta de commentThreads (padrão: 200000).",
    )
    parser.add_argument(
        "--max-reply-videos",
        type=int,
        default=40_000,
        help="Top N vídeos de maior engajamento para encadear comments.list (padrão: 40000).",
    )
    parser.add_argument(
        "--max-reply-threads-per-video",
        type=int,
        default=5,
        help="Máximo de threads ativas (totalReplyCount > 0) por vídeo (padrão: 5).",
    )
    parser.add_argument(
        "--comment-pages",
        type=int,
        default=3,
        help="Máximo de páginas de commentThreads.list por vídeo (padrão: 3, maxResults=100).",
    )
    parser.add_argument(
        "--reply-pages",
        type=int,
        default=1,
        help="Máximo de páginas de comments.list por thread ativa (padrão: 1, maxResults=100).",
    )
    parser.add_argument(
        "--order",
        choices=("relevance", "time"),
        default="relevance",
        help="Critério de ordenação de commentThreads.list (padrão: relevance).",
    )
    parser.add_argument(
        "--wave",
        default="M2_social",
        help="Rótulo da onda (ex.: M2_social, M5_longitudinal_30k, M6_holdout_20k).",
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
    """Run resumable comment collection CLI."""
    args = parse_args()
    if not args.api_key:
        raise SystemExit("API key não informada. Use --api-key ou YOUTUBE_API_KEY.")

    videos: list[VideoCommentSpec] = []
    if args.videos_file is not None:
        videos.extend(read_videos_file(args.videos_file.expanduser()))
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
        videos.extend(
            select_eligible_videos_from_runs(
                run_dirs,
                max_videos=args.max_videos,
                max_reply_videos=args.max_reply_videos,
            )
        )

    deduped: list[VideoCommentSpec] = []
    seen: set[str] = set()
    for v in videos:
        if v.video_id not in seen:
            seen.add(v.video_id)
            deduped.append(v)
    deduped = deduped[: args.max_videos]
    if not deduped:
        raise SystemExit("Nenhum vídeo elegível encontrado em --videos-file ou --source-run.")

    run_id = args.run_id or f"yrp_comments_{args.wave.lower()}"
    config = YouTubeDataConfig.for_yrp(
        api_key=args.api_key,
        storage_root=args.storage_root,
        run_id=run_id,
        compress_responses=args.compress_responses,
        general_credit_limit=args.general_credit_limit,
    )
    client = YouTubeDataClient.from_config(config)
    summary, summary_path = collect_comments_resumable(
        client,
        videos=deduped,
        storage_root=args.storage_root,
        comment_pages=args.comment_pages,
        reply_pages=args.reply_pages,
        max_reply_threads_per_video=args.max_reply_threads_per_video,
        order=args.order,
        wave=args.wave,
        credit_limit=args.credit_limit,
        general_credit_limit=args.general_credit_limit,
    )
    print("=== COLETA DE COMENTÁRIOS CONCLUÍDA ===")
    print(f"Run ID              : {summary['run_id']}")
    print(f"Status              : {summary['status']}")
    print(f"Vídeos processados  : {summary['videos_processed']}/{summary['videos_total']}")
    print(f"Vídeos pulados      : {summary['videos_skipped']}")
    print(f"Threads coletadas   : {summary['threads_collected_total']}")
    print(f"Respostas coletadas : {summary['replies_collected_total']}")
    print(f"Summary             : {summary_path}")


if __name__ == "__main__":
    main()
