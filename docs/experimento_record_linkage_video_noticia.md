# Experimento de record linkage notícia–vídeo

Avaliação crítica do matching implícito em `data/raw/runs/factcheck_unified_q3` e proposta de pipeline multimodal de validação usando endpoints adicionais da YouTube Data API v3.

---

## 1. Introdução e escopo

### 1.1 Motivação

A coleta descrita em `docs/metodologia_busca_youtube.md` parte do princípio de que cada termo de busca — derivado de uma notícia verificada por agência de fact-checking — produzirá, via `search.list`, um conjunto de vídeos *topicamente relacionados* a essa notícia. Na prática, o que a API retorna é o conjunto de vídeos que o **ranking interno do YouTube** considera mais relevantes para a string de busca, combinando correspondência textual com sinais de engajamento, freshness e personalização regional (ver `docs/metodologia_busca_youtube.md`, §2.1).

Esse matching é **implícito**: não há, no retorno da API, nenhum score ou evidência de que o vídeo de fato trate da notícia que gerou o termo. A inspeção qualitativa dos resultados de `factcheck_unified_q3` confirma a presença de ruído — vídeos de comentaristas políticos sensacionalistas aparecem misturados a reportagens jornalísticas factuais em resposta a termos derivados de checagens sobre economia.

### 1.2 Definição do problema

O problema de **record linkage notícia–vídeo** é definido como: dado um par `(record_id, videoId)` produzido pela pipeline de coleta, determinar se o vídeo em questão aborda a mesma alegação factual (`claim_text`) que motivou a checagem identificada por `record_id`.

Formalmente, busca-se uma função de scoring `f: (notícia, vídeo) → [0,1]` e um limiar `τ` tal que `f(n, v) ≥ τ ⇒ match`. Esta formulação é análoga ao problema de **previously fact-checked claim retrieval**, largamente estudado na literatura de *automated fact-checking* (Guo et al., 2022, TACL; Martín et al., 2022, FacTeR-Check).

### 1.3 Delimitação

Este experimento trata de **validação de relevância** (o vídeo fala sobre a notícia?), **não** de detecção de misinformation (o vídeo espalha informação falsa?). As duas tarefas se beneficiam mutuamente — um dataset de pares `(notícia, vídeo)` validados é pré-requisito para análises subsequentes de veracidade — mas são distintas.

---

## 2. Diagnóstico da run `factcheck_unified_q3`

### 2.1 Caracterização

| Aspecto | Valor |
|---|---|
| Vídeos únicos coletados | 4.499 |
| Termos processados | 68 (de 1.567 = 4,3 %) |
| Tier da query | Q3 (TF-IDF top-3 + top-2 entidades NER, filtro -30/+60 dias) |
| Endpoints usados | `search.list` (203 chamadas) e `videos.list` (112 chamadas) |
| Créditos consumidos | ~20.412 unidades |
| Status final | `stopped` (`stop_action_on_search`), retomável via checkpoint |

### 2.2 Tipologia de ruído observada

Amostragem qualitativa de títulos retornados (coletados em 2026-04-19):

- **Jornalismo factual** — p. ex. *"Petroleiros de plataformas e refinarias da Petrobras fazem greve"* (Jornal da Record) em resposta a termo derivado de notícia sobre greve;
- **Comentário político sensacionalista** — p. ex. *"ÓTIMA NOTÍCIA NESSE DOMINGO — Flávio Bolsonaro arrebentando e LULA derretendo"* (canal de deputado federal), retornado para termo sobre política fiscal;
- **Conteúdo tangencialmente relacionado** — análises genéricas que mencionam a entidade-chave mas não a alegação específica.

O ranking do YouTube mistura as três categorias sem discriminação, uma vez que o motor é otimizado para satisfação de busca de usuário final, não para correspondência fática.

### 2.3 Limitações estruturais

1. **Ausência de score de relevância** — a API não expõe nenhum ranking numérico; o único sinal ordinal disponível é a posição `page × index`.
2. **Vínculo `record_id ↔ videoId` não-materializado** — os arquivos de resultado residem em diretórios indexados por hash do termo (`endpoint=search/origin=explicit:<hash>/`), exigindo *post-hoc join* com `factcheck_unified_q3.jsonl` via recomputação de hash ou leitura do `manifest.jsonl`.
3. **`videos.list` com parts restritos** — a coleta atual usa `part=snippet,contentDetails,statistics`, deixando de fora `topicDetails`, `localizations` e `status`.
4. **Endpoints não utilizados** — `commentThreads`, `comments`, `channels`, `captions` estão implementados em `src/youtube_data_pipeline/endpoints/definitions.py` mas não participam da pipeline atual.

