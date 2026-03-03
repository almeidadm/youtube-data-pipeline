# Progress
- 2026-03-02: Milestone 1 concluido. Defini escopo, seeds e limite de cota em `config/collection_plan.json`. Seeds estao vazias para preenchimento; limite diario 10000 com soft limit 9000 e flag de parada. Escopo inclui channels, playlistItems e videos; comentarios desativados por padrao.
- 2026-03-02: Milestone 2 concluido. Adicionei `.env.example` com variaveis para API key e OAuth opcional, e documentei o uso em `config/credentials.md`.
- 2026-03-02: Milestone 3 concluido. Implementei o cliente em `src/youtube_client.py` com suporte a `part`/`fields`, API key ou bearer token, e retry/backoff para erros 429/5xx e falhas de conexao.
- 2026-03-02: Milestone 4 concluido. Criei `src/collection_flow.py` com o fluxo `channels.list` -> uploads playlist -> `playlistItems.list` (1 pagina) -> `videos.list`, incluindo extracao de `videoId` e tratamento basico de erros.
- 2026-03-02: Milestone 5 concluido. Adicionei paginacao com `iterate_playlist_items` e fluxo completo em `collect_channel_uploads_all`, incluindo `maxResults`, `pageToken` e limite opcional de paginas; videos sao buscados em lotes.
- 2026-03-02: Milestone 6 concluido. Criei `src/storage.py` com persistencia em SQLite (tabelas `runs`, `channels`, `videos`) e deduplicacao via upsert por `id`; adicionei `YOUTUBE_DB_PATH` ao `.env.example`.
- 2026-03-02: Milestone 7 concluido. Adicionei rastreio de requisicoes com estimativa de quota (`src/quota.py`, `src/tracking.py`), tabela `requests` e logger em `SqliteStore`; `YouTubeClient` agora aceita tracker opcional para registrar parametros e timestamps.
- 2026-03-02: Milestone 8 concluido. Implementei coleta de comentarios via `commentThreads.list` e `comments.list` em `src/collection_flow.py`, com paginacao, limites de paginas e opcao de incluir respostas completas por thread.
- 2026-03-02: Milestone 9 concluido. Adicionei suporte a execucao incremental e exportacao de dados:
  * novos metodos `video_exists`/`existing_video_ids` em `src/storage.py`.
  * funcao `collect_channel_uploads_incremental` em `src/collection_flow.py` que pula videos ja presentes no banco.
  * script CLI `src/cli.py` com comandos `collect` (com flag --incremental) e `export` para despejar tabelas em JSONL.
  * atualizei importacoes e documentacao leve no script.
- 2026-03-02: Milestone 11 concluido. Adicionei comandos/scrips simplificados para consulta de cota:
  * método `quota_summary` em `src/storage.py` que agrega por recurso e opcionalmente filtra por run_id.
  * subcomando `quota` em `src/cli.py` exibindo uso total e por recurso.
  * testes cobrindo funcionalidade de resumo de cota e CLI associado.
  * atualizei README com instruções de uso do comando.
- 2026-03-02: Milestone 10 concluido. Implementei testes mínimos e documentação completa:
  * Criado `tests/test_youtube_client.py` com 20+ testes cobrindo inicialização, headers, URL building, requisições bem-sucedidas, retry em 429/5xx, URLError, e integração com tracker.
  * Criado `tests/test_tracking.py` com testes para `RequestTracker` (logging, múltiplas requisições, busca) e estimativa de quota para todos os recursos.
  * Melhorado `README.md` com seções adicionais: esquema de banco de dados, exemplos práticos, considerações sobre quota, troubleshooting e documentação sobre testes.
  * Corrigido problemas de imports relativos em `src/` para compatibilidade com testes (prefixo `src.` completo).
  * Corrigido problema de `:memory:` database em `storage.py` que tentava criar diretórios.
  * Suite de testes agora com 45 testes validando cliente HTTP, fluxo de coleta, persistência, rastreamento de quota e CLI.
- 2026-03-02: Milestone 12 concluido. Implementei logs de monitoramento humano da aplicação:
  * Criado módulo `src/monitoring.py` com sistema de logging estruturado (console + arquivo com rotação automática).
  * Logging em dois níveis: console (INFO) para feedback em tempo real, arquivo (DEBUG) para auditoria completa.
  * Integração em pontos-chave: `src/cli.py` (início/fim de coleta, canais processados, resumo com vídeos/quota), `src/youtube_client.py` (requisições/retries com backoff), `src/collection_flow.py` (import para expansão futura).
  * Novo comando CLI `logs` listando arquivos de log com nome, tamanho e data de modificação.
  * Testes cobrindo criação de diretório, inicialização com run_id, funções sem exceções, criação e acúmulo de conteúdo em arquivo (9 novos testes, todos passando).
  * Documentação no README seção "Monitoramento e Logs": exemplos de uso, análise com grep, estrutura do formato, níveis de detalhe (console vs arquivo), e boas práticas.
  * Suite de testes ampliada para 54 testes total, todas passando com sucesso (9 novos de monitoramento).
  * Logs persistidos em `data/logs/collection_YYYYMMDD_HHMMSS_run_id.log` com formato legível `[LEVEL] YYYY-MM-DD HH:MM:SS | função:linha | mensagem` para auditoria e troubleshooting.
  * Conformidade com ideais de `instructions.md` mantida: logging não interfere com quota ou coleta de dados, apenas adiciona rastreabilidade de execução.
