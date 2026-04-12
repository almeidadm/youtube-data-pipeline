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

## Bulk terms collection (CLI)

Use `scripts/collect_videos_from_terms_resumable.py` when you have many search
terms in a file (one term per line, e.g. 1600 terms) and need to resume the run
after a credit reset or interruption.

```bash
uv run python scripts/collect_videos_from_terms_resumable.py \
  --terms-file ./data/terms.txt \
  --run-id my-bulk-run-001 \
  --credit-limit 10000 \
  --search-pages 3 \
  --region-code BR
```

Re-run the same command with the same `--run-id` to continue from the checkpoint
instead of redoing completed pages and batches.

It stores all `search` and `videos` responses in the run folder and writes:

```text
{storage_root}/runs/{run_id}/terms_collection_summary.json
{storage_root}/runs/{run_id}/terms_collection_checkpoint.json
```

If you want to trigger this CLI from a notebook, see
`docs/bulk_terms_collection.md`.

## Full channel collection (CLI)

Use `scripts/collect_channel_bundle.py` when you want a complete bundle for one
channel ID (account data, activities/posts, uploads/videos, and engagement).

```bash
uv run python scripts/collect_channel_bundle.py \
  --channel-id UC_x5XG1OV2P6uZZ5FSM9Ttw \
  --credit-limit 10000 \
  --max-videos 200 \
  --video-pages 10 \
  --comment-pages 2 \
  --reply-pages 1
```

Main output:

```text
{storage_root}/runs/{run_id}/channel_collection_summary.json
```

For a complete guide (flow, parameters, artifacts), see
`docs/channel_collection.md`.

## Development

```bash
uv run ruff check .
uv run pytest
```

## Structure

- `src/youtube_data_pipeline/`: main library.
- `tests/`: test suite with pytest.
- `docs/`: functional documentation and design docs.
