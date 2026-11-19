# Comando: collect_videos_from_terms_resumable.py

> **Nota de 2026-11-06 — participação no Researcher Program encerrada.** O valor `110000` em
> `--credit-limit` é **histórico**: correspondia à concessão de 110.000 unidades/dia, vigente de
> 2026-04-05 a 2026-11-06. Com o encerramento, a chave voltou ao padrão de **10.000 unidades/dia**;
> reduzir o limite antes de executar em produção.

Referência rápida para reconstruir e executar o comando de coleta de vídeos do YouTube a partir de uma lista de termos, com retomada (resumable) por `--run-id`.

Script fonte: `scripts/collect_videos_from_terms_resumable.py`

---

## Comando canônico (exemplo real usado)

```bash
uv run python scripts/collect_videos_from_terms_resumable.py \
  --terms-file ./data/processed/queries/factcheck_unified_q2.txt \
  --credit-limit 110000 \
  --search-pages 2 \
  --region-code BR \
  --api-key YOUR_YOUTUBE_API_KEY \
  --run-id factcheck_unified_q2
```

> A chave de API foi fornecida literalmente no comando original. Troque por uma variável de ambiente (`YOUTUBE_API_KEY`) ou `.env` — veja a seção **Segurança**.

---

## Fluxo do script (o que ele faz)

1. Lê termos do `--terms-file` (UTF-8, um por linha; linhas vazias e com `#` são ignoradas; duplicatas removidas).
2. Para cada termo: faz `search.list` paginado (até `--search-pages` páginas).
3. Extrai `videoId`s únicos e chama `videos.list` em batches de 50 para obter `snippet,statistics,contentDetails`.
4. Persiste um **checkpoint atômico** após cada chamada em `{storage_root}/runs/{run_id}/terms_collection_checkpoint.json`.
5. Atualiza o arquivo público `terms_collection_summary.json` com progresso, créditos e vídeos únicos globais.

---

## Flags (todas lidas direto do código)

| Flag | Obrigatória | Default | Significado |
|------|-------------|---------|-------------|
| `--terms-file` | sim | — | Arquivo UTF-8 (`.txt` com um termo por linha ou `.jsonl` estruturado por cluster). |
| `--api-key` | não* | lê `YOUTUBE_API_KEY` | API key do YouTube Data API v3. *Obrigatória se a variável de ambiente não estiver definida. |
| `--run-id` | não | UUID aleatório | Identificador da execução. **Reutilize o mesmo para retomar.** |
| `--storage-root` | não | `./data/raw` | Diretório base para respostas/checkpoint/logs. |
| `--log-path` | não | `youtube_data_pipeline.jsonl` | Nome do arquivo JSONL de logs dentro do diretório da run. |
| `--compress-responses` | não | desligado | Se presente, salva respostas como `.json.gz`. |
| `--region-code` | não | `BR` | Region code passado ao `search` e `videos`. |
| `--relevance-language` | não | `pt` (em `.jsonl`) | Idioma de relevância (`relevanceLanguage`) enviado ao `search.list`. |
| `--videos-part` | não | `snippet,contentDetails,statistics,status` (em `.jsonl`) | Campo `part` enviado ao `videos.list`. |
| `--dedupe-video-details` | não | ligado (em `.jsonl`) | Pula `videos.list` para `videoId`s já detalhados em clusters anteriores da mesma run. |
| `--search-pages` | não | `3` | Páginas máximas de `search.list` por termo/cluster (≥ 1). |
| `--search-max-results` | não | `50` | Itens por página em `search.list` (1–50). |
| `--credit-limit` | não | `10000` | Limite total de créditos da execução (padrão pós-Researcher Program). |
| `--max-terms` | não | (todos) | Processa apenas os N primeiros termos/clusters (útil para testes). |
| `--summary-name` | não | `terms_collection_summary.json` | Nome do arquivo de resumo público. |

> O comando canônico **não usa** `--storage-root`, `--log-path`, `--compress-responses`, `--search-max-results`, `--max-terms` nem `--summary-name`, então todos caem no default.

---

## Como reconstruir o comando do zero

### 1. Escolher o arquivo de termos

Os termos ficam em `./data/processed/queries/` e são gerados por `scripts/generate_queries_from_factchecks.py`. Verifique os disponíveis:

```bash
ls ./data/processed/queries/
```

### 2. Definir `--credit-limit`

