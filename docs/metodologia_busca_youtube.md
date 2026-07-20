# Metodologia de busca de notícias no YouTube

## 1. Visão geral do dataset

O dataset `predictions_s2_factcheck_scrape_unified.csv` contém **45.670 registros** de fact-checking provenientes de 13 agências brasileiras e internacionais (Boatos.org, Estadão Verifica, Aos Fatos, AFP Checamos, G1, entre outras). O dataset possui uma coluna `label` com dois valores: `outros` (43.705) e `mercado` (1.965).

**Escopo desta metodologia**: Apenas os registros classificados como `label == "mercado"`, que representam notícias relacionadas ao mercado financeiro, economia e consumo. Após filtragem adicional de registros com `claim_text` acima de 20 caracteres e `standard_label != missing`, restam **1.577 registros utilizáveis**, dos quais **1.554 possuem data de publicação**. As 23 notícias sem data terão tratamento específico descrito na seção 4.

Distribuição por `standard_label` no subconjunto "mercado":

| Label | Registros |
|-------|-----------|
| `false` | 1.122 |
| `misleading` | 351 |
| `true` | 82 |
| `other` | 19 |
| `unverified` | 2 |
| `satire` | 1 |

Principais fontes (dataset_id): Boatos.org (482), Estadão Verifica (419), Aos Fatos (272), AFP Checamos (195), G1 Fato ou Fake (139).

Colunas relevantes para a construção de queries:

| Coluna | Descrição | Tamanho mediano |
|--------|-----------|-----------------|
| `claim_text` | Texto original da alegação | ~72 caracteres |
| `title` | Título da checagem | ~13 palavras |
| `text_without_stopwords` | Texto sem stopwords (título + claim + body) | ~28 palavras |
| `lemmatized_text` | Texto lematizado com stopwords | ~258 caracteres |
| `entities` | Entidades nomeadas (LOC, ORG, PER) extraídas via NER | variável |
| `published_at` | Data de publicação da checagem | — |

## 2. Comportamento da YouTube Data API v3 — Search

### 2.1 Características do motor de busca

O endpoint `search.list` do YouTube **não é um mecanismo de busca por palavras-chave puro** — ele é um sistema de *information retrieval* projetado para queries em linguagem natural similar ao que um usuário digitaria na barra de busca. Internamente, o YouTube aplica:

- **Stemming/normalização** própria ao texto da query
- **Expansão semântica** implícita (sinônimos, termos relacionados)
- **Personalização** baseada em região (`regionCode`) e idioma (`relevanceLanguage`)
- **Ranqueamento** por relevância combinando correspondência textual com sinais de engajamento (views, CTR, freshness)

Isso tem consequências diretas para a construção de queries:

1. **Queries em linguagem natural tendem a performar melhor** do que listas de keywords desconectadas, pois o algoritmo foi otimizado para o primeiro caso.
2. **Queries muito longas (>10 palavras) degradam a precisão** — o algoritmo pondera todos os termos e tende a retornar resultados genéricos que satisfazem parcialmente muitos termos em vez de resultados específicos.
3. **Lematização manual pode ser contraproducente** — enviar "fornecer" em vez de "forneceu" pode confundir o matching, já que o YouTube já aplica normalização interna. A forma conjugada/flexionada muitas vezes carrega informação contextual útil para o ranking.
4. **Termos raros/específicos são muito eficazes** — nomes próprios, números, siglas e termos técnicos produzem resultados mais precisos do que termos genéricos.
5. **O parâmetro `q` não suporta operadores booleanos** (AND, OR, NOT) formalmente. Aspas (`"termo exato"`) forçam correspondência exata mas são muito restritivas.

### 2.2 Parâmetros de filtragem disponíveis

| Parâmetro | Descrição | Impacto no retrieval |
|-----------|-----------|---------------------|
| `q` | Texto da query (linguagem natural) | Determina o matching textual |
| `publishedAfter` | Data mínima (RFC 3339) | Restringe universo temporal |
| `publishedBefore` | Data máxima (RFC 3339) | Restringe universo temporal |
| `regionCode` | Código de região (ex: "BR") | Prioriza conteúdo regional |
| `relevanceLanguage` | Idioma de relevância (ex: "pt") | Pondera conteúdo no idioma |
| `type` | Tipo de recurso ("video") | Filtra apenas vídeos |
| `order` | Critério de ordenação | "relevance" (padrão), "date", "viewCount" |
| `maxResults` | Resultados por página (1–50) | Volume de resultados |