### 2.4 Hipótese central

> O ranking implícito do `search.list` é insuficiente como *ground truth* de relevância. Uma função de scoring explícita, alimentada por sinais multimodais extraídos de endpoints complementares, é necessária para produzir pares `(notícia, vídeo)` com qualidade suficiente para análise subsequente.

---

## 3. Revisão bibliográfica

### 3.1 Claim matching e recuperação de checagens prévias

O problema de casar uma alegação com uma checagem já existente é uma tarefa canônica em *automated fact-checking*. Guo, Schlichtkrull & Vlachos (2022, *A Survey on Automated Fact-Checking*, TACL) organizam a literatura em torno de três estágios — detecção de alegação, recuperação de evidência e veredicto — e dedicam atenção específica à *claim matching*. Martín et al. (2022) propõem o **FacTeR-Check**, baseado em XLM-RoBERTa, que combina similaridade semântica multilingue, *natural language inference* e extração automática de keywords para construir queries de busca.

Trabalhos mais recentes como **Claim2Vec** (2026) e **MultiClaimNet** (2025) exploram embeddings contrastivos dedicados ao domínio de fact-checking, superando modelos SBERT genéricos em cenários multilingues.

**Aplicação direta ao nosso problema:** o `claim_text` do dataset de fact-checks e o `title + description + transcript` do vídeo são tratados como duas "alegações" a serem casadas no mesmo espaço de embeddings.

### 3.2 Similaridade semântica texto-vídeo

Sentence-BERT (Reimers & Gurevych, 2019) estabelece a arquitetura bi-encoder Siamese padrão para cálculo eficiente de similaridade textual via cosseno. Em pipelines de retrieval modernos, bi-encoders fornecem o *first-stage ranking* (rápido, indexável via FAISS) e cross-encoders re-ranqueiam o top-K (lento, porém preciso). Essa arquitetura de duas etapas é a referência para fact-checking em escala.

### 3.3 Detecção baseada em transcrição

Ghosh et al. (2023, *Identifying Misinformation on YouTube through Transcript Analysis*, arXiv:2307.12155) mostram que **a tarefa de classificação de vídeo pode ser reduzida a classificação de texto** quando a transcrição oficial ou auto-gerada está disponível. A transcrição é o sinal mais fiel ao conteúdo real do vídeo — muito mais do que título e descrição, que são frequentemente otimizados para CTR e podem ser deliberadamente misleading (ver §3.5).

**Implicação para nosso pipeline:** o endpoint `captions` (ou, em fallback, transcrição via *whisper*/yt-dlp) é o caminho mais direto para acessar o conteúdo semântico do vídeo.

### 3.4 Fusão multimodal

Choi & Ko (2022, *Effective fake news video detection using domain knowledge and multimodal data fusion on YouTube*) combinam BERT para texto, uma CNN modificada para thumbnail, e metadados do YouTube, reportando F1 de até 0,90 sob fusão ótima no **FVC — Fake Video Corpus**. O ACM MM 2023 survey **Awesome-Misinfo-Video-Detection** (ICTMCG) cataloga mais de 60 trabalhos nessa linha.

### 3.5 Misleading metadata

Palod et al. (2019, *Misleading Metadata Detection on YouTube*, ECIR) documentam que título e descrição frequentemente divergem do conteúdo do vídeo — seja por clickbait, seja por manipulação deliberada. Consequentemente, qualquer pipeline que dependa exclusivamente de `snippet.title + snippet.description` subestima essa divergência.

### 3.6 Sinais de audiência nos comentários

O dataset **DIVERSE** (Suresh et al., 2024) anota stance de comentários em vídeos do YouTube. Trabalhos correlatos exploram a **razão de comentários céticos** como sinal de credibilidade: a proporção de comentários contendo termos como *"fake"*, *"hoax"*, *"photoshopped"*, *"mentira"*, *"boato"* funciona como proxy de suspeita coletiva.

