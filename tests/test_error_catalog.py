from youtube_data_pipeline.error_catalog import lookup_error_description, lookup_error_meta


def test_lookup_error_description_returns_text() -> None:
    desc = lookup_error_description("commentThreads.list", "commentsDisabled")
    assert desc is not None
    assert "comentários desativados" in desc


def test_lookup_error_meta_missing_returns_none() -> None:
    assert lookup_error_meta("unknown.list", "missing") is None