### 2.3 Custos e limitações de cota

| Item | Valor |
|------|-------|
| Custo por chamada `search.list` | **100 unidades** |
| Cota diária gratuita | **10.000 unidades** (100 buscas/dia) |
| Custo por `videos.list` (detalhes) | **1 unidade** |
| Limite de `maxResults` | 50 por página |

**Projeção de custo para o subconjunto "mercado" (1.577 notícias):**

| Cenário | Chamadas de busca | Unidades | Dias (110.000/dia) |
|---------|-------------------|----------|--------------------|
| 1.577 notícias × 1 query | 1.577 | 157.700 | ~2 |
| 1.577 notícias × 3 queries | 4.731 | 473.100 | **~5** |
| 1.577 notícias × 3 queries × 2 páginas | 9.462 | 946.200 | ~9 |

Com uma cota diária de **110.000 unidades**, a coleta completa com 3 queries e 1 página por query é viável em **aproximadamente 5 dias**. A margem restante em cada dia (~3.000–4.000 unidades) comporta as chamadas complementares de `videos.list` para enriquecimento de metadados (~1 unidade por batch de 50 IDs, impacto marginal).

Essa escala viabiliza o uso das 3 combinações sem necessidade de priorização adicional ou aumento de cota. Cada query deve retornar apenas **1 página** (50 resultados) para manter esse cronograma.

## 3. Estratégia de pré-processamento de texto

### 3.1 Pipeline NLP proposto

O dataset já contém as colunas `text_without_stopwords` e `lemmatized_text` pré-computadas. Para a extração de termos-chave via TF-IDF, propõe-se o seguinte pipeline:

```
claim_text (original)
    │
    ├──> text_without_stopwords (já disponível no dataset)
    │        │
    │        └──> Vetorização TF-IDF sobre corpus de claims
    │                 │
    │                 └──> Top-K termos por documento (K = 3–5)
    │
    └──> entities (já extraídas via NER)
             │
             └──> Nomes próprios, locais, organizações
```

### 3.2 Papel de cada transformação

**Remoção de stopwords** — Útil como etapa intermediária para TF-IDF e para encurtar queries. O campo `text_without_stopwords` do dataset cumpre esse papel. Porém, **não deve ser usado diretamente como query**, pois a lista de palavras desconectadas (ex: "urânio alegar falsamente brasil forneceu material irã") é menos eficaz no YouTube do que a formulação natural (ex: "Brasil forneceu urânio ao Irã").

**Lematização** — O campo `lemmatized_text` converte formas flexionadas para a forma canônica ("forneceu" → "fornecer", "ampolas" → "ampola"). **Recomendamos não utilizar o texto lematizado diretamente nas queries da API**, pois:
- O YouTube já normaliza internamente
- Formas lematizadas em português são frequentemente infinitivos verbais, que soam artificiais e podem divergir dos títulos reais de vídeos
- A perda da flexão remove contexto temporal ("forneceu" vs "fornecer" vs "fornecerá")

O texto lematizado **é útil internamente** na etapa de TF-IDF, pois agrupa variantes morfológicas sob um mesmo token, melhorando o cálculo de frequência.

**TF-IDF** — Técnica central para identificar os termos mais discriminativos de cada notícia. Ao calcular TF-IDF sobre o corpus completo de `text_without_stopwords` (ou `lemmatized_text`), os termos com alta pontuação serão aqueles que são frequentes na notícia específica mas raros no corpus geral — tipicamente nomes próprios, termos técnicos, números e topônimos. Estes são precisamente os termos mais eficazes para buscas no YouTube.

**Entidades nomeadas (NER)** — O campo `entities` contém entidades já extraídas (LOC, ORG, PER). Entidades nomeadas são naturalmente discriminativas e devem ser incorporadas nas queries de alta precisão.

### 3.3 Extração de termos TF-IDF

Recomendação de implementação:

