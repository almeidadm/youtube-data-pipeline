from youtube_data_pipeline.credits import CreditMonitor


def test_credit_monitor_tracks_totals() -> None:
    monitor = CreditMonitor()

    monitor.register("search", 100)
    state = monitor.register("videos", 1)

    assert state["total_spent"] == 101
    assert state["per_endpoint"] == {"search": 100, "videos": 1}
