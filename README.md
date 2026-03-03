# youtube-data-pipeline

Biblioteca em Python 3.12 para simplificar a comunicação com a YouTube Data API v3 usando `google-api-python-client`.

## Objetivos

- Coleta padronizada para múltiplos endpoints da API do YouTube.
- Monitoramento de consumo de créditos por requisição e por sessão.
- Persistência de respostas no padrão `{endpoint}/{timestamp}/{response files}`.
- Integração simples com notebooks e orquestração de tarefas recorrentes.

## Instalação com uv

```bash
uv sync
```

## Exemplo rápido

```python
from youtube_data_pipeline import YouTubeDataClient, YouTubeDataConfig

config = YouTubeDataConfig(
    api_key="SUA_API_KEY",
    storage_root="./data/raw",
)

client = YouTubeDataClient.from_config(config)
result = client.fetch(
    endpoint="search",
    params={
        "part": "snippet",
        "q": "finanças comportamentais",
        "maxResults": 5,
        "type": "video",
    },
)

print(result["files"])       # Arquivos salvos
print(result["credit_state"])  # Créditos consumidos
```

## Desenvolvimento

```bash
uv run ruff check .
uv run pytest
```

## Estrutura

- `src/youtube_data_pipeline/`: biblioteca principal.
- `tests/`: suíte de testes com pytest.
- `docs/`: documentação funcional e design docs.
