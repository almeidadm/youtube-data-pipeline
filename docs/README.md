# Documentation index

Documentation for `youtube-api` (library `youtube_data_pipeline`) is organized
around the four Diátaxis quadrants: **tutorials**, **how-to guides**,
**reference** and **explanation**. Each document belongs to a single quadrant;
if a document starts mixing them, split it.

## Map of existing documents

| Quadrant | Documents | Status |
|---|---|---|
| Tutorial | *(missing)* | TODO — add `docs/tutorials/first-collection.md` (5-minute end-to-end) |
| How-to | `usage.md`, `bulk_terms_collection.md`, `channel_collection.md`, `comando_collect_videos_from_terms.md` | done (PT-BR) |
| Reference | `storage_strategy.md`, `tipos_de_erro.md` | done (PT-BR) |
| Explanation | `design.md`, `metodologia_busca_youtube.md`, `experimento_record_linkage_video_noticia.md`, `youtube_researcher_program_quota_plan_20261118.md` | done (PT-BR) |
| Architecture | `architecture/c4.md` | skeleton (C4 context + container) |
| Operational | [`../CLAUDE.md`](../CLAUDE.md) (ToS/quota guardrails, workflows, incident notes) | done (PT-BR) |

## Gaps (skeleton TODOs)

- [ ] **Tutorial** — first collection from a terms file, end to end, with
      expected outputs.
- [ ] **C4 diagrams** — fill descriptions with real behavior (see
      `architecture/c4.md` TODOs).
- [ ] **Architecture decision records** — register decisions with alternatives
      and evidence (see `design.md` "Design decisions"); link to the
      dissertation's RDPs where a decision is methodological.
- [ ] **Language policy** — decide whether public docs stay PT-BR or move to EN
      (README and `design.md` are already EN; guides are PT-BR).
- [ ] **News query preparation** — `docs/news_query_preparation.md` is still
      missing; the module `query_preparation.py` and its tests were restored
      (see `../CLAUDE.md`, "Incidente conhecido").

## Conventions

- Docs are versioned with the code (docs-as-code): a docs change ships in the
  same repo history as the code change it documents.
- Architecture diagrams live in `architecture/` as Mermaid blocks
  (rendered by GitHub and Obsidian).
