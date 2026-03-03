# Guia de Desenvolvimento Assistido por LLM
## Integração YouTube Data API v3 para coleta de dados de pesquisa (MVP com cota gratuita)

### 1) Objetivo do MVP
Construir uma integração robusta e econômica (em quota) para coletar dados públicos do YouTube com foco em pesquisa. O MVP deve:
- Ingerir canais e vídeos a partir de seeds confiáveis (IDs de canais, playlists, termos de busca controlados).
- Coletar metadados e estatísticas de canais e vídeos.
- (Opcional) Coletar comentários de vídeos com controle de custo.
- Persistir dados com rastreabilidade de coleta (quando, como e com quais parâmetros).
- Operar dentro da cota diária padrão (10.000 unidades/dia) sem depender de expansão de quota.

### 2) Princípios essenciais (cota e desempenho)
- Toda requisição custa quota, mesmo se inválida; a cota padrão é 10.000 unidades/dia.
- Custo por operação: leitura de listas tende a custar 1 unidade; `search` custa 100 unidades; operações de escrita custam muito mais (50+).
- Cada página adicional (paginação) é uma nova requisição e consome quota.
- Use `part` e `fields` para reduzir payload e custo operacional (CPU/banda). `part` é obrigatório e `fields` filtra propriedades internas.

### 3) Autenticação
- Requisições públicas: use API key.
- Dados privados / ações de escrita: use OAuth 2.0 e os escopos adequados.
- Todas as requisições devem enviar API key ou OAuth.

### 3.1) Segurança de credenciais
- Não embuta a API key no código-fonte; use variáveis de ambiente ou armazenamento seguro.
- Aplique restrições à API key (por API e por origem) para reduzir risco de uso indevido.

### 4) Estratégia de coleta de baixo custo (evitar `search.list` quando possível)
- Preferir IDs de canal e playlists já conhecidos (seeds). O `search.list` custa 100 unidades e deve ser usado só para descoberta pontual.
- Nem todo canal tem username; use `channelId` como identificador principal sempre que possível.
- Descoberta de canais (se necessário):
  - Se tiver username legado, use `channels.list(part="id", forUsername="...")`.
  - Se só tiver o nome do canal, use `search.list(part="snippet", type="channel", q="...")`, sabendo que isso é caro.
- Coleta de vídeos por canal (estratégia recomendada):
  1) `channels.list(part=contentDetails, snippet, statistics, ...)` e ler `contentDetails.relatedPlaylists.uploads` para obter o playlist de uploads.
  2) `playlistItems.list(part=contentDetails,snippet, playlistId=uploads)` para listar vídeos do canal com custo 1.
  3) `videos.list(part=snippet,contentDetails,statistics,...)` para enriquecer os vídeos.
- Comentários:
  - `commentThreads.list` retorna threads e pode incluir algumas respostas; custo 1.
  - Para todas as respostas de um comentário, usar `comments.list(parentId=...)`.

### 5) Regras práticas de paginação e filtros
- Use `maxResults` no limite permitido (ex.: 50 em `search.list` e `playlistItems.list`) para reduzir número de chamadas.
- Use `pageToken`/`nextPageToken` para paginação controlada.
- Para recortes temporais em `search.list`, use `publishedAfter` e `publishedBefore`.

### 6) Dados mínimos do MVP (escopo recomendado)
- Canais: `id`, `snippet` (título/descrição), `statistics` (inscritos, views), `contentDetails` (uploads playlist).
- Vídeos: `id`, `snippet`, `contentDetails`, `statistics`.
- PlaylistItems (uploads): `snippet.resourceId.videoId`, `contentDetails.videoId`, `videoPublishedAt`.
- Comentários (se no escopo): `commentThreads` e, quando necessário, `comments` para replies completos.

### 7) MVP: fluxos de coleta (ordem recomendada)
1. Seed de canais/queries controladas.
2. Resolver IDs de canal (se necessário) com `channels.list` ou `search.list` pontual.
3. Coletar canais (`channels.list`).
4. Coletar uploads playlist de cada canal (`playlistItems.list`).
5. Enriquecer vídeos (`videos.list`).
6. Comentários (opcional): `commentThreads.list` e, se necessário, `comments.list`.
7. Persistir e versionar os snapshots (por data de coleta).

### 8) Requisitos de qualidade e rastreabilidade
- Registrar parâmetros usados em cada requisição (`part`, `fields`, filtros, `pageToken`).
- Salvar timestamps de coleta e IDs de execução para reprocessamento/replay.
- Fazer deduplicação por `id` de recursos.
- Respeitar limites de cota diária; interromper a coleta se ultrapassar a projeção.

### 9) Definição de pronto (DoD) do MVP
- Configuração funcional de credenciais (API key) e execução end-to-end para um conjunto de canais.
- Pipeline coleta canais + vídeos e persiste dados com rastreabilidade.
- Coleta de comentários funcionando para um subconjunto de vídeos (opcional), com custo controlado.
- Relatório simples de uso de quota por tipo de chamada (estimado a partir dos custos conhecidos).

### 10) Observações finais para o LLM
- `search.list` retorna resultados que identificam vídeos, canais e playlists; use os IDs retornados para buscar recursos completos quando necessário.
- Sempre minimize `part` e use `fields` quando possível para reduzir payload.
- Requisições inválidas também custam quota; valide parâmetros antes de enviar.