**Implicação:** o endpoint `commentThreads` fornece *wisdom-of-the-crowd* gratuita — 1 unidade de quota por chamada, custo marginal frente aos 100 do `search`.

### 3.7 Credibilidade no nível de canal

Avaliações jornalísticas (Tufts Library Guide; literatura de mídia/informação) convergem em que **número de inscritos e badge de verificação não são indicadores confiáveis de credibilidade**. Sinais úteis são: tipo de produtor (veículo jornalístico estabelecido, político, creator independente), histórico de publicação, consistência editorial.

Operacionalmente, `channels.list` com `part=snippet,statistics,topicDetails,brandingSettings` permite derivar features como: data de criação, número de uploads, categoria dominante, país declarado, presença de website oficial — que podem ser combinadas com uma allowlist curada de veículos jornalísticos brasileiros.

### 3.8 Audit studies

Hussein, Juneja & Mitra (2020, *Measuring Misinformation in Video Search Platforms*, ACM HCI) conduzem um audit do `search.list` do YouTube e documentam empiricamente a presença sistemática de misinformation nas SERPs — reforçando a hipótese §2.4 de que o ranking implícito não pode ser tratado como ground truth.

### 3.9 Síntese

A literatura converge em três padrões diretamente transferíveis:

1. **Bi-encoder + cross-encoder** sobre texto (claim ↔ transcrição) como núcleo do scoring;
2. **Fusão tardia** de sinais de canal, comentários e metadados como reforço;
3. **Gold standard anotado** para calibração de threshold e avaliação.

Todos os três padrões pressupõem endpoints que a run `q3` **não coletou**.

---

## 4. Como cada endpoint contribui para o problema

### 4.1 `videos.list` com parts expandidos

A coleta atual usa `part=snippet,contentDetails,statistics`. Propõe-se adicionar:

| Part | Campos relevantes | Uso no linkage |
|---|---|---|
| `topicDetails` | `topicCategories` (URIs de Wikipedia/Freebase) | Filtro grosso por categoria temática; casamento com entidades NER da notícia |
| `localizations` | título/descrição em idiomas adicionais | Robustez a republicações em PT/ES/EN |
| `status` | `uploadStatus`, `privacyStatus`, `embeddable` | Descartar vídeos removidos/privados antes de pipeline pesado |

Custo: mantém 1 unidade por chamada (parts não somam crédito em `videos.list`).

### 4.2 `captions` e fallback de transcrição

O endpoint `captions.list` (50 unidades — ver `definitions.py`) lista faixas de legenda disponíveis. O **download** das legendas via API requer OAuth com autorização do dono do canal, o que é inviável em escala. Alternativas práticas:

- **`youtube-transcript-api`** (biblioteca Python) — acessa legendas públicas (auto-geradas inclusive) sem autenticação;
- **`yt-dlp --write-auto-subs`** — equivalente, mais robusto a edge cases;
- **`whisper` / `faster-whisper`** — transcrição local do áudio como último recurso.

Recomendação: pipeline em cascata — tentar `youtube-transcript-api` primeiro, cair para whisper apenas quando ausente. Ancorar no conteúdo transcrito permite matching semântico profundo (§3.3) em vez de depender apenas do snippet (§3.5).

### 4.3 `commentThreads` e `comments`

Com 1 unidade por chamada e `maxResults=100`, coletar os ~100 comentários top de cada vídeo custa, para os 4.499 vídeos do dataset atual, ~4.500 unidades — menos de 5 % do orçamento da run original.

Features extraíveis:

- **Razão de ceticismo** — fração de comentários contendo léxico de desconfiança (*mentira, fake, boato, falso, clickbait*);
- **Stance distribution** — classificador de stance (support / contradict / neutral) aplicado contra o `claim_text`;
- **Menções a fact-checks** — presença de links para agências de checagem nos próprios comentários (sinal forte de vídeo *já* checado pela comunidade).

### 4.4 `channels.list`

Coletar metadados do canal uma única vez por `channelId` único (ordem de magnitude de centenas, não milhares) com `part=snippet,statistics,topicDetails,brandingSettings`. Features:

- **Tipologia** (via allowlist): `jornalistico_estabelecido` (G1, UOL, Jornal da Record, Folha, Estadão, CNN Brasil) / `agencia_factcheck` (Aos Fatos, AFP, Lupa) / `politico` / `creator_independente` / `desconhecido`;
- **Idade do canal** e `subscriberCount` como features contínuas (sem tratar como ground truth de credibilidade, apenas como feature);
- **`topicCategories`** para consistência temática canal ↔ vídeo.

### 4.5 Priorização por custo

Em ordem crescente de custo por vídeo:

1. `videos.list` expandido — 1/50 unidades (batch);
2. `channels.list` — 1/50 unidades (batch, deduplicado por `channelId`);
3. `commentThreads.list` — 1 unidade por vídeo;
4. `captions` / transcrição — 50 unidades (API) ou 0 unidades + compute local (biblioteca).

Todos cabem dentro do orçamento estabelecido em `docs/metodologia_busca_youtube.md`.

---

## 5. Pipeline experimental proposto

### 5.1 Fase 1 — Enriquecimento

Para cada `videoId` em `factcheck_unified_q3`:

1. Re-chamar `videos.list` com parts estendidos (§4.1);
2. Deduplicar `channelId` e chamar `channels.list` (§4.4);
3. Chamar `commentThreads.list` top-100 por `relevance` (§4.3);
4. Obter transcrição via `youtube-transcript-api` (§4.2);
5. Materializar arquivo de índice `record_id × videoId` resolvendo a limitação §2.3.2.

### 5.2 Fase 2 — Engenharia de features

Três famílias de features, computadas por par `(notícia, vídeo)`:

**Textuais**
- `sim_title` = cos(SBERT(claim_text), SBERT(title))
- `sim_desc` = cos(SBERT(claim_text), SBERT(description))
- `sim_transcript` = cos(SBERT(claim_text), SBERT(transcript))
- `sim_transcript_max_window` = máximo de similaridade sobre janelas de 256 tokens da transcrição
- `entity_overlap` = Jaccard entre entidades NER da notícia e entidades NER da transcrição
- `date_delta` = diferença em dias entre `published_at` da notícia e `publishedAt` do vídeo

**Audiência**
- `skepticism_ratio` (§4.3)
- `stance_contradict_ratio`, `stance_support_ratio`
- `commentCount_log`, `likeCount / viewCount`

**Canal**
- `channel_type` (one-hot)
- `channel_age_days`, `subscriber_count_log`
- `channel_topic_overlap` com categoria temática da notícia

### 5.3 Fase 3 — Scoring

Arquitetura em duas etapas, seguindo §3.2:

1. **Bi-encoder** (SBERT multilíngue, p. ex. `paraphrase-multilingual-mpnet-base-v2`) computa `sim_transcript` como *first-stage score*;
2. **Cross-encoder** (p. ex. `mmarco-mMiniLMv2-L12-H384-v1`) re-ranqueia os top-20 vídeos por notícia;
3. **Classificador de fusão** (Gradient Boosting sobre todas as features da §5.2) produz score final calibrado.

### 5.4 Fase 4 — Calibração de threshold

Isotonic regression sobre scores do classificador usando o gold standard (§6.1), escolhendo `τ` que maximize F1 na validação.

### 5.5 Diagrama de fluxo

```
 record_id (CSV)                 videoId (run q3)
      │                                │
      └─────► factcheck_unified ◄──────┘
                    │
      ┌─────────────┼──────────────┬─────────────┐
      ▼             ▼              ▼             ▼
 videos.list    channels.list  commentThreads  transcript
  (expandido)   (por canal)    (top-100)      (youtube-transcript-api)
      │             │              │             │
      └─────────────┴──────┬───────┴─────────────┘
                           ▼
                   features (§5.2)
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
    bi-encoder       cross-encoder     fusão GBM
                                            │
                                            ▼
                                   score ∈ [0,1] → τ
```

---

## 6. Avaliação

### 6.1 Gold standard

Construção manual de um conjunto anotado com amostragem **estratificada por tier** (Q1/Q2/Q3) e por `dataset_id` da notícia, totalizando ~500 pares `(notícia, vídeo)` rotulados como `match` / `partial` / `no_match`. Dois anotadores independentes, com cálculo de Cohen's κ como acordo inter-anotadores (alvo κ ≥ 0,7).

### 6.2 Métricas