1. **Corpus**: usar `text_without_stopwords` (sem stopwords mas sem lematizar, preservando formas naturais)
2. **Vetorizador**: `TfidfVectorizer` do scikit-learn com `max_df=0.3` (ignorar termos presentes em >30% dos documentos), `min_df=3` (ignorar termos que aparecem em menos de 3 documentos)
3. **Extração**: para cada documento, selecionar os **top 5 termos** com maior score TF-IDF
4. **Filtragem**: remover termos com menos de 3 caracteres e tokens numéricos puros
5. **Enriquecimento**: cruzar com entidades NER — entidades nomeadas presentes no top TF-IDF recebem prioridade na composição da query

## 4. Definição das 3 combinações de busca

Cada notícia será buscada **exatamente 3 vezes** na API, com graus crescentes de especificidade. A lógica é ir de alta cobertura (recall) a alta precisão, garantindo que vídeos relevantes sejam capturados em pelo menos uma das passadas.

---

### Query 1 — Linguagem natural, sem filtro de data (Alta Cobertura)

**Objetivo**: Maximizar recall capturando vídeos publicados em qualquer período que tratem do tema.

| Aspecto | Valor |
|---------|-------|
| Texto da query | `claim_text` original, truncado em 100 caracteres no último espaço |
| Filtro de data | Nenhum |
| `regionCode` | `"BR"` |
| `relevanceLanguage` | `"pt"` |
| `order` | `"relevance"` |
| `maxResults` | `50` |
| Páginas | 1 |

**Justificativa**: O `claim_text` mediano tem ~72 caracteres, que está abaixo do limiar de degradação de performance (~100–128 caracteres). Usar o texto original em linguagem natural aproveita a capacidade do YouTube de matching semântico. A ausência de filtro temporal garante que vídeos antigos ou tardios sobre o tema sejam capturados — muitas desinformações ressurgem ciclicamente.

**Truncamento**: para claims com mais de 100 caracteres (~10% do dataset), truncar no último espaço antes de 100 caracteres. Isso evita cortar palavras pela metade e mantém a query dentro da faixa de eficácia do motor de busca.

**Exemplo**:
```
Notícia: "Brasil forneceu ao Irã ampolas de urânio que desapareceram em 2023"
Query:   "Brasil forneceu ao Irã ampolas de urânio que desapareceram em 2023"
Filtro:  Nenhum
```

---

### Query 2 — Termos-chave sem stopwords + entidades, filtro de data largo (Cobertura Balanceada)

**Objetivo**: Reduzir ruído da Query 1 focando nos termos substantivos, com uma janela temporal que capture o ciclo de vida típico de uma notícia.

| Aspecto | Valor |
|---------|-------|
| Texto da query | Top 5–7 termos de `text_without_stopwords` (priorizando entidades NER) |
| Filtro de data | `publishedAfter`: `published_at - 180 dias`; `publishedBefore`: `published_at + 180 dias` |
| `regionCode` | `"BR"` |
| `relevanceLanguage` | `"pt"` |
| `order` | `"relevance"` |
| `maxResults` | `50` |
| Páginas | 1 |

**Construção da query**:
1. Extrair as entidades nomeadas do campo `entities` (máximo 3 entidades)
2. Selecionar os primeiros 5 termos substantivos do campo `text_without_stopwords` que **não sejam** entidades já incluídas
3. Concatenar: `[entidades] + [termos substantivos]`, limitando a **7 tokens** no total
4. Unir com espaço simples

**Justificativa do filtro temporal**: A janela de ±180 dias (6 meses) é suficientemente larga para capturar: (a) vídeos publicados antes da checagem que geraram a desinformação; (b) vídeos de cobertura jornalística que vieram depois; (c) vídeos de outras ondas de viralização do mesmo tema. Seis meses é conservador — a maioria dos vídeos relevantes estará dentro de ±90 dias, mas a janela maior protege contra ciclos longos.

**Justificativa do texto**: Ao remover stopwords e limitar a 7 tokens, a query fica concisa e focada nos conceitos-chave. A priorização de entidades NER garante que nomes próprios — que são os termos mais discriminativos no motor de busca — estejam sempre presentes.

**Exemplo**:
```
Notícia: "Brasil forneceu ao Irã ampolas de urânio que desapareceram em 2023"
Entidades: ["brasil", "irã"]
Termos sem stopwords: ["forneceu", "ampolas", "urânio", "desapareceram", "2023"]
Query: "brasil irã forneceu ampolas urânio desapareceram 2023"
Filtro: publishedAfter=2022-09-04, publishedBefore=2023-09-04
```

