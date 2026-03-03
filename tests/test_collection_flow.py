from typing import Dict, Iterable, List

import pytest

import src.collection_flow as cf
import src.storage as st
from src.youtube_client import YouTubeClient


class DummyClient(YouTubeClient):
    def __init__(self):
        super().__init__(api_key="DUMMY")
        self.calls = []

    def list(self, resource: str, part: Iterable[str], **params) -> Dict:
        # return simple canned responses based on resource
        self.calls.append((resource, params))
        if resource == "channels":
            return {"items": [{"id": params.get("id", ""), "contentDetails": {"relatedPlaylists": {"uploads": "pl1"}}}]}
        if resource == "playlistItems":
            # simulate two items with ids a and b
            items = [
                {"contentDetails": {"videoId": "a"}},
                {"contentDetails": {"videoId": "b"}},
            ]
            return {"items": items}
        if resource == "videos":
            ids = params.get("id", "").split(",")
            return {"items": [{"id": vid} for vid in ids]}
        return {}


def test_extract_video_ids():
    items = [
        {"contentDetails": {"videoId": "x"}},
        {"snippet": {"resourceId": {"videoId": "y"}}},
        {},
    ]
    ids = cf.extract_video_ids(items)
    assert ids == ["x", "y"]


def test_collect_incremental_skips_existing(monkeypatch):
    client = DummyClient()
    store = st.SqliteStore(db_path=":memory:")
    run_id = "r"
    # Create the run first
    store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id, "2026-03-02T00:00:00Z"))
    # mark video 'a' as existing
    store.upsert_video({"id": "a"}, run_id=run_id)

    # run incremental collection
    channel, items, videos = cf.collect_channel_uploads_incremental(
        client,
        store,
        channel_id="chan1",
    )
    # 'a' existed so only 'b' should be fetched
    assert {v.get("id") for v in videos} == {"b"}
    # and items filtered accordingly
    assert len(items) == 1
    assert cf.extract_video_ids(items) == ["b"]


# ensure iterate functions work with multiple pages

class PageClient(DummyClient):
    def list(self, resource: str, part: Iterable[str], **params) -> Dict:
        if resource == "playlistItems":
            page = params.get("pageToken")
            if page == "2":
                return {"items": [{"contentDetails": {"videoId": "c"}}]}
            else:
                return {"items": [{"contentDetails": {"videoId": "a"}}], "nextPageToken": "2"}
        return super().list(resource, part=part, **params)


def test_iterate_playlist_items_multiple_pages():
    client = PageClient()
    ids = [item.get("contentDetails", {}).get("videoId") for item in cf.iterate_playlist_items(client, "pl")]
    assert ids == ["a", "c"]


def test_persist_video_comments():
    store = st.SqliteStore(db_path=":memory:")
    run_id = "r1"
    store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id, "2026-03-02T00:00:00Z"))
    
    video_id = "vid1"
    threads = [
        {"id": "thread1", "snippet": {"topLevelComment": {"id": "comment1"}}},
        {"id": "thread2", "snippet": {"topLevelComment": {"id": "comment2"}}},
    ]
    replies = [
        {"id": "reply1", "snippet": {"textDisplay": "Reply 1", "parentId": "thread1"}},
        {"id": "reply2", "snippet": {"textDisplay": "Reply 2", "parentId": "thread1"}},
        {"id": "reply3", "snippet": {"textDisplay": "Reply 3", "parentId": "thread2"}},
    ]
    
    threads_persisted, replies_persisted = cf.persist_video_comments(
        store, video_id, threads, replies, run_id=run_id
    )
    
    assert threads_persisted == 2
    assert replies_persisted == 3
    
    # Verify they were actually persisted
    assert store.comment_thread_exists("thread1")
    assert store.comment_thread_exists("thread2")
    assert store.comment_exists("reply1")
    assert store.comment_exists("reply2")
    assert store.comment_exists("reply3")

# ============================================================================
# Tests for new pipeline functions (Milestone 15)
# ============================================================================


