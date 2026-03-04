from youtube_data_pipeline.errors import RetryPolicy, build_error_info


def test_error_info_includes_description() -> None:
    info = build_error_info(
        endpoint="comments.list",
        status=404,
        reason="commentNotFound",
        message="Not found",
        domain=None,
        location=None,
        location_type=None,
        raw_errors=[],
        attempt=1,
        page=1,
        origin="origin",
        params_hash=None,
        uri=None,
        retry_policy=RetryPolicy(),
    )

    assert info.description is not None
    assert "comentários" in info.description
