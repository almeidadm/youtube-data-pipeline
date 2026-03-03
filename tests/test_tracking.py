import pytest

from src.quota import estimate_quota
from src.storage import SqliteStore
from src.tracking import RequestTracker


class TestEstimateQuota:
    def test_search_costs_100(self):
        assert estimate_quota("search", {}) == 100

    def test_channels_costs_1(self):
        assert estimate_quota("channels", {"id": "ch1"}) == 1

    def test_videos_costs_1(self):
        assert estimate_quota("videos", {}) == 1

    def test_playlistitems_costs_1(self):
        assert estimate_quota("playlistItems", {}) == 1

    def test_commentthreads_costs_1(self):
        assert estimate_quota("commentThreads", {}) == 1

    def test_comments_costs_1(self):
        assert estimate_quota("comments", {}) == 1

    def test_playlists_costs_1(self):
        assert estimate_quota("playlists", {}) == 1

    def test_subscriptions_costs_1(self):
        assert estimate_quota("subscriptions", {}) == 1

    def test_activities_costs_1(self):
        assert estimate_quota("activities", {}) == 1

    def test_unknown_resource_costs_default(self):
        assert estimate_quota("unknownResource", {}) == 1

    def test_case_insensitive(self):
        assert estimate_quota("SEARCH", {}) == 100
        assert estimate_quota("Search", {}) == 100
        assert estimate_quota("CHANNELS", {}) == 1


class TestRequestTracker:
    def test_tracker_logs_request(self):
        store = SqliteStore(db_path=":memory:")
        run_id = "test_run"
        # Create run first
        store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id, "2026-03-02T00:00:00Z"))
        
        tracker = RequestTracker(store, run_id=run_id)
        tracker.on_request("channels", {"id": "ch1", "part": "snippet"})

        summary = store.quota_summary(run_id=run_id)
        assert summary["channels"] == 1

    def test_tracker_logs_multiple_requests(self):
        store = SqliteStore(db_path=":memory:")
        run_id = "run1"
        store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id, "2026-03-02T00:00:00Z"))
        
        tracker = RequestTracker(store, run_id=run_id)

        tracker.on_request("channels", {})
        tracker.on_request("videos", {})
        tracker.on_request("channels", {})

        summary = store.quota_summary(run_id=run_id)
        assert summary["channels"] == 2
        assert summary["videos"] == 1

    def test_tracker_handles_search_correctly(self):
        store = SqliteStore(db_path=":memory:")
        run_id = "search_run"
        store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id, "2026-03-02T00:00:00Z"))
        
        tracker = RequestTracker(store, run_id=run_id)
        tracker.on_request("search", {"q": "test", "part": "snippet"})

        summary = store.quota_summary(run_id=run_id)
        assert summary["search"] == 100

    def test_tracker_with_json_params(self):
        store = SqliteStore(db_path=":memory:")
        run_id = "json_run"
        store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id, "2026-03-02T00:00:00Z"))
        
        tracker = RequestTracker(store, run_id=run_id)

        complex_params = {
            "id": "ch1",
            "part": ["snippet", "statistics"],
            "fields": "id,snippet.title",
        }
        tracker.on_request("channels", complex_params)

        # Should not raise and should log correctly
        summary = store.quota_summary(run_id=run_id)
        assert summary["channels"] == 1

    def test_tracker_request_stored_with_timestamp(self):
        store = SqliteStore(db_path=":memory:")
        run_id = "time_run"
        store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id, "2026-03-02T00:00:00Z"))
        
        tracker = RequestTracker(store, run_id=run_id)
        tracker.on_request("channels", {"id": "ch1"})

        # Fetch stored request
        cur = store.conn.execute(
            "SELECT requested_at, params_json, quota_estimate FROM requests WHERE run_id = ?",
            (run_id,)
        )
        row = cur.fetchone()
        assert row is not None
        requested_at, params_json, quota_estimate = row
        assert requested_at is not None
        assert "ch1" in params_json
        assert quota_estimate == 1

    def test_tracker_multiple_runs_isolated(self):
        store = SqliteStore(db_path=":memory:")
        run_id_a = "run_a"
        run_id_b = "run_b"
        store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id_a, "2026-03-02T00:00:00Z"))
        store.conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id_b, "2026-03-02T00:00:00Z"))
        
        tracker1 = RequestTracker(store, run_id=run_id_a)
        tracker2 = RequestTracker(store, run_id=run_id_b)

        tracker1.on_request("channels", {})
        tracker1.on_request("channels", {})
        tracker2.on_request("videos", {})

        summary_a = store.quota_summary(run_id=run_id_a)
        summary_b = store.quota_summary(run_id=run_id_b)

        assert summary_a == {"channels": 2}
        assert summary_b == {"videos": 1}
