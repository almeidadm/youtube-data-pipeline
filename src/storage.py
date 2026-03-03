import json
import os
import sqlite3
import time
from typing import Any, Dict, Iterable, Optional


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value else default


def _connect(db_path: str) -> sqlite3.Connection:
    if db_path != ":memory:":
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class SqliteStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or _env("YOUTUBE_DB_PATH", "data/youtube.db")
        self.conn = _connect(self.db_path)
        self._ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def _ensure_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT,
                params_json TEXT,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS channels (
                id TEXT PRIMARY KEY,
                data_json TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                run_id TEXT,
                FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );

            CREATE TABLE IF NOT EXISTS videos (
                id TEXT PRIMARY KEY,
                data_json TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                run_id TEXT,
                FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );

            CREATE TABLE IF NOT EXISTS comment_threads (
                id TEXT PRIMARY KEY,
                video_id TEXT NOT NULL,
                data_json TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                run_id TEXT,
                FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );

            CREATE TABLE IF NOT EXISTS comments (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                data_json TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                run_id TEXT,
                FOREIGN KEY (run_id) REFERENCES runs(run_id),
                FOREIGN KEY (thread_id) REFERENCES comment_threads(id)
            );

            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                requested_at TEXT NOT NULL,
                resource TEXT NOT NULL,
                params_json TEXT NOT NULL,
                quota_estimate INTEGER NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );
            """
        )
        self.conn.commit()

    def start_run(self, run_id: str, params: Optional[Dict[str, Any]] = None) -> None:
        payload = json.dumps(params or {}, ensure_ascii=True)
        self.conn.execute(
            "INSERT OR REPLACE INTO runs(run_id, started_at, params_json) VALUES (?, ?, ?)",
            (run_id, _utc_now_iso(), payload),
        )
        self.conn.commit()

    def finish_run(self, run_id: str, status: str, notes: Optional[str] = None) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at=?, status=?, notes=? WHERE run_id=?",
            (_utc_now_iso(), status, notes, run_id),
        )
        self.conn.commit()

    def upsert_channel(self, channel: Dict[str, Any], run_id: Optional[str] = None) -> None:
        channel_id = channel.get("id")
        if not channel_id:
            raise ValueError("Channel sem id")
        payload = json.dumps(channel, ensure_ascii=True)
        self.conn.execute(
            """
            INSERT INTO channels(id, data_json, collected_at, run_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                data_json=excluded.data_json,
                collected_at=excluded.collected_at,
                run_id=excluded.run_id
            """,
            (channel_id, payload, _utc_now_iso(), run_id),
        )
        self.conn.commit()

    def upsert_video(self, video: Dict[str, Any], run_id: Optional[str] = None) -> None:
        video_id = video.get("id")
        if not video_id:
            raise ValueError("Video sem id")
        payload = json.dumps(video, ensure_ascii=True)
        self.conn.execute(
            """
            INSERT INTO videos(id, data_json, collected_at, run_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                data_json=excluded.data_json,
                collected_at=excluded.collected_at,
                run_id=excluded.run_id
            """,
            (video_id, payload, _utc_now_iso(), run_id),
        )
        self.conn.commit()

    def upsert_many_channels(
        self, channels: Iterable[Dict[str, Any]], run_id: Optional[str] = None
    ) -> int:
        count = 0
        for channel in channels:
            self.upsert_channel(channel, run_id=run_id)
            count += 1
        return count

    def upsert_many_videos(
        self, videos: Iterable[Dict[str, Any]], run_id: Optional[str] = None
    ) -> int:
        count = 0
        for video in videos:
            self.upsert_video(video, run_id=run_id)
            count += 1
        return count

    def upsert_comment_thread(
        self, thread: Dict[str, Any], video_id: str, run_id: Optional[str] = None
    ) -> None:
        thread_id = thread.get("id")
        if not thread_id:
            raise ValueError("Comment thread sem id")
        payload = json.dumps(thread, ensure_ascii=True)
        self.conn.execute(
            """
            INSERT INTO comment_threads(id, video_id, data_json, collected_at, run_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                data_json=excluded.data_json,
                collected_at=excluded.collected_at,
                run_id=excluded.run_id
            """,
            (thread_id, video_id, payload, _utc_now_iso(), run_id),
        )
        self.conn.commit()

    def upsert_comment(
        self, comment: Dict[str, Any], thread_id: str, run_id: Optional[str] = None
    ) -> None:
        comment_id = comment.get("id")
        if not comment_id:
            raise ValueError("Comment sem id")
        payload = json.dumps(comment, ensure_ascii=True)
        self.conn.execute(
            """
            INSERT INTO comments(id, thread_id, data_json, collected_at, run_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                data_json=excluded.data_json,
                collected_at=excluded.collected_at,
                run_id=excluded.run_id
            """,
            (comment_id, thread_id, payload, _utc_now_iso(), run_id),
        )
        self.conn.commit()

    def upsert_many_comment_threads(
        self, threads: Iterable[Dict[str, Any]], video_id: str, run_id: Optional[str] = None
    ) -> int:
        count = 0
        for thread in threads:
            self.upsert_comment_thread(thread, video_id=video_id, run_id=run_id)
            count += 1
        return count

    def upsert_many_comments(
        self, comments: Iterable[Dict[str, Any]], thread_id: str, run_id: Optional[str] = None
    ) -> int:
        count = 0
        for comment in comments:
            self.upsert_comment(comment, thread_id=thread_id, run_id=run_id)
            count += 1
        return count

    # helper methods for incremental runs
    def video_exists(self, video_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM videos WHERE id=? LIMIT 1",
            (video_id,)
        )
        return cur.fetchone() is not None

    def existing_video_ids(self, ids: Iterable[str]) -> set:
        ids_list = list(ids)
        if not ids_list:
            return set()
        placeholder = ",".join(["?" for _ in ids_list])
        cur = self.conn.execute(
            f"SELECT id FROM videos WHERE id IN ({placeholder})",
            ids_list,
        )
        return {row[0] for row in cur.fetchall()}

    # helper methods for comments
    def comment_thread_exists(self, thread_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM comment_threads WHERE id=? LIMIT 1",
            (thread_id,)
        )
        return cur.fetchone() is not None

    def comment_exists(self, comment_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM comments WHERE id=? LIMIT 1",
            (comment_id,)
        )
        return cur.fetchone() is not None

    def existing_comment_thread_ids(self, ids: Iterable[str]) -> set:
        ids_list = list(ids)
        if not ids_list:
            return set()
        placeholder = ",".join(["?" for _ in ids_list])
        cur = self.conn.execute(
            f"SELECT id FROM comment_threads WHERE id IN ({placeholder})",
            ids_list,
        )
        return {row[0] for row in cur.fetchall()}

    def existing_comment_ids(self, ids: Iterable[str]) -> set:
        ids_list = list(ids)
        if not ids_list:
            return set()
        placeholder = ",".join(["?" for _ in ids_list])
        cur = self.conn.execute(
            f"SELECT id FROM comments WHERE id IN ({placeholder})",
            ids_list,
        )
        return {row[0] for row in cur.fetchall()}

    def log_request(
        self,
        run_id: Optional[str],
        resource: str,
        requested_at: str,
        params_json: str,
        quota_estimate: int,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO requests(run_id, requested_at, resource, params_json, quota_estimate)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, requested_at, resource, params_json, quota_estimate),
        )
        self.conn.commit()

    def quota_summary(self, run_id: Optional[str] = None) -> Dict[str, int]:
        """Return a dict mapping resource names to total quota consumed.

        If ``run_id`` is provided, the summary is filtered to that run only.
        """
        if run_id:
            cur = self.conn.execute(
                "SELECT resource, SUM(quota_estimate) FROM requests WHERE run_id=? GROUP BY resource",
                (run_id,),
            )
        else:
            cur = self.conn.execute(
                "SELECT resource, SUM(quota_estimate) FROM requests GROUP BY resource"
            )
        result: Dict[str, int] = {}
        for resource, total in cur.fetchall():
            result[resource] = total or 0
        return result
