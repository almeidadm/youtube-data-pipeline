# Usage Guide

## Jupyter Notebook integration

```python
from youtube_data_pipeline import YouTubeDataClient, YouTubeDataConfig

config = YouTubeDataConfig(
    api_key="...",
    storage_root="./data/raw",
    log_path="./logs/youtube_data_pipeline.jsonl",
    run_id="example-run-001",
)
client = YouTubeDataClient.from_config(config)

search_data = client.fetch(
    "search",
    {
        "part": "snippet",
        "q": "investments portugal brazil",
        "type": "video",
        "maxResults": 25,
    },
    max_pages=2,
    origin="seed-search",
)

videos_data = client.fetch(
    "videos",
    {
        "part": "snippet,statistics,contentDetails",
        "id": ",".join([item["id"]["videoId"] for item in search_data["responses"][0]["items"]]),
    },
)
```

## Credit limit usage

```python
result = client.fetch(
    "search",
    {"part": "snippet", "q": "finance", "type": "video", "maxResults": 25},
    max_pages=5,
    credit_limit=500,
)

if result["limit_reached"]:
    print("Credit limit reached:", result["stopped_reason"])
```

## Recurring execution

```python
from youtube_data_pipeline import RecurringCollector

runner = RecurringCollector(interval_seconds=3600)


def job():
    client.fetch(
        "commentThreads",
        {"part": "snippet", "videoId": "abc123", "maxResults": 100},
        max_pages=5,
    )

runner.run(job, iterations=24)
```

## Logs and credits

Each request page writes a JSONL entry to `./logs/youtube_data_pipeline.jsonl` (default) with fields that are easy to parse:

- event
- run_id
- request_id
- endpoint
- origin
- origin_field
- cost
- cumulative_credits
- per_endpoint
- page
- file

Example JSONL line:

```json
{"timestamp":"2026-03-04T12:00:00+00:00","level":"info","event":"youtube_api_request","run_id":"example-run-001","request_id":"example-run-001-000001","endpoint":"search","origin":"investments portugal brazil","origin_field":"q","cost":100,"cumulative_credits":100,"per_endpoint":{"search":100},"page":1,"file":"/path/data/raw/search/20260304T120000Z/response_001.json"}
```

To override the log path, set `log_path` in `YouTubeDataConfig`. To control the origin explicitly, pass `origin` to `fetch`.

## Storage layout

Responses are stored under a run-scoped directory with an index manifest:

```text
{storage_root}/runs/{run_id}/manifest.jsonl
{storage_root}/runs/{run_id}/endpoint=search/origin=q:HASH/page_001.json
```

Each line in `manifest.jsonl` includes request metadata (origin, params hash, page, credits) to support both manual inspection and machine retrieval.
