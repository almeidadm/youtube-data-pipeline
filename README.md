# YouTube API Collector (MVP)

Pequeno projeto para coleta de dados públicos do YouTube usando a Data API v3
com foco em uso eficiente de cota. Destinado a pesquisas e dados de análise;
o MVP suporta canais, vídeos e comentários (opcional).

## Estrutura

- `src/` : implementação principal
  - `youtube_client.py` : cliente HTTP com retry e logger de requisições
  - `collection_flow.py` : funções de fluxo (canal -> playlist -> vídeos;
    comentários; incremental)
  - `storage.py` : SQLite com esquemas para runs, canais, vídeos, requests
  - `tracking.py` : tracker que estima cota e escreve em `requests`
  - `cli.py` : utilitário de linha de comando para coletar/exportar dados
  - `quota.py` : lógica de estimativa de custo por recurso (usada no tracker)

## Documentação adicional

A documentação estendida do projeto fica na pasta `docs/`. Consulte
[`docs/README.md`](docs/README.md) para uma visão geral e links rápidos aos
manuais de *usage*, *architecture* e *development*.


- `config/` : planos de coleta e credenciais (`collection_plan.json`,
  `credentials.md`)
- `tests/` : testes mínimos com `pytest` cobrindo armazenamento,
  fluxos e CLI

## Instalação

1. Clone o repositório e entre no diretório.
2. Configure variáveis de ambiente (`.env` ou export):
   ```bash
   export YOUTUBE_API_KEY=YOUR_KEY_HERE
   export YOUTUBE_DB_PATH=data/youtube.db
   ```
3. (Opcional) configure OAuth tokens se precisar de endpoints privados.
4. Instale dependências de desenvolvimento (por exemplo, `pytest`):
   ```bash
   python -m pip install -U pytest
   ```

## Uso

O script CLI pode ser executado de duas formas equivalentes:
```bash
python src/cli.py <command>      # execução direta
python -m src.cli <command>      # execução como módulo (alternativa)
```

### Plano de sementes
Edite `config/collection_plan.json` com uma lista de `channel_ids`,
`playlists` ou `queries` (o MVP começa apenas com `channel_ids`).  O arquivo de
configuração também suporta um campo opcional `pipeline_type` (padrão
`channel_videos`) e uma seção `pipeline_options` com parâmetros específicos de
cada pipeline.

As pipelines disponíveis são:

- `channel_videos` – coleta vídeos de canais listados em `channel_ids`.
- `search_channels` – executa buscas de canais por query e registra os IDs
  retornados.
- `search_videos` – busca vídeos por termo e grava metadados dos vídeos.
- `video_comments` – coleta threads e respostas de comentários para vídeos em
  `video_ids`.

Os elementos de seeds usados por cada pipeline são descritos na documentação
em `docs/architecture.md`.

### Coleta
```bash
# coleta completa (refaz tudo)
python src/cli.py collect --seeds config/collection_plan.json

# coleta incremental (pula vídeos já existentes)
python src/cli.py collect --incremental
```
O progresso e estimativa de cota são registrados no banco SQLite.

### Consulta de cota
```bash
# sumariza o consumo de quota por recurso
python src/cli.py quota

# restringe ao último run
python src/cli.py quota --run-id <uuid>
```


### Exportação
```bash
# exportar todas as tabelas como JSON lines
python src/cli.py export --tables all > dump.jsonl

# exportar apenas vídeos para arquivo
python src/cli.py export --tables videos --output videos.jsonl

# exportar apenas comentários (threads e replies)
python src/cli.py export --tables comment_threads comments --output comments.jsonl

# exportar múltiplas tabelas específicas
python src/cli.py export --tables channels videos comments > data.jsonl
```

### Desenvolvimento/testes
Execute `pytest` na raiz do projeto para rodar os testes mínimos.

```bash
pytest -q
```

Os testes cobrem o cliente HTTP, fluxo de coleta, persistência, rastreamento de
quota, CLI e log. Somente dependências leves do `pytest` são necessárias, o que
mantém o projeto fácil de instalar em ambientes interativos como notebooks.

## Esquema de Banco de Dados

O projeto usa SQLite com as seguintes tabelas:

### `runs`
Metadados de cada execução de coleta:
- `id` (UUID): identificador único
- `started_at` (timestamp): início da coleta
- `completed_at` (timestamp, nullable): fim (se completado)
- `status` (str): estado (running, completed, failed)

### `channels`
Canais coletados:
- `id` (str, chave primária): ID do canal do YouTube
- `data_json` (str): documento JSON completo (snippet, statistics, contentDetails)
- `run_id` (UUID): referência ao run de coleta

