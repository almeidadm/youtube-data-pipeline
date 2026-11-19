# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

- Added YRP dual-bucket credit governance to `CreditMonitor` and
  `YouTubeDataConfig` (`for_yrp`, Pacific-time quota-day helpers,
  `allow_endpoints` allow-list, per-endpoint call caps), wired through
  `YouTubeDataClient.fetch`.
- Added `playlistItems.list` and `videoCategories.list` to the endpoint
  registry, extra optional parameters for `search`, `videos`, `comments`,
  `channels` and `commentThreads`, and playlist error catalog/skip handling.
- Added structured `.jsonl` shard support to
  `scripts/collect_videos_from_terms_resumable.py` (cluster metadata, date
  windows, per-cluster `cluster_sightings.jsonl`, cross-cluster `videos.list`
  dedupe) plus `--yrp-mode` and per-bucket credit flags.
- Added `scripts/prepare_frozen_seed_shards.py` (deterministic disjoint shards
  from a seed CSV, with manifest and ready-to-run commands) and
  `scripts/unify_runs.py --disjoint-shards`.
- Added YRP collection pipelines: `collect_comments_resumable.py`,
  `collect_longitudinal_waves.py`, `collect_recidivist_channels_playlists.py`,
  `import_pilot_to_resumable_run.py` and `export_yrp_dehydrated.py`.
- Added `tests/test_yrp_quota_plan.py` and
  `tests/test_prepare_frozen_seed_shards.py`.
- Added `CLAUDE.md`, the Diátaxis index `docs/README.md`, the C4 architecture
  document `docs/architecture/c4.md` and
  `docs/youtube_researcher_program_quota_plan_20261118.md`.
- Added `src/youtube_data_pipeline/query_preparation.py` with reusable helpers to
  normalize news records, clean titles, prepare linkage seeds, and build
  query manifests linked to `record_id`.
- Added `notebooks/eda_news_query_preparation.ipynb` to run EDA over the
  consolidated news catalog, restricted to the `catalogacao_final="mercado"`
  slice, and prepare optimized YouTube search queries without changing the
  public client contract.
- Added `docs/news_query_preparation.md` describing the notebook flow, title
  heuristics, artifact schemas, and `origin_value` reconciliation.
- Added `tests/test_query_preparation.py` covering normalization, title
  cleaning, title-quality classification, entity extraction, query generation,
  artifact output, and notebook structure.
- Added `scripts/collect_videos_from_terms.py` to run bulk `search -> videos`
  collection from a terms file (`--terms-file`), with credit-limit control and
  per-run JSON summary output.
- Added `docs/bulk_terms_collection.md` with terminal and notebook usage for
  bulk collection.
- Added a README section for bulk collection workflow and output summary file.
- Added `scripts/collect_channel_bundle.py` to collect a complete channel
  bundle from `--channel-id` (`channels -> activities -> playlistItems -> videos
  -> commentThreads -> comments`) with credit limit control and
  `channel_collection_summary.json`.
- Added `docs/channel_collection.md` describing the channel flow, CLI
  parameters, output artifacts, and a Mermaid diagram.
- Added tests for the channel bundle CLI flow, including sequence, credit-limit
  stop, skip vs stop handling, deduplication/max-videos, and run-scoped
  manifest persistence.
- Added `scripts/collect_videos_from_terms_resumable.py` as a checkpointed copy
  of the bulk terms collector, with exact resume support when rerun with the
  same `--run-id`.
- Added `tests/test_collect_videos_from_terms_resumable.py` covering fresh
  runs, mid-search resume, mid-videos resume, no-op reruns, and legacy
  bootstrap compatibility checks.

### Changed

- Reduced the resumable terms collector defaults after the Researcher Program
  cycle ended: `--credit-limit` from `110000` to `10000` and `--search-pages`
  from `5` to `3`; documented the quota transition in
  `docs/comando_collect_videos_from_terms.md` and
  `docs/metodologia_busca_youtube.md`.
- `README.md` now links the documentation index (`docs/README.md`).
- Updated `README.md` with notebook installation guidance and the news-query
  preparation workflow entry point.
- Updated the notebook and its documentation to discard `outros` and
  duplicate records before preparing `mercado` search artifacts.
- Added a `notebook` optional dependency group in `pyproject.toml` for the EDA
  and artifact-generation stack (`pandas`, `pyarrow`, `matplotlib`,
  `seaborn`).
- Updated the bulk terms documentation and README to recommend the resumable
  CLI, document `terms_collection_checkpoint.json`, and explain resume flow with
  a Mermaid diagram.

### Fixed

- Restored `src/youtube_data_pipeline/query_preparation.py` and
  `tests/test_query_preparation.py`, which were documented above but absent
  from the repository history (reconstructed from surviving `.pyc` files and
  the EDA notebook; validated against real notebook outputs).
