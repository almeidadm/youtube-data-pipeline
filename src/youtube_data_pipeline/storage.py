import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(slots=True)
class ResponseStorage:
    root: Path

    def save(
        self,
        endpoint: str,
        payload: dict,
        page_idx: int = 1,
        timestamp: str | None = None,
    ) -> Path:
        ts = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = self.root / endpoint / ts
        output_dir.mkdir(parents=True, exist_ok=True)

        output_file = output_dir / f"response_{page_idx:03d}.json"
        output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return output_file
