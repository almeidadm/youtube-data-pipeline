#!/usr/bin/env python3
"""Simple command-line tools for running collections and exporting data.

Commands
--------
collect
    Execute a full or incremental collection run based on seeds in a JSON
    plan file. Results are stored in the SQLite database defined by
    ``YOUTUBE_DB_PATH``.
export
    Dump table contents from the database to JSON lines on stdout or a file.

The script is intentionally lightweight to keep the MVP focused on web
sampling; for more complex orchestration a separate workflow manager could be
used later.
"""
import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

# Handle direct execution: add parent directory to path so 'src' module is importable
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    # Try relative imports (when imported as module)
    from .youtube_client import YouTubeClient
    from .storage import SqliteStore
    from .tracking import RequestTracker
    from .monitoring import setup_monitoring, log_collection_start, log_collection_end, log_channel_processing, log_channel_summary, log_info, get_log_files_info
    from .collection_flow import collection_flow
except ImportError:
    # Fall back to absolute imports (when executed directly)
    from src.youtube_client import YouTubeClient
    from src.storage import SqliteStore
    from src.tracking import RequestTracker
    from src.monitoring import setup_monitoring, log_collection_start, log_collection_end, log_channel_processing, log_channel_summary, log_info, get_log_files_info
    import src.collection_flow as collection_flow

from dotenv import load_dotenv

load_dotenv()