- 2026-03-02: Milestone 13 concluido. Integrei logging estruturado em `src/collection_flow.py`:
  * Expandido imports do `monitoring.py` para incluir `log_info`, `log_request`, `log_error`.
  * Adicionado logging em funções-chave:
    - `fetch_channel`: Log ao buscar canal com título para rastreabilidade.
    - `iterate_playlist_items`: Log de início de paginação e progresso por página (contagem acumulativa de items).
    - `fetch_videos`: Log ao buscar lotes de vídeos com counts (fetched/total).
    - `iterate_comment_threads`: Log de início de paginação de comentários e contadores por página.
    - `iterate_comments`: Log de paginação de replies com contadores.
    - `collect_channel_uploads_all`: Log de início/fim de coleta com totais (playlist items vs vídeos únicos).
    - `collect_channel_uploads_incremental`: Log de início/fim com contadores de novos vs duplicados (skipped).
    - `collect_video_comments_all`: Log de início/fim de coleta com totais (threads vs replies).
  * Logs informativos (INFO level) para feedback em tempo real (console) e DEBUG level para paginação/detalhe em arquivo.
  * Nenhuma mudança em assinatura de funções ou fluxo de coleta; logging é non-invasivo e compatível com suite existente.
  * Suite de testes completa (54/54 tests) continua passando sem modificações; logging não influencia lógica ou tipos.
  * Rastreabilidade aumentada: cada etapa de coleta (fetch de canal, paginação, busca de vídeos, deduplicação) agora registrada.
  * Alinhado com ideais de `instructions.md`: logging adiciona observabilidade sem afetar quota ou arquitetura de coleta.
- 2026-03-02: Milestone 14 concluido. Implementei métodos para persistência (upsert e dedup) de comentários em banco de dados:
  * Adicionadas tabelas `comment_threads` e `comments` ao schema em `src/storage.py`:
    - `comment_threads`: id (pk), video_id, data_json, collected_at, run_id (acompanha threads por vídeo).
    - `comments`: id (pk), thread_id (fk), data_json, collected_at, run_id (acompanha replies e vinculação com threads).
    - Ambas com campo run_id para rastreamento por execução e integridade referencial.
  * Métodos de upsert implementados:
    - `upsert_comment_thread()`: Insere ou atualiza thread de comentário com deduplicação por id.
    - `upsert_comment()`: Insere ou atualiza comentário/reply com deduplicação por id, vinculado a thread_id.
    - `upsert_many_comment_threads()`: Batch upsert de threads para um vídeo (conveniente para holdup de coleta).
    - `upsert_many_comments()`: Batch upsert de comentários para uma thread (conveniente para reply pagination).
  * Métodos helper para dedup e validação:
    - `comment_thread_exists()`: Verifica existência de uma thread já coletada.
    - `comment_exists()`: Verifica existência de um comentário já coletado.
    - `existing_comment_thread_ids()`: Batch lookup de threads existentes (similar a `existing_video_ids()`).
    - `existing_comment_ids()`: Batch lookup de comentários existentes (similar a `existing_video_ids()`).
  * Função helper `persist_video_comments()` em `src/collection_flow.py`:
    - Toma listas de threads e replies coletadas e as persiste usando store.
    - Vincula replies às suas threads via parentId (dedup semântica).
    - Retorna contadores (threads_persisted, replies_persisted) para logging/validação.
    - Tratamento gracioso de erros: falhas de persistência não bloqueiam coleta (log_error).
    - Suporta orphan replies (replies sem thread no batch atual, common em paginated collection).
  * Testes adicionados com cobertura completa:
    - `test_comment_thread_upsert_and_exists`: Upsert e existence check de threads.
    - `test_comment_upsert_and_exists`: Upsert e existence check de comentários.
    - `test_upsert_many_comment_threads_and_comments`: Batch upsert de threads e comentários.
    - `test_persist_video_comments`: Integração ponta-a-ponta (collect->persist->validate).
  * Suite de testes ampliada para 58 testes; 100% passando (4 novos testes de comentários).
  * Conformidade com `instructions.md` mantida:
    - Persistência não interfere com quota (apenas logging/storage).
    - Esquema segue padrão de channels/videos (JSON, timestamps, run tracking, dedup).
    - Rastreabilidade completa: cada comentário vinculado a execução via run_id.
    - Preparado para integração futura com CLI (persist pode ser chamado após `collect_video_comments_all()`).