**Tratamento de notícias sem data**: Para os 23 registros utilizáveis sem `published_at`, a Query 2 será executada sem filtro temporal (degrada para um comportamento similar à Query 1, mas com texto diferente).

---

### Query 3 — Termos raros TF-IDF + entidades, filtro de data justo (Alta Precisão)

**Objetivo**: Maximizar precisão usando apenas os termos mais discriminativos de cada notícia, com janela temporal estreita.

| Aspecto | Valor |
|---------|-------|
| Texto da query | Top 3 termos TF-IDF + top 2 entidades NER |
| Filtro de data | `publishedAfter`: `published_at - 30 dias`; `publishedBefore`: `published_at + 60 dias` |
| `regionCode` | `"BR"` |
| `relevanceLanguage` | `"pt"` |
| `order` | `"relevance"` |
| `maxResults` | `50` |
| Páginas | 1 |

**Construção da query**:
1. Computar TF-IDF sobre o corpus de `text_without_stopwords`
2. Para cada notícia, selecionar os **top 3 termos TF-IDF** (excluindo tokens < 3 caracteres e números puros)
3. Adicionar as **top 2 entidades NER** (priorizando PER > LOC > ORG)
4. Concatenar com espaço simples, total de **3 a 5 tokens**

**Justificativa do filtro temporal**: A janela assimétrica de -30 / +60 dias reflete o ciclo real de desinformação:
- **-30 dias**: vídeos que podem ter originado ou amplificado a desinformação antes da checagem
- **+60 dias**: vídeos de fact-checking, cobertura jornalística e reações que tipicamente surgem nas semanas seguintes à viralização

A janela estreita elimina vídeos não relacionados que compartilham os mesmos termos-chave em contextos diferentes, reduzindo falsos positivos.

**Justificativa do texto**: Termos TF-IDF altos são, por definição, raros no corpus e frequentes no documento — são os termos que melhor distinguem uma notícia de todas as outras. Com apenas 3–5 tokens, a query é extremamente focada. Isso é complementar à Query 1 (linguagem natural ampla) e à Query 2 (termos substantivos moderados).

**Exemplo**:
```
Notícia: "Brasil forneceu ao Irã ampolas de urânio que desapareceram em 2023"
Top TF-IDF: ["urânio", "ampolas", "enriquecido"]
Top Entidades: ["irã", "brasil"]
Query: "urânio ampolas enriquecido irã brasil"
Filtro: publishedAfter=2023-02-02, publishedBefore=2023-05-04
```

## 5. Resumo comparativo das 3 queries

| Dimensão | Query 1 | Query 2 | Query 3 |
|----------|---------|---------|---------|
| **Objetivo** | Recall máximo | Recall/Precisão balanceado | Precisão máxima |
| **Fonte do texto** | `claim_text` (original) | `text_without_stopwords` + `entities` | TF-IDF top-k + `entities` |
| **N. de tokens** | ~10–15 (natural) | 5–7 (curado) | 3–5 (discriminativo) |
| **Filtro de data** | Nenhum | ±180 dias | -30 / +60 dias |
| **Lematização** | Não | Não | Não (TF-IDF calculado sobre texto sem stopwords, não lematizado) |
| **Uso de NER** | Não | Sim (até 3 entidades) | Sim (até 2 entidades) |
| **Resultado esperado** | Amplo, com mais ruído | Moderado, equilibrado | Poucos resultados, alta relevância |

## 6. Decisões de projeto e justificativas

### 6.1 Por que não usar texto lematizado nas queries

Embora a lematização seja uma técnica padrão de NLP, ela **não é adequada como transformação final do texto da query** para a YouTube API por três razões:

1. **Redundância**: O YouTube já aplica normalização morfológica internamente. Enviar texto pré-lematizado adiciona uma camada de transformação que pode divergir da normalização do YouTube.
2. **Artificialidade**: Texto lematizado em português produz sequências pouco naturais ("fornecer o irã ampola de urânio desaparecer") que não correspondem ao tipo de query para o qual o algoritmo foi otimizado.
3. **Perda de sinal**: Formas flexionadas carregam informação temporal e aspectual ("forneceu" vs "fornecerá") que pode ser relevante para o ranking de relevância.

A lematização **é utilizada indiretamente** como passo do pipeline TF-IDF (para agrupar variantes), mas o texto final da query usa formas originais/flexionadas.

