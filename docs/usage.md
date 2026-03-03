# Guia de uso

## Integração em Jupyter Notebook

```python
from youtube_data_pipeline import YouTubeDataClient, YouTubeDataConfig

config = YouTubeDataConfig(api_key="...", storage_root="./data/raw")
client = YouTubeDataClient.from_config(config)

search_data = client.fetch(
    "search",
    {
        "part": "snippet",
        "q": "investimentos portugal brasil",
        "type": "video",
        "maxResults": 25,
    },
    max_pages=2,
)

videos_data = client.fetch(
    "videos",
    {
        "part": "snippet,statistics,contentDetails",
        "id": ",".join([item["id"]["videoId"] for item in search_data["responses"][0]["items"]]),
    },
)
```

## Execução recorrente

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

## Logs e créditos

Cada chamada gera log estruturado com:

- endpoint;
- custo estimado da chamada;
- total acumulado de créditos na sessão;
- arquivo de saída salvo.
