# Architecture and Design

This document provides a high‑level view of how the components interact and the
rationale behind key decisions.

## Components

* `youtube_client.py` – a thin HTTP wrapper for the YouTube Data API v3.  It
  builds request URLs, adds API key/auth headers, and implements retries with
  exponential backoff for 429/5xx responses or network errors.  It also accepts
  an optional tracker object for logging each call's parameters and estimated
  quota cost.
* `collection_flow.py` – contains the various pipelines which orchestrate API
  calls, pagination, and persistence.  Four main pipelines exist:
  * `pipeline_channel_videos` (default) – collect videos uploaded by channels.
  * `pipeline_search_channels` – discover channels via search and fetch
    metadata.
  * `pipeline_search_videos` – discover videos via search and fetch details.
  * `pipeline_video_comments` – collect comment threads and replies for videos.
* `storage.py` – SQLite-based store with tables for `runs`, `channels`, `videos`,
  `comment_threads`, `comments` and `requests`.  Upsert helpers ensure
  deduplication by `id` and maintain `run_id` for traceability.
* `tracking.py` & `quota.py` – logic to estimate quota cost of each call and
  persist a log entry.  Supports summarization by resource and run.
* `cli.py` – command-line interface providing entry points for collection,
  quota reporting, export and log inspection.
* `monitoring.py` – structured logging layer writing to console (INFO) and
  rotating file (DEBUG).  Integrated throughout the codebase for observability.

## Database Schema

See the “Esquema de Banco de Dados” section in the main README; the schema is
intentionally simple and stores raw JSON in text columns to avoid schema
migrations or tight coupling to the API's field set.

## Pipeline Strategy

All pipelines follow the pattern:

1. **Seed/Discovery** – obtain a list of resource IDs from configuration or
   search results.
2. **Fetch** – call the appropriate API endpoint(s), handling pagination.
3. **Enrich** – where applicable, perform secondary calls (e.g. resolving
   video metadata after listing playlist items).
4. **Persist** – upsert fetched objects into the database, maintaining a
   `run_id` and timestamp.
5. **Track** – log quota consumption and request parameters for auditing.

The chosen workflow reflects the principles in `instructions.md`: minimize
expensive `search.list` calls, use `part`/`fields` to reduce payload, and avoid
refetching known items via the incremental flag.

## Logging & Monitoring

Logs are written to `data/logs` using a timestamp and run ID in the filename.
Console output provides user‑friendly progress messages; file logs capture
debug‑level details such as every API request and retry.  The CLI exposes a
`logs` command to list created files.

## Extensibility

New pipelines can be added by following the existing pattern and updating the
CLI routing logic.  Storage helpers make adding new resource types (e.g. “play
lists” or “channels statistics snapshots”) straightforward.

## Tests

The test suite (under `tests/`) uses simple dummy clients and fixtures to
simulate API responses.  Coverage includes client retries, storage operations,
pipelines, the CLI, and logging initialization.  Running `pytest -q` should
result in all tests passing.