### `videos`
Vídeos coletados:
- `id` (str, chave primária): ID do vídeo do YouTube
- `data_json` (str): documento JSON completo (snippet, contentDetails, statistics)
- `run_id` (UUID): referência ao run de coleta

### `requests`
Log de requisições HTTP (para rastreio de quota):
- `id` (autoincrement)
- `run_id` (UUID): referência ao run
- `resource` (str): tipo de recurso (channels, videos, search, etc)
- `requested_at` (timestamp): momento da requisição
- `params_json` (str): parâmetros usados
- `quota_estimate` (int): custo estimado em unidades de quota

### `comment_threads`
Threads de comentários coletadas:
- `id` (str, chave primária): ID da thread do YouTube
- `video_id` (str): ID do vídeo ao qual a thread pertence
- `data_json` (str): documento JSON completo (snippet, replies)
- `collected_at` (timestamp): momento da coleta
- `run_id` (UUID): referência ao run de coleta

### `comments`
Replies de comentários coletados:
- `id` (str, chave primária): ID do documento comentário do YouTube
- `thread_id` (str, chave estrangeira): ID da thread pai
- `data_json` (str): documento JSON completo (snippet)
- `collected_at` (timestamp): momento da coleta
- `run_id` (UUID): referência ao run de coleta

## Exemplos de Uso Prático

### Configurar sementes e coletar
```bash
# Editar config/collection_plan.json
cat config/collection_plan.json
# {
#   "channel_ids": ["UC...", "UC..."],
#   "playlists": [],
#   "queries": [],
#   "daily_quota_limit": 10000,
#   ...
# }

# Coletar uma vez
python src/cli.py collect

# Coletar incrementalmente (pula vídeos já conhecidos)
python src/cli.py collect --incremental
```

### Monitorar uso de quota
```bash
# Mostrar resumo total
python src/cli.py quota

# Filtrar por último run
python src/cli.py quota --run-id <uuid>

# Exemplo de saída:
# Total API quota used: 150 units
# Breakdown by resource:
#   channels: 5
#   playlistItems: 20
#   videos: 120
#   search: 0
# (filtered to run_id=abcd-efgh)
```

### Exportar dados
```bash
# Exportar para JSONL (formato legível)
python src/cli.py export --tables all > dump.jsonl

# Exportar apenas vídeos
python src/cli.py export --tables videos > videos.jsonl

# Exportar comentários
python src/cli.py export --tables comment_threads comments > comments.jsonl

# Importar em ferramentas externas:
jq '.[] | select(.data_json | fromjson | has("statistics"))' dump.jsonl
```

### Uso em Jupyter Notebook

O projeto também pode ser usado interativamente em um ambiente de notebook
Jupyter.  Um exemplo funcional está incluído em
`notebooks/YouTube_API_Collector_Examples.ipynb`.  Abra o notebook a partir da
raiz do repositório (`jupyter notebook` ou `jupyter lab`) e ele mostrará como
adicionar `..` ao `sys.path`, configurar variáveis de ambiente, importar as
funções principais (`do_collect`, `SqliteStore`, etc.) e disparar uma coleta
programaticamente.  Isso é útil para análise exploratória ou desenvolvimentos
personalizados que não se encaixam perfeitamente no CLI.

## Considerações sobre Quota

A cota padrão é **10.000 unidades por dia**.

Custos por operação:
- Leitura de lista (`channels`, `videos`, `playlistItems`, etc.): **1 unidade**
- Busca (`search.list`): **100 unidades**
- Comentários (`commentThreads`, `comments`): **1 unidade**
- Operações de escrita: **50+ unidades** (não usadas neste MVP)

**Dica**: Use a coleta incremental para evitar refazer chamadas a `videos.list`
para vídeos já conhecidos.

## Monitoramento e Logs

A aplicação registra todas as atividades em formato legível para análise humana.

### Visualizar logs
```bash
# Listar arquivos de log disponíveis
python src/cli.py logs

# Exemplo de saída:
# Log directory: data/logs
# Found 3 log file(s):
#   - collection_20260302_121530_a1b2c3d4.log (245.3 KB, modified: 2026-03-02T12:15:30)
#   - collection_20260302_100000_x9y8z7w6.log (156.8 KB, modified: 2026-03-02T10:00:00)
#   - collection_20260301_180000_m5n4o3p2.log (89.2 KB, modified: 2026-03-01T18:00:00)
```

### Estrutura dos logs
Os logs são salvos em `data/logs/` com nome no formato:
`collection_YYYYMMDD_HHMMSS_run_id.log`

