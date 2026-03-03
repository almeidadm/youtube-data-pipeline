import sqlite3
from src.storage import SqliteStore


def test_upsert_and_exists():
    store = SqliteStore(db_path=":memory:")
    run_id = "r1"
    store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id, "2026-03-02T00:00:00Z"))
    
    video = {"id": "vid1", "snippet": {"title": "Test"}}
    assert not store.video_exists("vid1")
    store.upsert_video(video, run_id=run_id)
    assert store.video_exists("vid1")
    # existing_video_ids should return set of ids present
    ids = store.existing_video_ids(["vid1", "vid2"])
    assert ids == {"vid1"}


def test_quota_summary():
    store = SqliteStore(db_path=":memory:")
    run_id_r1 = "r1"
    run_id_r2 = "r2"
    store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id_r1, "2026-03-02T00:00:00Z"))
    store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id_r2, "2026-03-02T00:00:00Z"))
    
    # insert some requests
    store.log_request(run_id_r1, "channels", "now", "{}", 1)
    store.log_request(run_id_r1, "videos", "now", "{}", 2)
    store.log_request(run_id_r2, "channels", "now", "{}", 3)
    summary_all = store.quota_summary()
    assert summary_all == {"channels": 4, "videos": 2}
    summary_r1 = store.quota_summary(run_id=run_id_r1)
    assert summary_r1 == {"channels": 1, "videos": 2}


def test_upsert_many_and_query():
    store = SqliteStore(db_path=":memory:")
    run_id = "r2"
    store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id, "2026-03-02T00:00:00Z"))
    
    vids = [{"id": str(i)} for i in range(3)]
    count = store.upsert_many_videos(vids, run_id=run_id)
    assert count == 3
    # check they are persisted by querying directly
    cur = store.conn.execute("SELECT COUNT(*) FROM videos")
    assert cur.fetchone()[0] == 3


def test_comment_thread_upsert_and_exists():
    store = SqliteStore(db_path=":memory:")
    run_id = "r1"
    store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id, "2026-03-02T00:00:00Z"))
    
    thread = {"id": "thread1", "snippet": {"topLevelComment": {"id": "comment1"}}}
    video_id = "vid1"
    
    assert not store.comment_thread_exists("thread1")
    store.upsert_comment_thread(thread, video_id=video_id, run_id=run_id)
    assert store.comment_thread_exists("thread1")
    
    # existing_comment_thread_ids should return set of ids present
    ids = store.existing_comment_thread_ids(["thread1", "thread2"])
    assert ids == {"thread1"}


def test_comment_upsert_and_exists():
    store = SqliteStore(db_path=":memory:")
    run_id = "r1"
    store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id, "2026-03-02T00:00:00Z"))
    
    thread = {"id": "thread1", "snippet": {"topLevelComment": {"id": "comment1"}}}
    video_id = "vid1"
    store.upsert_comment_thread(thread, video_id=video_id, run_id=run_id)
    
    comment = {"id": "comment2", "snippet": {"textDisplay": "Test reply", "parentId": "thread1"}}
    thread_id = "thread1"
    
    assert not store.comment_exists("comment2")
    store.upsert_comment(comment, thread_id=thread_id, run_id=run_id)
    assert store.comment_exists("comment2")
    
    # existing_comment_ids should return set of ids present
    ids = store.existing_comment_ids(["comment2", "comment3"])
    assert ids == {"comment2"}


def test_upsert_many_comment_threads_and_comments():
    store = SqliteStore(db_path=":memory:")
    run_id = "r1"
    store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id, "2026-03-02T00:00:00Z"))
    
    video_id = "vid1"
    threads = [
        {"id": "thread1", "snippet": {"topLevelComment": {"id": "comment1"}}},
        {"id": "thread2", "snippet": {"topLevelComment": {"id": "comment2"}}},
    ]
    
    thread_count = store.upsert_many_comment_threads(threads, video_id=video_id, run_id=run_id)
    assert thread_count == 2
    
    cur = store.conn.execute("SELECT COUNT(*) FROM comment_threads")
    assert cur.fetchone()[0] == 2
    
    # Now upsert comments for thread1
    comments = [
        {"id": "reply1", "snippet": {"textDisplay": "Reply 1", "parentId": "thread1"}},
        {"id": "reply2", "snippet": {"textDisplay": "Reply 2", "parentId": "thread1"}},
    ]
    
    comment_count = store.upsert_many_comments(comments, thread_id="thread1", run_id=run_id)
    assert comment_count == 2
    
    cur = store.conn.execute("SELECT COUNT(*) FROM comments")
    assert cur.fetchone()[0] == 2
