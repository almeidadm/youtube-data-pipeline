import json
from pathlib import Path

from youtube_data_pipeline.storage import ResponseStorage


def test_storage_writes_expected_structure(tmp_path: Path) -> None:
    storage = ResponseStorage(root=tmp_path)

    file_path = storage.save("search", {"items": []}, page_idx=1, timestamp="20260101T000000Z")

    assert file_path.exists()
    assert file_path.as_posix().endswith("search/20260101T000000Z/response_001.json")
    assert json.loads(file_path.read_text(encoding="utf-8"))["items"] == []