Cada log contém:
- **[INFO]**: eventos principais (início/fim de coleta, canais processados)
- **[WARNING]**: retries de requisições, limites de cota próximos
- **[ERROR]**: erros de coleta ou falhas de API
- **[DEBUG]**: detalhes de todas as requisições HTTP (visto apenas em arquivo)

Exemplo de log:
```
[INFO    ] 2026-03-02 12:15:30 | Starting full collection run: a1b2c3d4-...
[INFO    ] 2026-03-02 12:15:30 | Processing 5 channel(s)
[INFO    ] 2026-03-02 12:15:30 | Processing channel_id=UCAbc123 (title: Example Channel)
[DEBUG   ] 2026-03-02 12:15:30 | API GET channels | id=UCAbc123 [quota: 1]
[DEBUG   ] 2026-03-02 12:15:30 | API GET playlistItems | playlistId=ULAbc123 [quota: 1]
[DEBUG   ] 2026-03-02 12:15:31 | API GET videos | [quota: 50]
[INFO    ] 2026-03-02 12:15:32 | Channel UCAbc123: collected 50 videos, quota used: 52
[INFO    ] 2026-03-02 12:15:45 | Collection run a1b2c3d4-... completed successfully
```

### Análise de logs
Para análise posterior:
```bash
# Ver apenas erros
grep "\[ERROR" data/logs/collection_*.log

# Ver retries (problemas de rede/rate limit)
grep "Retrying" data/logs/collection_*.log

# Ver quota por run
grep "quota checkpoint\|Quota limit" data/logs/collection_*.log

# Tempos de coleta
grep "Starting\|completed successfully\|failed" data/logs/collection_*.log
```

### Níveis de detalhe
A aplicação usa logs em dois níveis:
1. **Console (INFO level)**: apenas eventos importantes para o usuário
2. **Arquivo (DEBUG level)**: todas as requisições e detalhes para auditoria

Você pode aumentar o nível de detalhe passando argumentos à função `setup_monitoring()`.

## Observações
- O fluxo principal segue as recomendações do `instructions.md`:
  usar `channels.list` para obter playlist de uploads,
  `playlistItems.list` para listar vídeos e `videos.list` para detalhes.
- A coleta incremental usa o banco para evitar chamadas repetidas a
  `videos.list` por `videoId` já conhecidos.
- Comentários são opcionais e custam cota; estão implementados na
  função `collect_video_comments_all`.
- Logs são armazenados em arquivo com rotação automática (máx 10 MB por arquivo).

## Troubleshooting

### "API key not found" ou "Defina YOUTUBE_API_KEY ou YOUTUBE_ACCESS_TOKEN"
Certifique-se de que:
1. A variável `YOUTUBE_API_KEY` está definida no ambiente
2. Ou a credencial está configurada em `config/credentials.md`

Solução:
```bash
export YOUTUBE_API_KEY=sua_chave_aqui
python src/cli.py collect
```

### "HTTP 403 - quotaExceeded"
Você ultrapassou o limite diário de 10.000 unidades. Solução:
- Aguarde até o dia seguinte
- Reduza o número de vídeos/canais a coletar
- Use `--incremental` para evitar coleta repetida

### "HTTP 429 - Too Many Requests"
Você enviou muitas requisições rapidamente. O cliente tenta automaticamente
com `backoff` exponencial (até 3 tentativas por padrão).

Se persistir:
```bash
# Aumentar timeout e máximo de retries
export YOUTUBE_TIMEOUT_MS=60000
export YOUTUBE_RETRY_MAX=5
python src/cli.py collect
```

### Banco de dados corrompido
Se receber erros SQL:
```bash
# Remover o banco (perda de dados!)
rm -f data/youtube.db
# Recoletar do zero
python src/cli.py collect
```

## Testes

Executar a suite de testes:
```bash
pytest -v
```

Testes incluem:
- `test_youtube_client.py`: testes do cliente HTTP (retry, timeout, headers)
- `test_storage.py`: persistência e deduplicação
- `test_collection_flow.py`: fluxo de coleta e paginação
- `test_cli.py`: comandos de linha de comando
- `test_tracking.py`: rastreamento de quota e requisições

## Limitações e próximas etapas
- Não há tratamento automático de limites diários; é responsabilidade
  do usuário monitorar a coluna `quota_estimate` na tabela `requests`.
- Documentação adicional e scripts de orquestração podem ser adicionados.
- Suporte a cache de respostas ou modo offline pode melhorar a experiência
  em ambientes com redes intermitentes.

