# youtube-data-pipeline

Python 3.12 library to simplify communication with the YouTube Data API v3 using `google-api-python-client`.

## Objectives

- Standardized collection for multiple YouTube API endpoints.
- Monitoring of credit consumption per request and per session.
- Response persistence in the `{endpoint}/{timestamp}/{response files}` pattern.
- Simple integration with notebooks and recurring task orchestration.

## Installation with uv

```bash
uv sync
```

## Quick example

```python
from youtube_data_pipeline import YouTubeDataClient, YouTubeDataConfig

config = YouTubeDataConfig(
    api_key="YOUR_API_KEY",
    storage_root="./data/raw",
)

client = YouTubeDataClient.from_config(config)
result = client.fetch(
    endpoint="search",
    params={
        "part": "snippet",
        "q": "behavioral finance",
        "maxResults": 5,
        "type": "video",
    },
)

print(result["files"])       # Saved files
print(result["credit_state"])  # Credits consumed
```

## Development

```bash
uv run ruff check .
uv run pytest
```

## Structure

- `src/youtube_data_pipeline/`: main library.
- `tests/`: test suite with pytest.
- `docs/`: functional documentation and design docs.
