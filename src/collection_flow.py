from typing import Any, Dict, Iterable, List, Optional, Tuple

from .youtube_client import YouTubeClient
from .storage import SqliteStore
from .monitoring import log_info, log_request, log_error


DEFAULT_CHANNEL_PARTS = ("snippet", "statistics", "contentDetails")
DEFAULT_PLAYLISTITEM_PARTS = ("snippet", "contentDetails")
DEFAULT_VIDEO_PARTS = ("snippet", "statistics", "contentDetails")
DEFAULT_COMMENTTHREAD_PARTS = ("snippet", "replies")
DEFAULT_COMMENT_PARTS = ("snippet",)


class CollectionError(RuntimeError):
    pass


def _first_item(items: List[Dict[str, Any]], label: str) -> Dict[str, Any]:
    if not items:
        raise CollectionError(f"Nenhum item encontrado para {label}")
    return items[0]


def fetch_channel(
    client: YouTubeClient,
    channel_id: str,
    parts: Iterable[str] = DEFAULT_CHANNEL_PARTS,
    fields: Optional[str] = None,
) -> Dict[str, Any]:
    log_request("channels.list", params={"id": channel_id})
    response = client.list(
        "channels",
        part=parts,
        fields=fields,
        id=channel_id,
        maxResults=1,
    )
    item = _first_item(response.get("items", []), f"channel_id={channel_id}")
    channel_title = item.get("snippet", {}).get("title", "untitled")
    log_info(f"Fetched channel {channel_id}: '{channel_title}'")
    return item


def get_uploads_playlist_id(channel: Dict[str, Any]) -> str:
    content = channel.get("contentDetails", {})
    related = content.get("relatedPlaylists", {})
    uploads = related.get("uploads")
    if not uploads:
        raise CollectionError("Playlist de uploads nao encontrada no canal")
    return uploads


def fetch_playlist_items_page(
    client: YouTubeClient,
    playlist_id: str,
    parts: Iterable[str] = DEFAULT_PLAYLISTITEM_PARTS,
    fields: Optional[str] = None,
    page_token: Optional[str] = None,
    max_results: int = 50,
) -> Dict[str, Any]:
    response = client.list(
        "playlistItems",
        part=parts,
        fields=fields,
        playlistId=playlist_id,
        maxResults=max_results,
        pageToken=page_token,
    )
    return response


def iterate_playlist_items(
    client: YouTubeClient,
    playlist_id: str,
    parts: Iterable[str] = DEFAULT_PLAYLISTITEM_PARTS,
    fields: Optional[str] = None,
    max_results: int = 50,
    max_pages: Optional[int] = None,
) -> Iterable[Dict[str, Any]]:
    page_token: Optional[str] = None
    pages = 0
    total_items = 0
    log_info(f"Starting pagination of playlist {playlist_id} (max_results={max_results}, max_pages={max_pages})")
    while True:
        page = fetch_playlist_items_page(
            client,
            playlist_id,
            parts=parts,
            fields=fields,
            page_token=page_token,
            max_results=max_results,
        )
        page_items = page.get("items", [])
        page_count = len(page_items)
        total_items += page_count
        log_request("playlistItems.list", params={"playlistId": playlist_id, "pageToken": page_token}, quota_estimate=1)
        for item in page_items:
            yield item
        page_token = page.get("nextPageToken")
        pages += 1
        log_info(f"Playlist pagination: page {pages} returned {page_count} items (total so far: {total_items})")
        if not page_token:
            log_info(f"Playlist {playlist_id} pagination complete: {pages} pages, {total_items} total items")
            break
        if max_pages is not None and pages >= max_pages:
            log_info(f"Reached max_pages={max_pages} for playlist {playlist_id}")
            break