### 6.2 Por que TF-IDF sobre `text_without_stopwords` e não sobre `lemmatized_text`

Calcular TF-IDF sobre `text_without_stopwords` (em vez de `lemmatized_text`) garante que os termos extraídos estejam na forma natural do texto — a mesma forma que aparece em títulos e descrições de vídeos no YouTube. Se TF-IDF fosse calculado sobre texto lematizado, o termo extraído seria "fornecer" quando o termo eficaz para busca seria "forneceu".

**Alternativa aceitável**: Calcular TF-IDF sobre `lemmatized_text` para ranqueamento, mas mapear de volta para a forma original em `text_without_stopwords` na hora de montar a query. Isso agruparia melhor variantes ("forneceu"/"fornecido") no cálculo de frequência. Se a implementação justificar a complexidade adicional, essa abordagem pode ser explorada.

### 6.3 Por que não usar mais de 3 queries por notícia

O custo marginal de cada query adicional é **100 unidades de cota** por notícia. Com 1.577 notícias:

- 3 queries: 473.100 unidades (~5 dias)
- 4 queries: 630.800 unidades (~6 dias)
- 5 queries: 788.500 unidades (~8 dias)

Embora o custo absoluto seja administrável nesta escala, há retornos decrescentes: as 3 combinações propostas cobrem o espectro de recall–precisão de forma suficientemente granular. Uma quarta query (ex: apenas entidades NER, ou texto lematizado) teria alta sobreposição com as queries existentes sem ganho proporcional de cobertura.

### 6.4 Por que 1 página por query (e não múltiplas)

Cada página adicional custa 100 unidades. A primeira página (50 resultados) captura os vídeos mais relevantes segundo o ranking do YouTube. Páginas subsequentes têm relevância decrescente e maior proporção de ruído. Com 3 queries × 1 página, cada notícia já terá até **150 vídeos candidatos** (antes da deduplicação), o que é um volume generoso para análise.

### 6.5 Filtro temporal assimétrico na Query 3

A janela de -30/+60 dias é intencionalmente assimétrica porque a checagem (`published_at`) é tipicamente publicada **após** a viralização da notícia. Assim:

- O conteúdo original/desinformação tende a preceder a checagem (capturado por -30 dias)
- A cobertura midiática e reações tendem a seguir a checagem (capturado por +60 dias)
- +60 dias (em vez de +30) dá margem para vídeos de acompanhamento mais tardios

## 7. Considerações de implementação

### 7.1 Integração com o pipeline existente

O projeto já possui um script resumável (`scripts/collect_videos_from_terms_resumable.py`) que aceita um arquivo de termos e executa buscas paginadas. A implementação da nova metodologia requer:

1. **Script de pré-processamento** que leia o CSV, compute TF-IDF, e gere 3 arquivos de termos (um por query tier) no formato esperado pelo coletor
2. **Extensão do endpoint `search`** para incluir `publishedBefore` e `relevanceLanguage` nos `optional_params` do `EndpointDefinition`
3. **Metadados de origin** que identifiquem qual tier de query gerou cada resultado, para posterior análise de eficácia

### 7.2 Execução por lotes

Com 1.577 notícias e cota de 110.000 unidades/dia, a coleta completa é viável em ~5 dias. Recomenda-se:

- Executar cada tier de query como um `run_id` separado (ex: `run_q1_natural`, `run_q2_balanced`, `run_q3_tfidf`)
- Utilizar o sistema de checkpoint existente para retomada após interrupções
- Monitorar o consumo de cota via `CreditMonitor` já implementado
- Cada tier consome ~157.700 unidades (~1,5 dias), permitindo executar ~2 tiers por dia com folga para `videos.list`

### 7.3 Métricas de avaliação posterior

Para avaliar a eficácia de cada tier de query, registrar para análise posterior:

- **Yield**: número médio de resultados por query (das 3 combinações)
- **Overlap**: proporção de vídeos capturados por mais de uma query (indica redundância)
- **Unique recall**: vídeos encontrados exclusivamente por cada tier (indica valor marginal de cada abordagem)
- **Precisão estimada**: amostragem manual de relevância dos resultados por tier

Essas métricas informarão se futuras coletas devem manter as 3 queries ou reduzir para 2 (caso uma query tenha alto overlap com as outras sem contribuição única significativa).
