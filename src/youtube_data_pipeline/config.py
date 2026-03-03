from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class YouTubeDataConfig:
    api_key: str
    storage_root: str | Path = "./data/raw"
    discovery_cache: bool = False

    def normalized_storage_root(self) -> Path:
        return Path(self.storage_root).expanduser().resolve()