def extract_video_ids(playlist_items: List[Dict[str, Any]]) -> List[str]:
    ids: List[str] = []
    for item in playlist_items:
        content = item.get("contentDetails", {})
        video_id = content.get("videoId")
        if not video_id:
            snippet = item.get("snippet", {})
            resource = snippet.get("resourceId", {})
            video_id = resource.get("videoId")
        if video_id:
            ids.append(video_id)
    return ids


def fetch_videos(
    client: YouTubeClient,
    video_ids: List[str],
    parts: Iterable[str] = DEFAULT_VIDEO_PARTS,
    fields: Optional[str] = None,
) -> Dict[str, Any]:
    if not video_ids:
        return {"items": []}
    log_request("videos.list", params={"count": len(video_ids)}, quota_estimate=1)
    response = client.list(
        "videos",
        part=parts,
        fields=fields,
        id=",".join(video_ids),
        maxResults=min(50, len(video_ids)),
    )
    fetched_count = len(response.get("items", []))
    log_info(f"Fetched {fetched_count}/{len(video_ids)} videos")
    return response


def fetch_comment_threads_page(
    client: YouTubeClient,
    video_id: str,
    parts: Iterable[str] = DEFAULT_COMMENTTHREAD_PARTS,
    fields: Optional[str] = None,
    page_token: Optional[str] = None,
    max_results: int = 100,
    order: Optional[str] = None,
    text_format: str = "plainText",
) -> Dict[str, Any]:
    response = client.list(
        "commentThreads",
        part=parts,
        fields=fields,
        videoId=video_id,
        maxResults=max_results,
        pageToken=page_token,
        order=order,
        textFormat=text_format,
    )
    return response


def iterate_comment_threads(
    client: YouTubeClient,
    video_id: str,
    parts: Iterable[str] = DEFAULT_COMMENTTHREAD_PARTS,
    fields: Optional[str] = None,
    max_results: int = 100,
    max_pages: Optional[int] = None,
    order: Optional[str] = None,
    text_format: str = "plainText",
) -> Iterable[Dict[str, Any]]:
    page_token: Optional[str] = None
    pages = 0
    total_threads = 0
    log_info(f"Starting pagination of comment threads for video {video_id}")
    while True:
        page = fetch_comment_threads_page(
            client,
            video_id,
            parts=parts,
            fields=fields,
            page_token=page_token,
            max_results=max_results,
            order=order,
            text_format=text_format,
        )
        page_items = page.get("items", [])
        page_count = len(page_items)
        total_threads += page_count
        for item in page_items:
            yield item
        page_token = page.get("nextPageToken")
        pages += 1
        log_info(f"Comment threads pagination: page {pages} returned {page_count} threads (total: {total_threads})")
        if not page_token:
            break
        if max_pages is not None and pages >= max_pages:
            break


def fetch_comments_page(
    client: YouTubeClient,
    parent_id: str,
    parts: Iterable[str] = DEFAULT_COMMENT_PARTS,
    fields: Optional[str] = None,
    page_token: Optional[str] = None,
    max_results: int = 100,
    text_format: str = "plainText",
) -> Dict[str, Any]:
    response = client.list(
        "comments",
        part=parts,
        fields=fields,
        parentId=parent_id,
        maxResults=max_results,
        pageToken=page_token,
        textFormat=text_format,
    )
    return response


def iterate_comments(
    client: YouTubeClient,
    parent_id: str,
    parts: Iterable[str] = DEFAULT_COMMENT_PARTS,
    fields: Optional[str] = None,
    max_results: int = 100,
    max_pages: Optional[int] = None,
    text_format: str = "plainText",
) -> Iterable[Dict[str, Any]]:
    page_token: Optional[str] = None
    pages = 0
    total_comments = 0
    log_info(f"Starting pagination of comments for parent {parent_id}")
    while True:
        page = fetch_comments_page(
            client,
            parent_id,
            parts=parts,
            fields=fields,
            page_token=page_token,
            max_results=max_results,
            text_format=text_format,
        )
        page_items = page.get("items", [])
        page_count = len(page_items)
        total_comments += page_count
        for item in page_items:
            yield item
        page_token = page.get("nextPageToken")
        pages += 1
        log_info(f"Comments pagination: page {pages} returned {page_count} comments (total: {total_comments})")
        if not page_token:
            break
        if max_pages is not None and pages >= max_pages:
            break

