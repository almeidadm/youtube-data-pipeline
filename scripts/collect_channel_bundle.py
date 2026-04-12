"""Collect a complete YouTube channel bundle using YouTube Data API v3.

Flow:
1) channel account data (channels)
2) channel posts/activity feed (activities)
3) uploads playlist items (playlistItems)
4) video details/metrics (videos)
5) engagement by comments and replies (commentThreads + comments)

All responses are persisted by youtube_data_pipeline under:
{storage_root}/runs/{run_id}/...
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from youtube_data_pipeline import YouTubeDataClient, YouTubeDataConfig
from youtube_data_pipeline.endpoints.definitions import EndpointDefinition


def chunked(items: Sequence[str], size: int) -> Iterable[list[str]]:
    """Yield fixed-size chunks preserving input order."""
    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def should_abort(result: dict[str, Any]) -> bool:
    """Return True only when endpoint policy indicates a stop action."""
    for error in result.get("errors", []):
        if error.get("action") == "stop":
            return True
    return False


def count_items(responses: Sequence[dict[str, Any]]) -> int:
    """Count total items across paginated API responses."""
    total = 0
    for response in responses:
        total += len(response.get("items", []))
    return total


def extract_first_channel_item(responses: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the first channel item found across paginated responses."""
    for response in responses:
        items = response.get("items", [])
        if items:
            return items[0]
    return None


def extract_uploads_playlist_id(channel_item: dict[str, Any]) -> str | None:
    """Extract uploads playlist ID from a channels.list item."""
    return (
        channel_item.get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads")
    )


def extract_playlist_video_ids(
    playlist_responses: Sequence[dict[str, Any]],
    *,
    max_videos: int,
) -> list[str]:
    """Extract unique video IDs from playlistItems preserving first occurrence order."""
    ordered_ids: list[str] = []
    seen: set[str] = set()

    for response in playlist_responses:
        for item in response.get("items", []):
            video_id = item.get("contentDetails", {}).get("videoId")
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)
            ordered_ids.append(video_id)
            if len(ordered_ids) >= max_videos:
                return ordered_ids
    return ordered_ids


def extract_top_level_comment_ids(comment_threads_responses: Sequence[dict[str, Any]]) -> list[str]:
    """Extract top-level comment IDs from commentThreads responses."""
    ordered_ids: list[str] = []
    seen: set[str] = set()

    for response in comment_threads_responses:
        for item in response.get("items", []):
            comment_id = (
                item.get("snippet", {})
                .get("topLevelComment", {})
                .get("id")
            )
            if not comment_id or comment_id in seen:
                continue
            seen.add(comment_id)
            ordered_ids.append(comment_id)
    return ordered_ids