Créditos = quota da YouTube Data API. Referência padrão da API: `search.list` ≈ 100 por chamada, `videos.list` ≈ 1 por chamada. O script delega a contagem ao `youtube_data_pipeline` (verificar a política exata de créditos no pacote se precisar afinar).

- Teste rápido: `10000`
- Execução canônica: `110000` (default)
- Execução longa com muitas páginas: `250000`–`500000`

### 3. Definir `--search-pages`

Cada página traz até `--search-max-results` itens (máx 50). Com `--search-pages 2` e default de 50 itens, você pega até 100 resultados por termo.

### 4. Definir `--region-code`

Código ISO 3166-1 alfa-2. Ex.: `BR`, `US`, `MX`, `AR`.

### 5. Definir `--run-id`

Use nome descritivo, combinando dataset + tier + (opcional) data. Ex.: `factcheck_unified_q2`, `factcheck_unified_q2_20260419`.

### 6. Configurar a `--api-key`

Preferir variável de ambiente (veja **Segurança**). Se omitida, o script lê `YOUTUBE_API_KEY`.

---

## Retomada (resumable)

Rerodar o mesmo comando com **o mesmo `--run-id`** retoma a coleta do último ponto persistido.

O que o script faz ao encontrar um checkpoint existente:

1. Carrega `terms_collection_checkpoint.json` da run.
2. Valida o **fingerprint** da configuração atual contra a armazenada. O fingerprint é SHA-256 sobre:
   - hash dos termos (após dedupe e `--max-terms`)
   - `region_code`
   - `search_pages`
   - `search_max_results`
   - `compress_responses`
   - `summary_name`
3. Se o fingerprint **não bate**, aborta com erro de "Configuração incompatível".
4. Se bate: restaura contador de créditos, sequência de requisições e continua na página/batch pendente.

**O que você pode mudar entre execuções da mesma run**:
- `--credit-limit` (dar mais fôlego à retomada)
- `--api-key`
- `--storage-root` — verificar: o script resolve `storage_root/runs/run_id` como raiz; mudá-lo aponta para outro local.
- `--log-path`

**O que NÃO pode mudar** (quebra o fingerprint):
- `--terms-file` (conteúdo efetivo, após dedupe e `--max-terms`)
- `--region-code`
- `--search-pages`
- `--search-max-results`
- `--compress-responses`
- `--summary-name`

Se precisar mudar qualquer um destes, use um **novo `--run-id`**.

---

## Exemplos variantes

### Teste rápido (5 termos, 1 página, crédito baixo)

```bash
uv run python scripts/collect_videos_from_terms_resumable.py \
  --terms-file ./data/processed/queries/factcheck_unified_q2.txt \
  --max-terms 5 \
  --credit-limit 10000 \
  --search-pages 1 \
  --region-code BR \
  --run-id factcheck_unified_q2_test
```

### Coleta robusta (5 páginas, crédito alto)

```bash
uv run python scripts/collect_videos_from_terms_resumable.py \
  --terms-file ./data/processed/queries/factcheck_unified_q2.txt \
  --credit-limit 500000 \
  --search-pages 5 \
  --region-code BR \
  --run-id factcheck_unified_q2_full
```

### Outra região (México)

```bash
uv run python scripts/collect_videos_from_terms_resumable.py \
  --terms-file ./data/processed/queries/factcheck_unified_q2.txt \
  --credit-limit 110000 \
  --search-pages 2 \
  --region-code MX \
  --run-id factcheck_unified_q2_mx
```

### Usando variável de ambiente + compressão

```bash
export YOUTUBE_API_KEY="sua_chave_real"

uv run python scripts/collect_videos_from_terms_resumable.py \
  --terms-file ./data/processed/queries/factcheck_unified_q2.txt \
  --credit-limit 110000 \
  --search-pages 2 \
  --region-code BR \
  --compress-responses \
  --run-id factcheck_unified_q2
```

---

## Segurança: protegendo a API key

A chave usada no comando original (`AIzaSy...`) é sensível. **Não comite no git.**

### Variável de ambiente (recomendado)

```bash
export YOUTUBE_API_KEY="sua_chave_real"
uv run python scripts/collect_videos_from_terms_resumable.py \
  --terms-file ./data/processed/queries/factcheck_unified_q2.txt \
  --credit-limit 110000 \
  --search-pages 2 \
  --region-code BR \
  --run-id factcheck_unified_q2
```

### Arquivo `.env` (local)

