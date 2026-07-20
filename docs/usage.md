# Usage Guide

## Jupyter Notebook integration

```python
from youtube_data_pipeline import YouTubeDataClient, YouTubeDataConfig

config = YouTubeDataConfig(
    api_key="...",
    storage_root="./data/raw",
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

## Error handling e descrições

As descrições por endpoint estão em `tipos_de_erro.md` e são adicionadas ao campo
`description` em logs e artefatos de erro.

Exemplo de log de erro:

```json
{"timestamp":"2026-03-04T12:00:00+00:00","level":"error","event":"youtube_api_error","run_id":"example-run-001","request_id":"example-run-001-000123","endpoint":"comments","page":1,"status":404,"reason":"commentNotFound","description":"Não foi possível encontrar um ou mais dos comentários especificados. Verifique se os valores dos parâmetros id e parentId da solicitação estão corretos.","action":"skip"}
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

Each request page writes a JSONL entry to a run-scoped log file (default):

```
{storage_root}/runs/{run_id}/youtube_data_pipeline-{run_id_hash}.jsonl
```

The `{run_id_hash}` is the first 12 hex chars of the SHA-256 hash of `run_id`, keeping log files unique and short.

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
{"timestamp":"2026-03-04T12:00:00+00:00","level":"info","event":"youtube_api_request","run_id":"example-run-001","request_id":"example-run-001-000001","endpoint":"search","origin":"investments portugal brazil","origin_field":"q","cost":100,"cumulative_credits":100,"per_endpoint":{"search":100},"page":1,"file":"/path/data/raw/runs/example-run-001/endpoint=search/origin=q:HASH/page_001.json"}
```

To override the base log filename, set `log_path` in `YouTubeDataConfig`. The file is always stored under `{storage_root}/runs/{run_id}/` and suffixed with the run hash. To control the origin explicitly, pass `origin` to `fetch`.

## Storage layout

Responses are stored under a run-scoped directory with an index manifest:

```text
{storage_root}/runs/{run_id}/manifest.jsonl
{storage_root}/runs/{run_id}/endpoint=search/origin=q:HASH/page_001.json
```

Each line in `manifest.jsonl` includes request metadata (origin, params hash, page, credits) to support both manual inspection and machine retrieval.
