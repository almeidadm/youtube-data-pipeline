# Avaliação do guia da YouTube Data API v3 para desenvolvedores

## Objetivo da avaliação

Esta avaliação traduz os principais pontos do guia da API para um desenho de biblioteca voltada a coleta de dados para pesquisa em fake news financeira multimodal em português.

## Pontos críticos do guia e implicações de arquitetura

1. **Sistema de cotas por crédito**
   - Cada método possui custo em créditos.
   - `search.list` tem custo elevado (100 créditos).
   - Endpoints de leitura como `videos.list`, `channels.list`, `commentThreads.list` e `comments.list` tendem a custo baixo (1 crédito).
   - **Implicação**: o cliente precisa registrar custo por chamada e acumulado por sessão para gestão orçamentária.

2. **Paginação com `nextPageToken`**
   - Coletas amplas exigem múltiplas chamadas por endpoint.
   - **Implicação**: o cliente deve suportar `max_pages` e persistir respostas por página.

3. **Parâmetros obrigatórios por endpoint**
   - Ex.: `part` é obrigatório na maioria dos métodos.
   - **Implicação**: validação declarativa de parâmetros por endpoint para reduzir erros em notebooks e jobs.

4. **Respostas heterogêneas e com metadados ricos**
   - Snippets, statistics, content details, captions e comentários têm estruturas diferentes.
   - **Implicação**: persistir payload bruto em JSON para manter rastreabilidade e permitir feature engineering posterior.

5. **Uso em ambientes de pesquisa e engenharia de dados**
   - Execução interativa (Jupyter) e agendada (batch).
   - **Implicação**: API Python simples (`fetch`) + mecanismo de orquestração recorrente reutilizável.

## Endpoints priorizados no projeto

- `search.list`
- `videos.list`
- `comments.list`
- `channels.list`
- `captions.list`
- `commentThreads.list`

## Recomendação para o cenário da pesquisa

Para o projeto de fake news financeira multimodal, recomenda-se:

- usar `search` para descoberta inicial de vídeos financeiros em português;
- enriquecer com `videos` + `channels` para metadados e contexto de produção;
- usar `captions` para insumo textual alinhado ao conteúdo audiovisual;
- usar `commentThreads` e `comments` para sinais de engajamento, percepção e possível contestação comunitária.