1. Adicione em `.env` na raiz do projeto:
   ```
   YOUTUBE_API_KEY=sua_chave_real
   ```
2. Confirme que `.env` está no `.gitignore`.
3. Antes de rodar: `set -a && source .env && set +a`.

> Verificar se o script carrega `.env` automaticamente — ele usa `os.getenv("YOUTUBE_API_KEY")` diretamente; não há `load_dotenv()` no script, então o `.env` precisa ser carregado pelo shell.

---

## Estrutura de output

Após executar, `{storage_root}/runs/{run_id}/` contém:

```
{run_id}/
├── terms_collection_checkpoint.json   # Estado interno (não editar)
├── terms_collection_summary.json      # Resumo público
├── youtube_data_pipeline.jsonl        # Logs
├── manifest.jsonl                     # Manifesto de requisições
├── endpoint=search/
│   └── origin=q:{hash}/page_NNN.json
└── endpoint=videos/
    └── origin=explicit:{hash}/page_NNN.json
```

---

## Troubleshooting

| Mensagem | Causa provável | Solução |
|----------|----------------|---------|
| `API key não informada. Use --api-key ou YOUTUBE_API_KEY.` | Sem chave | Exportar `YOUTUBE_API_KEY` ou passar `--api-key`. |
| `Arquivo de termos não encontrado: ...` | Caminho errado | `ls ./data/processed/queries/`. |
| `Nenhum termo válido encontrado no arquivo.` | Arquivo vazio ou só comentários | Conferir o conteúdo. |
| `Configuração incompatível para retomar esta run. ...` | Alterou terms/region/paginação/compressão/summary entre execuções do mesmo `--run-id` | Usar novo `--run-id` ou restaurar a configuração original. |
| `--search-max-results deve estar entre 1 e 50.` | Valor fora do range | Ajustar para 1–50. |
| `stop_reason: credit_limit` no resumo | `--credit-limit` esgotado | Retomar com `--credit-limit` maior. |

---

## Fluxo de Fotografia Congelada (`seed_20261107.csv` -> Partições Disjuntas `.jsonl`)

Para a coleta de fotografia congelada integrada à triagem LLM (`seed_20261107.csv`), com até 3 páginas fixas por cluster, janela temporal fechada `[-45d, +90d]` e particionamento estático disjunto entre $K$ contas/operadores:

### 1. Gerar as partições estáticas disjuntas (`prepare_frozen_seed_shards.py`)

```bash
uv run python scripts/prepare_frozen_seed_shards.py \
  --seed-csv ~/Documentos/repositorios/fact-checking-scrape/data/economia/census/seed_20261107.csv \
  --output-dir ./data/processed/queries/frozen_20261107 \
  --prefix econ_snapshot_20261107 \
  --num-shards 3 \
  --shard-strategy interleaved \
  --search-pages 3 \
  --credit-limit 9900 \
  --exclude-monitoring-db ~/Documentos/repositorios/schedule_fake_news_monitoring/data/monitoring.db
```

### 2. Executar cada partição (1 `--run-id` fixo por partição, retomável diariamente)

```bash
uv run python scripts/collect_videos_from_terms_resumable.py \
  --terms-file ./data/processed/queries/frozen_20261107/econ_snapshot_20261107_s01of03.jsonl \
  --run-id econ_snapshot_20261107_s01of03 \
  --search-pages 3 \
  --credit-limit 9900 \
  --region-code BR \
  --relevance-language pt
```

> Para retomar a mesma partição no dia seguinte (após renovação da cota de 10.000 unidades/dia), incremente `--credit-limit` (ex.: `19800` no dia 2, `29700` no dia 3) mantendo o mesmo `--run-id`.

### 3. Unificar as partições disjuntas concluídas (`unify_runs.py --disjoint-shards`)

```bash
uv run python scripts/unify_runs.py \
  --disjoint-shards \
  --keep-credits \
  --source ./data/raw/runs/econ_snapshot_20261107_s01of03 \
  --source ./data/raw/runs/econ_snapshot_20261107_s02of03 \
  --source ./data/raw/runs/econ_snapshot_20261107_s03of03 \
  --target econ_snapshot_20261107_unified
```

---

## Referências rápidas

- YouTube Data API v3: https://developers.google.com/youtube/v3
- Gerador de termos: `scripts/generate_queries_from_factchecks.py`
- Gerador de partições congeladas: `scripts/prepare_frozen_seed_shards.py`
- Metodologia do projeto: `docs/metodologia_busca_youtube.md`