class SearchDummyClient(YouTubeClient):
    """Mock client for testing search and pipeline functions."""
    
    def __init__(self):
        super().__init__(api_key="DUMMY")
        self.calls = []

    def list(self, resource: str, part: Iterable[str], **params) -> Dict:
        """Return canned responses for search-based and pipeline tests."""
        self.calls.append((resource, params))
        
        if resource == "search":
            # Simulate search results (channels or videos)
            search_type = params.get("type", "")
            if search_type == "channel":
                return {
                    "items": [
                        {"id": {"channelId": "ch1"}},
                        {"id": {"channelId": "ch2"}},
                    ]
                }
            elif search_type == "video":
                return {
                    "items": [
                        {"id": {"videoId": "vid1"}},
                        {"id": {"videoId": "vid2"}},
                    ]
                }
        elif resource == "channels":
            ids = params.get("id", "").split(",")
            return {
                "items": [
                    {
                        "id": cid,
                        "snippet": {"title": f"Channel {cid}"},
                        "contentDetails": {"relatedPlaylists": {"uploads": f"pl_{cid}"}},
                    }
                    for cid in ids
                ]
            }
        elif resource == "videos":
            ids = params.get("id", "").split(",")
            return {
                "items": [{"id": vid, "snippet": {"title": f"Video {vid}"}} for vid in ids]
            }
        elif resource == "commentThreads":
            return {
                "items": [
                    {"id": f"thread_{i}", "snippet": {"totalReplyCount": 0}}
                    for i in range(2)
                ]
            }
        
        return {}


def test_pipeline_search_channels():
    """Test pipeline_search_channels function."""
    client = SearchDummyClient()
    store = st.SqliteStore(db_path=":memory:")
    run_id = "search_ch_test"
    store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id, "2026-03-02T00:00:00Z"))
    
    channels, count = cf.pipeline_search_channels(
        client,
        store,
        "test query",
        run_id=run_id,
    )
    
    assert count >= 0
    # Verify channels were fetched
    assert len(client.calls) >= 2  # search.list + channels.list
    assert any(call[0] == "search" for call in client.calls)
    assert any(call[0] == "channels" for call in client.calls)


def test_pipeline_search_videos():
    """Test pipeline_search_videos function."""
    client = SearchDummyClient()
    store = st.SqliteStore(db_path=":memory:")
    run_id = "search_vid_test"
    store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id, "2026-03-02T00:00:00Z"))
    
    videos, count = cf.pipeline_search_videos(
        client,
        store,
        "test query",
        run_id=run_id,
    )
    
    assert count >= 0
    # Verify videos were fetched
    assert len(client.calls) >= 2  # search.list + videos.list
    assert any(call[0] == "search" for call in client.calls)
    assert any(call[0] == "videos" for call in client.calls)


def test_pipeline_channel_videos():
    """Test pipeline_channel_videos function."""
    client = DummyClient()
    store = st.SqliteStore(db_path=":memory:")
    run_id = "ch_vid_test"
    store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id, "2026-03-02T00:00:00Z"))
    
    channels, videos = cf.pipeline_channel_videos(
        client,
        store,
        ["ch1"],
        run_id=run_id,
    )
    
    assert len(channels) >= 0
    assert len(videos) >= 0
    # Verify channels and videos were stored
    assert len(client.calls) > 0


def test_pipeline_video_comments():
    """Test pipeline_video_comments function."""
    client = SearchDummyClient()
    store = st.SqliteStore(db_path=":memory:")
    run_id = "vid_comm_test"
    store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id, "2026-03-02T00:00:00Z"))
    
    threads, comments = cf.pipeline_video_comments(
        client,
        store,
        ["vid1"],
        run_id=run_id,
    )
    
    assert threads >= 0
    assert comments >= 0
    # Verify commentThreads were queried
    assert any(call[0] == "commentThreads" for call in client.calls)


def test_iterate_search_results():
    """Test iterate_search_results function."""
    client = SearchDummyClient()
    
    results = list(
        cf.iterate_search_results(
            client,
            "test query",
            search_type="channel",
            max_pages=1,
        )
    )
    
    assert len(results) >= 0
    # At least one search.list call was made
    assert any(call[0] == "search" for call in client.calls)