def parse_metric(value: Any) -> int:
    """Parse API metric values that may arrive as numeric strings."""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def sum_video_statistics(video_responses: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Aggregate key video statistics across videos.list responses."""
    totals = {
        "view_count_total": 0,
        "like_count_total": 0,
        "comment_count_total": 0,
    }

    for response in video_responses:
        for item in response.get("items", []):
            stats = item.get("statistics", {})
            totals["view_count_total"] += parse_metric(stats.get("viewCount"))
            totals["like_count_total"] += parse_metric(stats.get("likeCount"))
            totals["comment_count_total"] += parse_metric(stats.get("commentCount"))
    return totals


def register_script_endpoints(client: YouTubeDataClient) -> None:
    """Register endpoints required by this script without changing client defaults."""
    client.register_endpoint(
        EndpointDefinition(
            name="activities",
            resource="activities",
            base_credit_cost=1,
            required_params={"part"},
            optional_params={
                "channelId",
                "home",
                "maxResults",
                "mine",
                "pageToken",
                "publishedAfter",
                "publishedBefore",
                "regionCode",
            },
        )
    )
    client.register_endpoint(
        EndpointDefinition(
            name="playlistItems",
            resource="playlistItems",
            base_credit_cost=1,
            required_params={"part", "playlistId"},
            optional_params={"id", "maxResults", "pageToken", "videoId"},
        )
    )


def validate_args(args: argparse.Namespace) -> None:
    """Validate CLI argument ranges."""
    if not args.api_key:
        raise SystemExit("API key não informada. Use --api-key ou YOUTUBE_API_KEY.")
    if args.credit_limit < 1:
        raise SystemExit("--credit-limit deve ser >= 1.")
    if args.activities_pages < 1:
        raise SystemExit("--activities-pages deve ser >= 1.")
    if args.activities_max_results < 1 or args.activities_max_results > 50:
        raise SystemExit("--activities-max-results deve estar entre 1 e 50.")
    if args.video_pages < 1:
        raise SystemExit("--video-pages deve ser >= 1.")
    if args.max_videos < 1:
        raise SystemExit("--max-videos deve ser >= 1.")
    if args.comment_pages < 1:
        raise SystemExit("--comment-pages deve ser >= 1.")
    if args.reply_pages < 1:
        raise SystemExit("--reply-pages deve ser >= 1.")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Coleta um pacote completo de dados de um canal (conta, postagens, "
            "vídeos e engajamento), persistindo respostas por run."
        )
    )
    parser.add_argument(
        "--channel-id",
        required=True,
        help="ID do canal (ex.: UC_x5XG1OV2P6uZZ5FSM9Ttw).",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("YOUTUBE_API_KEY"),
        help="API key do YouTube (ou defina YOUTUBE_API_KEY).",
    )
    parser.add_argument(
        "--storage-root",
        default="./data/raw",
        help="Diretório para armazenar respostas e artefatos.",
    )
    parser.add_argument(
        "--log-path",
        default="youtube_data_pipeline.jsonl",
        help="Nome base do arquivo JSONL de logs dentro do diretório da run.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Identificador da execução (opcional).",
    )
    parser.add_argument(
        "--compress-responses",
        action="store_true",
        help="Salvar respostas comprimidas (.json.gz).",
    )
    parser.add_argument(
        "--credit-limit",
        type=int,
        default=10_000,
        help="Limite total de créditos da execução.",
    )
    parser.add_argument(
        "--activities-pages",
        type=int,
        default=5,
        help="Número máximo de páginas de activities.list.",
    )
    parser.add_argument(
        "--activities-max-results",
        type=int,
        default=50,
        help="Quantidade de itens por página em activities.list (máximo: 50).",
    )
    parser.add_argument(
        "--video-pages",
        type=int,
        default=10,
        help="Número máximo de páginas de playlistItems.list (uploads do canal).",
    )
    parser.add_argument(
        "--max-videos",
        type=int,
        default=200,
        help="Número máximo de vídeos únicos para coletar detalhes/engajamento.",
    )
    parser.add_argument(
        "--comment-pages",
        type=int,
        default=2,
        help="Número máximo de páginas de commentThreads por vídeo.",
    )
    parser.add_argument(
        "--reply-pages",
        type=int,
        default=1,
        help="Número máximo de páginas de replies por comentário de topo.",
    )
    parser.add_argument(
        "--summary-name",
        default="channel_collection_summary.json",
        help="Nome do arquivo de resumo em {storage_root}/runs/{run_id}/.",
    )
    return parser.parse_args()


def collect_channel_bundle(
    client: YouTubeDataClient,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], Path]:
    """Execute channel bundle collection and save a run summary file."""
    started_at = datetime.now(timezone.utc)

    stop_reason: str | None = None
    limit_hit = False
    uploads_playlist_id: str | None = None

    counts: dict[str, int] = {
        "channels_pages_collected": 0,
        "channel_items_collected": 0,
        "activities_pages_collected": 0,
        "activities_items_collected": 0,
        "playlist_pages_collected": 0,
        "playlist_items_collected": 0,
        "playlist_unique_videos": 0,
        "videos_batches": 0,
        "videos_items_collected": 0,
        "comment_threads_pages_collected": 0,
        "comment_threads_items_collected": 0,
        "top_level_comments_collected": 0,
        "reply_pages_collected": 0,
        "reply_items_collected": 0,
    }
    totals = {
        "view_count_total": 0,
        "like_count_total": 0,
        "comment_count_total": 0,
    }

    selected_video_ids: list[str] = []

    channels_result = client.fetch(
        "channels",
        {
            "part": "snippet,statistics,contentDetails,status",
            "id": args.channel_id,
            "maxResults": 1,
        },
        max_pages=1,
        credit_limit=args.credit_limit,
        origin=args.channel_id,
    )
    counts["channels_pages_collected"] = len(channels_result.get("responses", []))
    counts["channel_items_collected"] = count_items(channels_result.get("responses", []))

    if channels_result.get("limit_reached"):
        limit_hit = True
        stop_reason = "credit_limit"
    elif should_abort(channels_result):
        stop_reason = "stop_action_on_channels"

    channel_item = extract_first_channel_item(channels_result.get("responses", []))
    if stop_reason is None and channel_item is None:
        stop_reason = "channel_not_found"
    if channel_item is not None:
        uploads_playlist_id = extract_uploads_playlist_id(channel_item)

    if stop_reason is None:
        activities_result = client.fetch(
            "activities",
            {
                "part": "snippet,contentDetails",
                "channelId": args.channel_id,
                "maxResults": args.activities_max_results,
            },
            max_pages=args.activities_pages,
            credit_limit=args.credit_limit,
            origin=f"{args.channel_id}:activities",
        )
        counts["activities_pages_collected"] = len(activities_result.get("responses", []))
        counts["activities_items_collected"] = count_items(activities_result.get("responses", []))

        if activities_result.get("limit_reached"):
            limit_hit = True
            stop_reason = "credit_limit"
        elif should_abort(activities_result):
            stop_reason = "stop_action_on_activities"

    if stop_reason is None and uploads_playlist_id:
        playlist_result = client.fetch(
            "playlistItems",
            {
                "part": "snippet,contentDetails,status",
                "playlistId": uploads_playlist_id,
                "maxResults": 50,
            },
            max_pages=args.video_pages,
            credit_limit=args.credit_limit,
            origin=f"{args.channel_id}:uploads_playlist",
        )
        playlist_responses = playlist_result.get("responses", [])
        counts["playlist_pages_collected"] = len(playlist_responses)
        counts["playlist_items_collected"] = count_items(playlist_responses)

        if playlist_result.get("limit_reached"):
            limit_hit = True
            stop_reason = "credit_limit"
        elif should_abort(playlist_result):
            stop_reason = "stop_action_on_playlist_items"
        else:
            selected_video_ids = extract_playlist_video_ids(
                playlist_responses,
                max_videos=args.max_videos,
            )
            counts["playlist_unique_videos"] = len(selected_video_ids)

    if stop_reason is None and selected_video_ids:
        for batch_idx, batch in enumerate(chunked(selected_video_ids, 50), start=1):
            videos_result = client.fetch(
                "videos",
                {
                    "part": "snippet,statistics,contentDetails",
                    "id": ",".join(batch),
                    "maxResults": 50,
                },
                max_pages=1,
                credit_limit=args.credit_limit,
                origin=f"{args.channel_id}:videos_batch:{batch_idx:04d}",
            )
            counts["videos_batches"] += 1

            if videos_result.get("limit_reached"):
                limit_hit = True
                stop_reason = "credit_limit"
                break
            if should_abort(videos_result):
                stop_reason = "stop_action_on_videos"
                break

            responses = videos_result.get("responses", [])
            counts["videos_items_collected"] += count_items(responses)
            stats = sum_video_statistics(responses)
            totals["view_count_total"] += stats["view_count_total"]
            totals["like_count_total"] += stats["like_count_total"]
            totals["comment_count_total"] += stats["comment_count_total"]

    if stop_reason is None and selected_video_ids:
        for video_idx, video_id in enumerate(selected_video_ids, start=1):
            threads_result = client.fetch(
                "commentThreads",
                {
                    "part": "snippet,replies",
                    "videoId": video_id,
                    "maxResults": 100,
                },
                max_pages=args.comment_pages,
                credit_limit=args.credit_limit,
                origin=f"{args.channel_id}:video:{video_idx:04d}:{video_id}",
            )

            if threads_result.get("limit_reached"):
                limit_hit = True
                stop_reason = "credit_limit"
                break
            if should_abort(threads_result):
                stop_reason = "stop_action_on_comment_threads"
                break

            thread_responses = threads_result.get("responses", [])
            counts["comment_threads_pages_collected"] += len(thread_responses)
            counts["comment_threads_items_collected"] += count_items(thread_responses)

            top_level_comment_ids = extract_top_level_comment_ids(thread_responses)
            counts["top_level_comments_collected"] += len(top_level_comment_ids)

            for comment_id in top_level_comment_ids:
                replies_result = client.fetch(
                    "comments",
                    {
                        "part": "snippet",
                        "parentId": comment_id,
                        "maxResults": 100,
                    },
                    max_pages=args.reply_pages,
                    credit_limit=args.credit_limit,
                    origin=(
                        f"{args.channel_id}:video:{video_idx:04d}:{video_id}:"
                        f"parent:{comment_id}"
                    ),
                )
                if replies_result.get("limit_reached"):
                    limit_hit = True
                    stop_reason = "credit_limit"
                    break
                if should_abort(replies_result):
                    stop_reason = "stop_action_on_comments"
                    break

                reply_responses = replies_result.get("responses", [])
                counts["reply_pages_collected"] += len(reply_responses)
                counts["reply_items_collected"] += count_items(reply_responses)

            if stop_reason is not None:
                break

    finished_at = datetime.now(timezone.utc)
    run_root = Path(args.storage_root).expanduser().resolve() / "runs" / client.run_id
    run_root.mkdir(parents=True, exist_ok=True)
    summary_path = run_root / args.summary_name

    summary_payload: dict[str, Any] = {
        "run_id": client.run_id,
        "channel_id": args.channel_id,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 2),
        "limit_reached": limit_hit,
        "stop_reason": stop_reason,
        "uploads_playlist_id": uploads_playlist_id,
        "video_limit": args.max_videos,
        "credit_limit": args.credit_limit,
        "credit_state": client.credit_monitor.snapshot(),
        "counts": counts,
        "totals": totals,
    }
    summary_path.write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return summary_payload, summary_path


def main() -> None:
    """Run channel bundle collection."""
    args = parse_args()
    validate_args(args)

    config = YouTubeDataConfig(
        api_key=args.api_key,
        storage_root=args.storage_root,
        log_path=args.log_path,
        run_id=args.run_id,
        compress_responses=args.compress_responses,
    )
    client = YouTubeDataClient.from_config(config)
    register_script_endpoints(client)

    summary_payload, summary_path = collect_channel_bundle(client, args)

    print("Channel bundle collection finished")
    print(f"Run ID: {client.run_id}")
    print(f"Channel ID: {args.channel_id}")
    print(f"Limit reached: {summary_payload['limit_reached']}")
    print(f"Stop reason: {summary_payload['stop_reason']}")
    print(f"Videos unique: {summary_payload['counts']['playlist_unique_videos']}")
    print(f"Videos detailed: {summary_payload['counts']['videos_items_collected']}")
    print(f"Total views: {summary_payload['totals']['view_count_total']}")
    print(f"Total likes: {summary_payload['totals']['like_count_total']}")
    print(f"Total comments metric: {summary_payload['totals']['comment_count_total']}")
    print(f"Total spent: {summary_payload['credit_state']['total_spent']}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