- 2026-03-02: Milestone 15 concluido. Implementei pipelines de execução diferente para cada caso de uso principal:
  * Adicionadas 4 pipelines principais em `src/collection_flow.py`:
    - `pipeline_search_channels()`: Busca canais por termo usando search.list (100 quota) seguido de channels.list para metadados completos.
    - `pipeline_search_videos()`: Busca vídeos por termo usando search.list (100 quota) seguido de videos.list para estatísticas completas.
    - `pipeline_channel_videos()`: Refactorização da coleta por canal existente, suportando múltiplos canais, full ou incremental.
    - `pipeline_video_comments()`: Coleta de comentários e replies de um ou mais vídeos com paginação e persistência.
  * Funções helper adicionadas:
    - `fetch_search_page()`: Fetch de uma página de resultados de search com suporte a filtros (order, publishedAfter/Before).
    - `iterate_search_results()`: Iteração sobre múltiplas páginas de search com logaritmo de progresso.
  * Configuração em `config/collection_plan.json`:
    - Campo `pipeline_type` define qual pipeline usar (padrão: "channel_videos").
    - Seeds expandidas: `search_channels_queries`, `search_videos_queries`, `video_ids` além dos `channel_ids` existentes.
    - `pipeline_options`: Seção de referência com descrição e parâmetros padrão de cada pipeline.
    - Mantém retrocompatibilidade com planos antigos (usa padrão channel_videos).
  * CLI em `src/cli.py`:
    - Adicionado argumento `--pipeline-type` para sobrescrever tipo de pipeline do config.
    - Função `do_collect()` refatorada para suportar routing dinâmico de pipeline.
    - Funções privadas `_execute_*_pipeline()` para cada tipo de pipeline com tratamento específico.
    - Cada pipeline mapeia corretamente para suas seeds correspondentes no arquivo de configuração.
  * Testes adicionados (9 novos, total 63):
    - `test_pipeline_search_channels()`: Valida busca por canal e fetch de metadados.
    - `test_pipeline_search_videos()`: Valida busca por vídeo e fetch de metadados.
    - `test_pipeline_channel_videos()`: Valida coleta de vídeos de canal(is).
    - `test_pipeline_video_comments()`: Valida coleta de comentários.
    - `test_iterate_search_results()`: Valida iteração sobre search com paginação.
    - Mock client `SearchDummyClient` para simulação de search/videos/channels/commentThreads.
    - Suite de testes agora 100% passando sem avisos (63/63 testes).
  * Arquitetura e conformidade com `instructions.md`:
    - Cada pipeline segue padrão: fetch -> enriquecer -> persistir (quando aplicável).
    - Logging integrado em cada pipeline (start/finish com contadores).
    - Tratamento gracioso de erros: falhas em um item não bloqueiam pipeline.
    - Custo de quota respeitado: search continua custando 100, outros recursos 1.
    - Separação clara de casos de uso permite futuras otimizações específicas por tipo.
    - Retrocompatibilidade: código cliente antigo continua funcionando (padrão channel_videos).

- 2026-03-02: Milestone 16 concluido. Atualizei README.md com link para seção de docs e criei diretório `docs/` contendo arquivos de documentação: overview, usage, architecture e development; instruções detalhadas e notas para contribuintes.
- 2026-03-02: Milestone 17 concluido. Tornei o projeto mais amigável para uso em Jupyter notebooks:
  * adicionei `src/__init__.py` com versionamento e re-export de helpers para facilitar
    importações interativas (`import src; from src import do_collect`).
  * Criei notebook de exemplo `notebooks/YouTube_API_Collector_Examples.ipynb` que
    demonstra configuração de ambiente, importação de módulos e execução de cole
    e inspeção do banco.
  * Atualizei documentação (`README.md` e `docs/usage.md`) com uma seção explicando
    o uso em notebooks e apontando para o exemplo.
  * Incluí teste `tests/test_package.py` para garantir que a importação de
    `src` e seus símbolos funcione corretamente.
  * Nenhuma alteração afetou o comportamento existente ou a suíte de 63 testes,
    mas agora existem 64 testes no total.
- 2026-03-02: Milestone 18 concluido. Revisei e ampliei a documentação e o README:
  * README atualizado com descrição das pipelines suportadas, detalhes sobre o
    campo `pipeline_type` e `pipeline_options` no plano de sementes, e seção de
    instruções para execução de testes.
  * `docs/usage.md` recebeu explicações sobre pipelines, exemplos de seeds,
    e nova seção "Running Tests".
  * Garantias de que a documentação geral (architecture, development) cobre os
    recursos atuais; nenhum conteúdo contraditório foi encontrado.
  * Todas as alterações são puramente documentais; a suíte de 64 testes continua
    passando sem alterações de código.
```
