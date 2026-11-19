# CLAUDE.md — Diretrizes para Claude Code

Este arquivo define as diretrizes que o Claude Code deve seguir ao trabalhar neste repositório.

---

## Projeto

Cliente Python da YouTube Data API v3 (pacote `youtube-data-pipeline`) que cobre as etapas **E4–E6** do pipeline da dissertação: geração de queries a partir de claims de fact-checking, busca de vídeos por termos e coleta de metadados, comentários e captions.

As **restrições legais (ToS do YouTube) e de quota são requisitos de engenharia de primeira ordem**, não detalhes operacionais. Decisões metodológicas (o que coletar, como alinhar claim-vídeo) vivem no vault da dissertação (`~/Documentos/PPGCC`, RDPs em `decisoes/`), não aqui. Ficha do repo: `repos/youtube-api.md` no vault.

## Restrições inegociáveis (ToS + quota)

- **Apenas metadados e IDs podem ser persistidos.** Nunca baixar vídeos ou áudios, nem integrar `yt-dlp` ou similar.
- Exceção documentada: `scripts/collect_videos_from_daily_factchecks.py` persiste **somente IDs de vídeo** no sightings file; os payloads brutos de `search.list` ficam nos runs apenas para auditoria.
- Quota: **10.000 unidades/dia** (padrão da YouTube Data API) desde 2026-11-06, quando o primeiro ciclo do YouTube Researcher Program foi encerrado (entre 2026-04-05 e 2026-11-06, a chave teve 110.000 unidades/dia). Para a janela de **6 meses (180 dias)** do novo plano do YRP (`docs/youtube_researcher_program_quota_plan_20261118.md`), o `CreditMonitor` suporta governança **dual-bucket de 250.000 unidades/dia** (`200.000` unidades/dia = `2.000` chamadas/dia para `search.list` + `50.000` unidades/dia para endpoints gerais de leitura, bloqueando `captions.list` via `allow_endpoints` no perfil YRP). Todo fluxo de coleta deve respeitar `--credit-limit` / `--search-credit-limit` / `--general-credit-limit` e registrar o consumo por requisição (`credits.py`).

## Estrutura

```
src/youtube_data_pipeline/
    client.py          — YouTubeDataClient (validate → execute → save → credit → paginação)
    config.py          — YouTubeDataConfig (api key, storage_root, descoberta via env/.env)
    credits.py         — CreditMonitor (custo por endpoint, acumulado por sessão)
    storage.py         — persistência run-scoped + manifest.jsonl
    errors.py          — política de erro determinística (retry / stop / skip)
    error_catalog.py   — descrições humanas de erro por endpoint
    orchestration.py   — RecurringCollector (execução recorrente)
    endpoints/         — registry declarativo de endpoints (resource, method, custo-base)
scripts/               — CLIs de coleta (terms resumable, frozen shards, channel bundle, comments,
                         longitudinal waves, recidivist channels, daily factchecks, queries Q1/Q2/Q3)
docs/                  — guias funcionais + índice Diátaxis (README.md) + C4 (architecture/c4.md)
tests/                 — pytest
data/runs/{run_id}/    — manifest.jsonl + respostas JSON por página (gitignored)
```

## Comandos

```bash
uv sync
uv run pytest
uv run ruff check .

# Coleta em lote por termos (retomável: re-rodar com o mesmo --run-id continua do checkpoint)
uv run python scripts/collect_videos_from_terms_resumable.py \
  --terms-file ./data/terms.txt --run-id my-bulk-run-001 \
  --credit-limit 10000 --search-pages 3 --region-code BR

# Bundle completo de canal
uv run python scripts/collect_channel_bundle.py --channel-id <id> --credit-limit 10000

# Queries a partir de fact-checks (tiers Q1/Q2/Q3 — metodologia em docs/metodologia_busca_youtube.md)
uv run python scripts/generate_queries_from_factchecks.py \
  --csv-path data/raw/predictions_s2_factcheck_scrape_unified.csv \
  --output-dir data/processed/queries --dataset-name factcheck_unified
```

