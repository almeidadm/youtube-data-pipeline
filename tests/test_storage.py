import json
from pathlib import Path

from youtube_data_pipeline.storage import ResponseStorage


def test_storage_writes_expected_structure(tmp_path: Path) -> None:
    storage = ResponseStorage(root=tmp_path, run_id="run-test")

    file_path = storage.save(
        "search",
        {"items": []},
        page_idx=1,
        timestamp="20260101T000000Z",
        origin="finance",
        origin_field="q",
    )

    assert file_path.exists()
    assert "runs/run-test" in file_path.as_posix()
    assert "endpoint=search" in file_path.as_posix()
    assert "origin=q:" in file_path.as_posix()
    assert file_path.name.startswith("page_001")
    assert json.loads(file_path.read_text(encoding="utf-8"))["items"] == []