def collect_video_categories_all(
        client: YouTubeClient,
        video_category_ids: list[str], 
        region_code: str | None = None,
        parts: Iterable[str] = ("snippet",),
):
    ...


def collect_video_comments_all(
    client: YouTubeClient,
    video_id: str,
    thread_parts: Iterable[str] = DEFAULT_COMMENTTHREAD_PARTS,
    comment_parts: Iterable[str] = DEFAULT_COMMENT_PARTS,
    thread_max_results: int = 100,
    thread_max_pages: Optional[int] = None,
    reply_max_results: int = 100,
    reply_max_pages: Optional[int] = None,
    order: Optional[str] = None,
    text_format: str = "plainText",
    include_replies: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    log_info(f"Starting collection of comments for video {video_id}")
    threads: List[Dict[str, Any]] = []
    replies: List[Dict[str, Any]] = []
    seen_reply_ids = set()

    for thread in iterate_comment_threads(
        client,
        video_id,
        parts=thread_parts,
        max_results=thread_max_results,
        max_pages=thread_max_pages,
        order=order,
        text_format=text_format,
    ):
        threads.append(thread)
        if not include_replies:
            continue
        snippet = thread.get("snippet", {})
        top_level = snippet.get("topLevelComment", {})
        parent_id = top_level.get("id")
        replies_in_thread = thread.get("replies", {}).get("comments", [])
        for reply in replies_in_thread:
            reply_id = reply.get("id")
            if reply_id and reply_id in seen_reply_ids:
                continue
            if reply_id:
                seen_reply_ids.add(reply_id)
            replies.append(reply)
        total_replies = snippet.get("totalReplyCount", 0)
        if not parent_id:
            continue
        if total_replies and total_replies <= len(replies_in_thread):
            continue
        for reply in iterate_comments(
            client,
            parent_id,
            parts=comment_parts,
            max_results=reply_max_results,
            max_pages=reply_max_pages,
            text_format=text_format,
        ):
            reply_id = reply.get("id")
            if reply_id and reply_id in seen_reply_ids:
                continue
            if reply_id:
                seen_reply_ids.add(reply_id)
            replies.append(reply)

    log_info(f"Comment collection complete for video {video_id}: {len(threads)} threads, {len(replies)} replies")
    return threads, replies


def persist_video_comments(
    store: SqliteStore,
    video_id: str,
    threads: List[Dict[str, Any]],
    replies: List[Dict[str, Any]],
    run_id: Optional[str] = None,
) -> Tuple[int, int]:
    """Persist collected comment threads and replies to the database.
    
    Returns a tuple of (threads_persisted, replies_persisted).
    """
    threads_persisted = 0
    replies_persisted = 0
    
    # Persist all comment threads for this video
    for thread in threads:
        try:
            store.upsert_comment_thread(thread, video_id=video_id, run_id=run_id)
            threads_persisted += 1
            
            # Persist any replies that belong to this thread
            thread_id = thread.get("id")
            matching_replies = [r for r in replies if r.get("snippet", {}).get("parentId") == thread_id]
            for reply in matching_replies:
                try:
                    store.upsert_comment(reply, thread_id=thread_id, run_id=run_id)
                    replies_persisted += 1
                except Exception as e:
                    log_error(f"Failed to persist comment {reply.get('id')}: {e}")
        except Exception as e:
            log_error(f"Failed to persist comment thread {thread.get('id')}: {e}")
    
    # Persist any remaining replies (orphans or edge cases)
    threads_ids = {t.get("id") for t in threads if t.get("id")}
    for reply in replies:
        thread_id = reply.get("snippet", {}).get("parentId")
        if thread_id and thread_id not in threads_ids:
            # This reply belongs to a thread not in our current batch
            # (can happen with reply pagination)
            try:
                store.upsert_comment(reply, thread_id=thread_id, run_id=run_id)
                replies_persisted += 1
            except Exception as e:
                log_error(f"Failed to persist orphan comment {reply.get('id')}: {e}")
    
    log_info(f"Persisted {threads_persisted} comment threads and {replies_persisted} replies for video {video_id}")
    return threads_persisted, replies_persisted


def collect_channel_uploads_page(
    client: YouTubeClient,
    channel_id: str,
    channel_parts: Iterable[str] = DEFAULT_CHANNEL_PARTS,
    playlist_parts: Iterable[str] = DEFAULT_PLAYLISTITEM_PARTS,
    video_parts: Iterable[str] = DEFAULT_VIDEO_PARTS,
    page_token: Optional[str] = None,
    max_results: int = 50,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    channel = fetch_channel(client, channel_id, parts=channel_parts)
    uploads_playlist_id = get_uploads_playlist_id(channel)

    playlist_page = fetch_playlist_items_page(
        client,
        uploads_playlist_id,
        parts=playlist_parts,
        page_token=page_token,
        max_results=max_results,
    )

    video_ids = extract_video_ids(playlist_page.get("items", []))
    videos = fetch_videos(client, video_ids, parts=video_parts)

    return channel, playlist_page, videos


def collect_channel_uploads_all(
    client: YouTubeClient,
    channel_id: str,
    channel_parts: Iterable[str] = DEFAULT_CHANNEL_PARTS,
    playlist_parts: Iterable[str] = DEFAULT_PLAYLISTITEM_PARTS,
    video_parts: Iterable[str] = DEFAULT_VIDEO_PARTS,
    max_results: int = 50,
    max_pages: Optional[int] = None,
    batch_size: int = 50,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    log_info(f"Starting full collection for channel {channel_id}")
    channel = fetch_channel(client, channel_id, parts=channel_parts)
    uploads_playlist_id = get_uploads_playlist_id(channel)
    log_info(f"Uploads playlist ID: {uploads_playlist_id}")

    items: List[Dict[str, Any]] = []
    videos: List[Dict[str, Any]] = []
    batch: List[str] = []

    for item in iterate_playlist_items(
        client,
        uploads_playlist_id,
        parts=playlist_parts,
        max_results=max_results,
        max_pages=max_pages,
    ):
        items.append(item)
        for video_id in extract_video_ids([item]):
            batch.append(video_id)
        if len(batch) >= batch_size:
            videos_response = fetch_videos(client, batch, parts=video_parts)
            videos.extend(videos_response.get("items", []))
            log_info(f"Batch collected: {len(batch)} videos, total videos so far: {len(videos)}")
            batch = []

    if batch:
        videos_response = fetch_videos(client, batch, parts=video_parts)
        videos.extend(videos_response.get("items", []))
        log_info(f"Final batch collected: {len(batch)} videos")

    log_info(f"Full collection complete for {channel_id}: {len(items)} playlist items, {len(videos)} unique videos")
    return channel, items, videos


# incremental variant uses storage information to avoid refetching existing videos

def collect_channel_uploads_incremental(
    client: YouTubeClient,
    store: SqliteStore,
    channel_id: str,
    channel_parts: Iterable[str] = DEFAULT_CHANNEL_PARTS,
    playlist_parts: Iterable[str] = DEFAULT_PLAYLISTITEM_PARTS,
    video_parts: Iterable[str] = DEFAULT_VIDEO_PARTS,
    max_results: int = 50,
    max_pages: Optional[int] = None,
    batch_size: int = 50,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Collect channel data while skipping videos already present in the store.

    The returned ``items`` list is filtered to only those playlist entries whose
    ``videoId`` correspond to the videos returned in ``videos``; the latter
    contains only new videos fetched during this run. ``store`` is queried to
    determine which video IDs already exist and avoid additional ``videos.list``
    requests for them.
    """
    log_info(f"Starting incremental collection for channel {channel_id}")
    channel = fetch_channel(client, channel_id, parts=channel_parts)
    uploads_playlist_id = get_uploads_playlist_id(channel)
    log_info(f"Uploads playlist ID: {uploads_playlist_id}")

    items: List[Dict[str, Any]] = []
    videos: List[Dict[str, Any]] = []
    batch: List[str] = []
    skipped_count = 0

    for item in iterate_playlist_items(
        client,
        uploads_playlist_id,
        parts=playlist_parts,
        max_results=max_results,
        max_pages=max_pages,
    ):
        items.append(item)
        for video_id in extract_video_ids([item]):
            if not store.video_exists(video_id):
                batch.append(video_id)
            else:
                skipped_count += 1
        if len(batch) >= batch_size:
            videos_response = fetch_videos(client, batch, parts=video_parts)
            videos.extend(videos_response.get("items", []))
            log_info(f"Batch collected: {len(batch)} new videos, {skipped_count} duplicates, total new: {len(videos)}")
            batch = []

    if batch:
        videos_response = fetch_videos(client, batch, parts=video_parts)
        videos.extend(videos_response.get("items", []))
        log_info(f"Final batch collected: {len(batch)} new videos, total new: {len(videos)}")

    # remove playlist items that don't correspond to any newly fetched videos
    new_ids = {v.get("id") for v in videos if v.get("id")}
    filtered_items: List[Dict[str, Any]] = []
    for item in items:
        vids = extract_video_ids([item])
        if any(v in new_ids for v in vids):
            filtered_items.append(item)

    log_info(
        f"Incremental collection complete for {channel_id}: {len(items)} playlist items found, "
        f"{len(videos)} new videos collected, {skipped_count} duplicates skipped"
    )
    return channel, filtered_items, videos

# ============================================================================
# Search-based pipelines: for discovering channels and videos by search query
# ============================================================================


def fetch_search_page(
    client: YouTubeClient,
    search_query: str,
    search_type: str = "channel",
    parts: Iterable[str] = ("snippet",),
    fields: Optional[str] = None,
    page_token: Optional[str] = None,
    max_results: int = 50,
    order: Optional[str] = None,
    published_after: Optional[str] = None,
    published_before: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch a single page of search results.
    
    Args:
        search_query: The search term.
        search_type: Type of resource to search for (channel, video).
        parts: Parts to retrieve (defaults to snippet for efficiency).
        fields: Additional fields filter.
        page_token: Token for pagination.
        max_results: Number of results per page (1-50).
        order: Sort order (relevance, uploadDate, viewCount).
        published_after: Filter by publication date (RFC 3339 format).
        published_before: Filter by publication date (RFC 3339 format).
    
    Returns:
        API response dictionary.
    """
    log_request(
        "search.list",
        params={"q": search_query, "type": search_type, "pageToken": page_token},
        quota_estimate=100,  # search is expensive
    )
    response = client.list(
        "search",
        part=parts,
        fields=fields,
        q=search_query,
        type=search_type,
        maxResults=max_results,
        pageToken=page_token,
        order=order,
        publishedAfter=published_after,
        publishedBefore=published_before,
    )
    return response


def iterate_search_results(
    client: YouTubeClient,
    search_query: str,
    search_type: str = "channel",
    parts: Iterable[str] = ("snippet",),
    fields: Optional[str] = None,
    max_results: int = 50,
    max_pages: Optional[int] = None,
    order: Optional[str] = None,
    published_after: Optional[str] = None,
    published_before: Optional[str] = None,
) -> Iterable[Dict[str, Any]]:
    """Iterate over all pages of search results.
    
    Args:
        search_query: The search term.
        search_type: Type of resource (channel, video).
        parts: Parts to retrieve.
        fields: Fields filter.
        max_results: Results per page.
        max_pages: Optional limit on total pages to fetch.
        order: Sort order.
        published_after: Publication date filter (after).
        published_before: Publication date filter (before).
    
    Yields:
        Individual search result items.
    """
    page_token: Optional[str] = None
    pages = 0
    total_results = 0
    log_info(
        f"Starting search for '{search_query}' (type={search_type}, "
        f"max_results={max_results}, max_pages={max_pages})"
    )
    while True:
        page = fetch_search_page(
            client,
            search_query,
            search_type=search_type,
            parts=parts,
            fields=fields,
            page_token=page_token,
            max_results=max_results,
            order=order,
            published_after=published_after,
            published_before=published_before,
        )
        page_items = page.get("items", [])
        page_count = len(page_items)
        total_results += page_count
        for item in page_items:
            yield item
        page_token = page.get("nextPageToken")
        pages += 1
        log_info(
            f"Search pagination: page {pages} returned {page_count} results "
            f"(total so far: {total_results})"
        )
        if not page_token:
            log_info(
                f"Search for '{search_query}' complete: {pages} pages, "
                f"{total_results} total results"
            )
            break
        if max_pages is not None and pages >= max_pages:
            log_info(f"Reached max_pages={max_pages} for search '{search_query}'")
            break


def pipeline_search_channels(
    client: YouTubeClient,
    store: SqliteStore,
    search_query: str,
    channel_parts: Iterable[str] = DEFAULT_CHANNEL_PARTS,
    max_results: int = 50,
    max_pages: Optional[int] = None,
    order: Optional[str] = None,
    published_after: Optional[str] = None,
    published_before: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """Pipeline to search for YouTube channels by query and fetch their metadata.
    
    Uses search.list (expensive, 100 quota per page) followed by channels.list
    to enrich results with full channel data.
    
    Args:
        client: YouTube API client.
        store: Database store for persistence.
        search_query: Search term for channels.
        channel_parts: Parts to fetch for each channel.
        max_results: Results per page for search.
        max_pages: Max pages to fetch from search.
        order: Sort order for search results.
        published_after: Filter channels by creation date (after).
        published_before: Filter channels by creation date (before).
        run_id: ID for tracking this collection run.
    
    Returns:
        Tuple of (channels list, count persisted).
    """
    log_info(f"Starting search-channels pipeline for query: '{search_query}'")
    channels: List[Dict[str, Any]] = []
    channel_ids: List[str] = []
    
    # Fetch search results to discover channel IDs
    for result in iterate_search_results(
        client,
        search_query,
        search_type="channel",
        parts=("snippet",),
        max_results=max_results,
        max_pages=max_pages,
        order=order,
        published_after=published_after,
        published_before=published_before,
    ):
        channel_id = result.get("id", {}).get("channelId")
        if channel_id:
            channel_ids.append(channel_id)
    
    log_info(f"Found {len(channel_ids)} channel IDs from search results")
    
    # Fetch full channel metadata for each discovered channel
    batch_size = 50
    for i in range(0, len(channel_ids), batch_size):
        batch = channel_ids[i : i + batch_size]
        try:
            response = client.list(
                "channels",
                part=channel_parts,
                id=",".join(batch),
                maxResults=len(batch),
            )
            batch_channels = response.get("items", [])
            for channel in batch_channels:
                channels.append(channel)
                if store:
                    store.upsert_channel(channel, run_id=run_id)
            log_info(
                f"Fetched metadata for {len(batch_channels)} channels "
                f"(batch {i // batch_size + 1})"
            )
        except Exception as e:
            log_error(f"Error fetching channel batch {i // batch_size + 1}: {e}")
    
    log_info(f"Search-channels pipeline complete: {len(channels)} channels fetched and persisted")
    return channels, len(channels)


def pipeline_search_videos(
    client: YouTubeClient,
    store: SqliteStore,
    search_query: str,
    video_parts: Iterable[str] = DEFAULT_VIDEO_PARTS,
    max_results: int = 50,
    max_pages: Optional[int] = None,
    order: Optional[str] = None,
    published_after: Optional[str] = None,
    published_before: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """Pipeline to search for YouTube videos by query and fetch their metadata.
    
    Uses search.list (expensive, 100 quota per page) followed by videos.list
    to enrich results with full video statistics.
    
    Args:
        client: YouTube API client.
        store: Database store for persistence.
        search_query: Search term for videos.
        video_parts: Parts to fetch for each video.
        max_results: Results per page for search.
        max_pages: Max pages to fetch from search.
        order: Sort order (relevant, uploadDate, viewCount).
        published_after: Filter videos by upload date (after).
        published_before: Filter videos by upload date (before).
        run_id: ID for tracking this collection run.
    
    Returns:
        Tuple of (videos list, count persisted).
    """
    log_info(f"Starting search-videos pipeline for query: '{search_query}'")
    videos: List[Dict[str, Any]] = []
    video_ids: List[str] = []
    
    # Fetch search results to discover video IDs
    for result in iterate_search_results(
        client,
        search_query,
        search_type="video",
        parts=("snippet",),
        max_results=max_results,
        max_pages=max_pages,
        order=order,
        published_after=published_after,
        published_before=published_before,
    ):
        video_id = result.get("id", {}).get("videoId")
        if video_id:
            video_ids.append(video_id)
    
    log_info(f"Found {len(video_ids)} video IDs from search results")
    
    # Fetch full video metadata for each discovered video
    batch_size = 50
    for i in range(0, len(video_ids), batch_size):
        batch = video_ids[i : i + batch_size]
        try:
            response = client.list(
                "videos",
                part=video_parts,
                id=",".join(batch),
                maxResults=len(batch),
            )
            batch_videos = response.get("items", [])
            for video in batch_videos:
                videos.append(video)
                if store:
                    store.upsert_video(video, run_id=run_id)
            log_info(
                f"Fetched metadata for {len(batch_videos)} videos "
                f"(batch {i // batch_size + 1})"
            )
        except Exception as e:
            log_error(f"Error fetching video batch {i // batch_size + 1}: {e}")
    
    log_info(f"Search-videos pipeline complete: {len(videos)} videos fetched and persisted")
    return videos, len(videos)


def pipeline_channel_videos(
    client: YouTubeClient,
    store: SqliteStore,
    channel_ids: List[str],
    channel_parts: Iterable[str] = DEFAULT_CHANNEL_PARTS,
    playlist_parts: Iterable[str] = DEFAULT_PLAYLISTITEM_PARTS,
    video_parts: Iterable[str] = DEFAULT_VIDEO_PARTS,
    max_results: int = 50,
    max_pages: Optional[int] = None,
    batch_size: int = 50,
    incremental: bool = False,
    run_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Pipeline to collect all videos from one or more channels.
    
    Uses the standard flow: channels.list -> uploads playlist -> playlistItems.list
    -> videos.list. Supports both full and incremental collection.
    
    Args:
        client: YouTube API client.
        store: Database store for persistence.
        channel_ids: List of channel IDs to collect from.
        channel_parts: Parts for channels.list.
        playlist_parts: Parts for playlistItems.list.
        video_parts: Parts for videos.list.
        max_results: Results per page for playlistItems.
        max_pages: Max pages per channel.
        batch_size: Batch size for videos.list calls.
        incremental: If True, skip videos already in store.
        run_id: ID for tracking this collection run.
    
    Returns:
        Tuple of (channels list, videos list).
    """
    log_info(f"Starting channel-videos pipeline for {len(channel_ids)} channel(s)")
    channels: List[Dict[str, Any]] = []
    videos: List[Dict[str, Any]] = []
    
    for channel_id in channel_ids:
        try:
            log_info(f"Processing channel: {channel_id}")
            if incremental:
                channel, _, batch_videos = collect_channel_uploads_incremental(
                    client,
                    store,
                    channel_id,
                    channel_parts=channel_parts,
                    playlist_parts=playlist_parts,
                    video_parts=video_parts,
                    max_results=max_results,
                    max_pages=max_pages,
                    batch_size=batch_size,
                )
            else:
                channel, _, batch_videos = collect_channel_uploads_all(
                    client,
                    channel_id,
                    channel_parts=channel_parts,
                    playlist_parts=playlist_parts,
                    video_parts=video_parts,
                    max_results=max_results,
                    max_pages=max_pages,
                    batch_size=batch_size,
                )
            
            channels.append(channel)
            videos.extend(batch_videos)
            
            if store:
                store.upsert_channel(channel, run_id=run_id)
                store.upsert_many_videos(batch_videos, run_id=run_id)
            
            channel_name = channel.get("snippet", {}).get("title", "unknown")
            log_info(f"Channel '{channel_name}': {len(batch_videos)} videos collected")
        except Exception as e:
            log_error(f"Error processing channel {channel_id}: {e}")
    
    log_info(
        f"Channel-videos pipeline complete: {len(channels)} channels, "
        f"{len(videos)} total videos (unique IDs may be lower due to potential duplicates)"
    )
    return channels, videos


def pipeline_video_comments(
    client: YouTubeClient,
    store: SqliteStore,
    video_ids: List[str],
    thread_parts: Iterable[str] = DEFAULT_COMMENTTHREAD_PARTS,
    comment_parts: Iterable[str] = DEFAULT_COMMENT_PARTS,
    thread_max_results: int = 100,
    thread_max_pages: Optional[int] = None,
    reply_max_results: int = 100,
    reply_max_pages: Optional[int] = None,
    order: Optional[str] = None,
    text_format: str = "plainText",
    include_replies: bool = True,
    run_id: Optional[str] = None,
) -> Tuple[int, int]:
    """Pipeline to collect comments and replies from one or more videos.
    
    Args:
        client: YouTube API client.
        store: Database store for persistence.
        video_ids: List of video IDs to collect comments from.
        thread_parts: Parts for commentThreads.list.
        comment_parts: Parts for comments.list.
        thread_max_results: Results per page for threads.
        thread_max_pages: Max pages for threads per video.
        reply_max_results: Results per page for replies.
        reply_max_pages: Max pages for replies per thread.
        order: Sort order for comment threads.
        text_format: Format for comment text (plainText, html).
        include_replies: Whether to fetch full replies for threads.
        run_id: ID for tracking this collection run.
    
    Returns:
        Tuple of (total threads persisted, total comments persisted).
    """
    log_info(f"Starting video-comments pipeline for {len(video_ids)} video(s)")
    total_threads = 0
    total_comments = 0
    
    for video_id in video_ids:
        try:
            log_info(f"Processing comments for video: {video_id}")
            threads, replies = collect_video_comments_all(
                client,
                video_id,
                thread_parts=thread_parts,
                comment_parts=comment_parts,
                thread_max_results=thread_max_results,
                thread_max_pages=thread_max_pages,
                reply_max_results=reply_max_results,
                reply_max_pages=reply_max_pages,
                order=order,
                text_format=text_format,
                include_replies=include_replies,
            )
            
            if store:
                threads_persisted, comments_persisted = persist_video_comments(
                    store,
                    video_id,
                    threads,
                    replies,
                    run_id=run_id,
                )
                total_threads += threads_persisted
                total_comments += comments_persisted
            else:
                total_threads += len(threads)
                total_comments += len(replies)
            
            log_info(
                f"Video {video_id}: {len(threads)} threads, "
                f"{len(replies)} comments collected"
            )
        except Exception as e:
            log_error(f"Error processing comments for video {video_id}: {e}")
    
    log_info(
        f"Video-comments pipeline complete: "
        f"{total_threads} threads, {total_comments} comments persisted"
    )
    return total_threads, total_comments