## Guarda operacional

- **Idempotência**: o coletor diário não reprocessa pares `(date, item_id)` já coletados; o coletor por termos retoma do checkpoint sem refazer páginas concluídas.
- **Nunca apagar runs** sem decisão registrada — os payloads brutos são a trilha de auditoria da coleta.
- **Quota**: antes de alterar custos-base no registry ou loops de paginação, conferir `docs/storage_strategy.md` e o modelo de créditos (`credits.py`); custo-base por chamada é uma modelagem e pode divergir de mudanças de política da API.
- **ToS**: qualquer mudança que passe a persistir conteúdo (frames, thumbnails baixadas, transcrições de vídeo baixadas) é proibida; se um experimento exigir, leve a decisão ao vault primeiro (`notas/metodologia/multimodalidade-sem-violacao-termos-de-uso.md`).

## Documentação

- Organização por quadrantes Diátaxis (índice em `docs/README.md`): tutorial (pendente), how-to (`usage.md`, `bulk_terms_collection.md`, `channel_collection.md`), referência (`storage_strategy.md`, `tipos_de_erro.md`), explicação (`design.md`, `metodologia_busca_youtube.md`).
- Arquitetura em `docs/architecture/c4.md` (C4 contexto + contêineres, Mermaid).
- **Idioma em aberto**: README e `design.md` em EN; guias em PT-BR. Decidir antes de tocar em docs públicos (ver nota `documentacao-engenharia-referencias-triagem.md` no vault).

## ⚠️ Incidente conhecido: módulo `query_preparation.py` perdido

O `CHANGELOG.md` (Unreleased) documenta `src/youtube_data_pipeline/query_preparation.py`, `tests/test_query_preparation.py` e `docs/news_query_preparation.md` como adicionados, mas **nenhum deles está na árvore e nenhum commit de qualquer branch os contém** (investigado em 2026-10-25). Fatos:

- Os `.py` nunca foram commitados; foram removidos por volta de jul/2026 (provavelmente `git clean`), junto com o teste e o doc.
- **Sobrevivem os `.pyc`** (mar/2026) em `src/youtube_data_pipeline/__pycache__/query_preparation.cpython-312.pyc` e `tests/__pycache__/test_query_preparation.cpython-312-pytest-9.0.2.pyc` — contêm os nomes de todas as funções com docstrings e os 11 testes.
- `notebooks/eda_news_query_preparation.ipynb` importa 5 funções do módulo; o dado de entrada (`data/raw/compiled_full_catalogado_final_sem_duplicatas_por_analysis_text.jsonl`, 315 MB) está intacto.
- **Módulo reconstruído em 2026-10-25** a partir dos `.pyc` (docstrings, assinaturas e bytecode) e do notebook: `src/youtube_data_pipeline/query_preparation.py` e `tests/test_query_preparation.py` recriados — 13 testes passando; saídas validadas contra o notebook (mesmos `query_id`, `title_clean`, `top_entities` e `published_after` no dado real). Pendência: `docs/news_query_preparation.md` não foi recriado.

Antes de qualquer mudança no repo: checar `git status` por arquivos não rastreados; **não rodar `git clean` sem backup**; commit de trabalho em andamento assim que funcional.

## Decisões de engenharia (resumo — detalhes em `docs/design.md`)

- Registry declarativo de endpoints (extensível sem tocar no núcleo do client).
- Cliente único com fluxo validate → execute → save → credit accounting → paginação.
- Persistência run-scoped com `manifest.jsonl` (índice legível por máquina).
- Log estruturado (structlog JSON).
- Política de erro determinística: retry (transitório, backoff exponencial) / stop (auth, validação, quota, forbidden) / skip (recurso inexistente/desabilitado).
- Limite conhecido: custo de crédito modelado por custo-base por chamada (não acompanha mudanças futuras de política).