def _load_plan(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def do_collect(
    seeds_path: str,
    pipeline_type: Optional[str],
    incremental: bool,
    max_pages: Optional[int],
    batch_size: int,
) -> None:
    """Execute collection based on plan and pipeline type.
    
    Supports multiple pipeline types:
    - channel_videos: Collect videos from specified channels
    - search_channels: Search for channels by query
    - search_videos: Search for videos by query
    - video_comments: Collect comments from specified videos
    """
    plan = _load_plan(seeds_path)
    store = SqliteStore()
    run_id = str(uuid.uuid4())
    
    # Initialize monitoring with run ID
    setup_monitoring(run_id=run_id)
    tracker = RequestTracker(store, run_id)
    client = YouTubeClient(tracker=tracker)

    # Determine pipeline type from argument or config
    ptype = pipeline_type or plan.get("pipeline_type", "channel_videos")
    log_info(f"Using pipeline type: {ptype}")
    
    store.start_run(run_id, params={
        "pipeline_type": ptype,
        "incremental": incremental,
    })
    log_collection_start(run_id, incremental)
    
    try:
        if ptype == "channel_videos":
            _execute_channel_videos_pipeline(
                client, store, plan, run_id, incremental, max_pages, batch_size
            )
        elif ptype == "search_channels":
            _execute_search_channels_pipeline(client, store, plan, run_id, max_pages)
        elif ptype == "search_videos":
            _execute_search_videos_pipeline(client, store, plan, run_id, max_pages)
        elif ptype == "video_comments":
            _execute_video_comments_pipeline(client, store, plan, run_id, max_pages)
        else:
            raise ValueError(f"Unknown pipeline type: {ptype}")
        
        store.finish_run(run_id, "success")
        log_collection_end(run_id, "success")
    except Exception as exc:  # noqa: BLE001
        store.finish_run(run_id, "error", notes=str(exc))
        log_collection_end(run_id, "error", error=str(exc))
        raise


def _execute_channel_videos_pipeline(
    client: "YouTubeClient",
    store: SqliteStore,
    plan: Dict[str, Any],
    run_id: str,
    incremental: bool,
    max_pages: Optional[int],
    batch_size: int,
) -> None:
    """Execute the channel_videos pipeline."""
    channel_ids = plan.get("seeds", {}).get("channel_ids", [])
    log_info(f"Processing {len(channel_ids)} channel(s)")
    
    if not channel_ids:
        log_info("No channel_ids provided in seeds")
        return
    
    channels, videos = collection_flow.pipeline_channel_videos(
        client,
        store,
        channel_ids,
        max_results=50,
        max_pages=max_pages,
        batch_size=batch_size,
        incremental=incremental,
        run_id=run_id,
    )
    
    for channel in channels:
        channel_id = channel.get("id")
        channel_name = channel.get("snippet", {}).get("title", "unknown")
        quota_used = store.quota_summary(run_id=run_id).get("total", 0)
        log_channel_summary(channel_id, 0, quota_used)
    
    log_info(
        f"Channel videos pipeline complete: "
        f"{len(channels)} channels, {len(videos)} total videos"
    )


def _execute_search_channels_pipeline(
    client: "YouTubeClient",
    store: SqliteStore,
    plan: Dict[str, Any],
    run_id: str,
    max_pages: Optional[int],
) -> None:
    """Execute the search_channels pipeline."""
    search_queries = plan.get("seeds", {}).get("search_channels_queries", [])
    log_info(f"Processing {len(search_queries)} search query(ies) for channels")
    
    if not search_queries:
        log_info("No search_channels_queries provided in seeds")
        return
    
    total_channels = 0
    for query in search_queries:
        channels, count = collection_flow.pipeline_search_channels(
            client,
            store,
            query,
            max_pages=max_pages,
            order="relevance",
            run_id=run_id,
        )
        total_channels += count
        log_info(f"Query '{query}': {count} channels discovered and persisted")
    
    log_info(f"Search channels pipeline complete: {total_channels} channels total")


def _execute_search_videos_pipeline(
    client: "YouTubeClient",
    store: SqliteStore,
    plan: Dict[str, Any],
    run_id: str,
    max_pages: Optional[int],
) -> None:
    """Execute the search_videos pipeline."""
    search_queries = plan.get("seeds", {}).get("search_videos_queries", [])
    log_info(f"Processing {len(search_queries)} search query(ies) for videos")
    
    if not search_queries:
        log_info("No search_videos_queries provided in seeds")
        return
    
    total_videos = 0
    for query in search_queries:
        videos, count = collection_flow.pipeline_search_videos(
            client,
            store,
            query,
            max_pages=max_pages,
            order="relevance",
            run_id=run_id,
        )
        total_videos += count
        log_info(f"Query '{query}': {count} videos discovered and persisted")
    
    log_info(f"Search videos pipeline complete: {total_videos} videos total")


def _execute_video_comments_pipeline(
    client: "YouTubeClient",
    store: SqliteStore,
    plan: Dict[str, Any],
    run_id: str,
    max_pages: Optional[int],
) -> None:
    """Execute the video_comments pipeline."""
    video_ids = plan.get("seeds", {}).get("video_ids", [])
    log_info(f"Processing {len(video_ids)} video(s) for comments")
    
    if not video_ids:
        log_info("No video_ids provided in seeds")
        return
    
    threads, comments = collection_flow.pipeline_video_comments(
        client,
        store,
        video_ids,
        thread_max_pages=max_pages,
        reply_max_pages=max_pages,
        order="relevance",
        include_replies=True,
        run_id=run_id,
    )
    
    log_info(
        f"Video comments pipeline complete: "
        f"{threads} threads and {comments} comments persisted"
    )


def do_export(
    tables: List[str],
    output: Optional[str],
) -> None:
    store = SqliteStore()
    out_file = open(output, "w", encoding="utf-8") if output else sys.stdout

    if "all" in tables:
        tables = ["channels", "videos", "comment_threads", "comments", "requests", "runs"]

    for table in tables:
        cur = store.conn.execute(f"SELECT * FROM {table}")
        cols = [d[0] for d in cur.description]
        for row in cur:
            record = dict(zip(cols, row))
            out_file.write(json.dumps(record, ensure_ascii=False) + "\n")

    if output:
        out_file.close()


def do_quota(run_id: Optional[str] = None) -> None:
    store = SqliteStore()
    summary = store.quota_summary(run_id=run_id)
    total = sum(summary.values())
    print("Quota usage summary")
    if run_id:
        print(f"(filtered to run_id={run_id})")
    for resource, amount in summary.items():
        print(f"  {resource}: {amount}")
    print(f"Total: {total}")


def do_logs() -> None:
    """Display information about monitoring logs."""
    info = get_log_files_info()
    if not info["exists"]:
        print(f"No log directory yet. Will be created at: {info['log_dir']}")
        return
    
    print(f"Log directory: {info['log_dir']}")
    if not info["files"]:
        print("No log files found.")
        return
    
    print(f"\nFound {len(info['files'])} log file(s):")
    for f in info["files"]:
        size_kb = f["size_bytes"] / 1024
        print(f"  - {f['name']} ({size_kb:.1f} KB, modified: {f['modified']})")


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube data collection utility")
    sub = parser.add_subparsers(dest="command")

    cparser = sub.add_parser("collect", help="run collection flow")
    cparser.add_argument(
        "--seeds",
        default="config/collection_plan.json",
        help="path to JSON file with seeds",
    )
    cparser.add_argument(
        "--pipeline-type",
        choices=["channel_videos", "search_channels", "search_videos", "video_comments"],
        help="override the pipeline type from config",
    )
    cparser.add_argument(
        "--incremental",
        action="store_true",
        help="skip videos that already exist in the database (channel_videos only)",
    )
    cparser.add_argument(
        "--max-pages",
        type=int,
        help="optional limit on pages fetched per playlist/search",
    )
    cparser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="number of video ids to batch in videos.list calls (channel_videos only)",
    )

    eparser = sub.add_parser("export", help="dump database tables")
    eparser.add_argument(
        "--tables",
        nargs="+",
        choices=["channels", "videos", "comment_threads", "comments", "requests", "runs", "all"],
        default=["all"],
        help="which tables to export",
    )
    eparser.add_argument(
        "--output",
        help="file path to write output (defaults to stdout)",
    )

    qparser = sub.add_parser("quota", help="show quota usage summary")
    qparser.add_argument(
        "--run-id",
        help="optional run_id to filter the summary",
    )

    sub.add_parser("logs", help="show information about monitoring logs")

    args = parser.parse_args()
    if args.command == "collect":
        do_collect(
            seeds_path=args.seeds,
            pipeline_type=args.pipeline_type,
            incremental=args.incremental,
            max_pages=args.max_pages,
            batch_size=args.batch_size,
        )
    elif args.command == "export":
        do_export(tables=args.tables, output=args.output)
    elif args.command == "quota":
        do_quota(run_id=args.run_id)
    elif args.command == "logs":
        do_logs()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