- **Precision@k** (k = 1, 5, 10) — fração de vídeos no top-k que são `match`;
- **Recall** — vídeos `match` recuperados / total de `match` no gold;
- **nDCG@10** — ganho acumulado ponderado por ranking;
- **MRR** — posição média do primeiro `match`;
- **F1** do classificador de fusão.

### 6.3 Ablation

Treinar o classificador da §5.3 removendo, uma por vez, cada família de features da §5.2 para quantificar a contribuição marginal de cada endpoint.

---

## 7. Limitações e riscos

1. **Quota** — enriquecer os 4.499 vídeos atuais é barato; escalar para os 1.577 × ~100 vídeos do dataset completo (~158k vídeos) exige planejamento de múltiplos dias, análogo ao cronograma da `metodologia_busca_youtube.md` §2.3.
2. **Indisponibilidade de transcrição** — nem todo vídeo brasileiro tem legenda, e comentários podem estar desabilitados. O pipeline precisa degradar graciosamente quando sinais faltam.
3. **Viés de modelo em PT-BR** — SBERT multilíngues têm qualidade inferior em PT-BR em comparação com EN. Avaliar alternativas como `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` *vs.* modelos PT-específicos (p. ex. BERTimbau fine-tuned).
4. **Circularidade** — usar sinais do YouTube (canal, comentários) para validar busca do YouTube introduz correlações indesejadas entre features e o próprio ranking implícito. Mitigar via features independentes (transcrição, entidades NER).
5. **Comentários moderados ou desabilitados** — veículos jornalísticos frequentemente desabilitam comentários, o que enviesa o sinal de ceticismo contra exatamente a categoria mais confiável.

---

## 8. Próximos passos

1. Implementar Fase 1 (enriquecimento) sobre `factcheck_unified_q3` antes de retomar o processamento dos 1.499 termos restantes;
2. Anotar gold standard inicial (~100 pares) para calibração preliminar;
3. Rodar Fase 2 + Fase 3 baseline (bi-encoder apenas) e reportar Precision@10;
4. Iterar: adicionar features de canal, depois de comentários, depois de transcrição, medindo o ganho incremental de cada endpoint.

---

## 9. Referências bibliográficas

- Choi, H. & Ko, Y. (2022). *Effective fake news video detection using domain knowledge and multimodal data fusion on YouTube*. Pattern Recognition Letters. https://www.sciencedirect.com/science/article/abs/pii/S0167865522000071
- Ghosh, S. et al. (2023). *Identifying Misinformation on YouTube through Transcript Analysis*. arXiv:2307.12155. https://arxiv.org/pdf/2307.12155
- Guo, Z., Schlichtkrull, M. & Vlachos, A. (2022). *A Survey on Automated Fact-Checking*. TACL. https://direct.mit.edu/tacl/article/doi/10.1162/tacl_a_00454/109469
- Hussein, E., Juneja, P. & Mitra, T. (2020). *Measuring Misinformation in Video Search Platforms: An Audit Study on YouTube*. ACM HCI. https://dl.acm.org/doi/10.1145/3392854
- ICTMCG (2023). *Awesome-Misinfo-Video-Detection* (ACM MM survey companion). https://github.com/ICTMCG/Awesome-Misinfo-Video-Detection
- Martín, A. et al. (2022). *FacTeR-Check: Semi-automated fact-checking through semantic similarity and natural language inference*. Knowledge-Based Systems. https://www.sciencedirect.com/science/article/pii/S0950705122006323
- Palod, P. et al. (2019). *Misleading Metadata Detection on YouTube*. ECIR. https://cse.iitkgp.ac.in/~pawang/papers/ecir19_youtube.pdf
- Papadopoulou, O. et al. (2021). *Misinformation Detection on YouTube Using Video Captions*. arXiv:2107.00941. https://arxiv.org/abs/2107.00941
- Reimers, N. & Gurevych, I. (2019). *Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks*. EMNLP.
- Soleimani, A., Monz, C. & Worring, M. (2020). *BERT for Evidence Retrieval and Claim Verification*. ECIR. https://arxiv.org/abs/1910.02655
- Suresh, S. et al. (2024). *DIVERSE: A Dataset of YouTube Video Comment Stances with a Data Programming Model*. arXiv:2403.03334. https://arxiv.org/html/2403.03334v2
