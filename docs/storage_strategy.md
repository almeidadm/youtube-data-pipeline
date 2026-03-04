# Storage Strategy Analysis and Recommendations

## Current design overview
The current storage layout is:

```
{storage_root}/{endpoint}/{timestamp}/response_001.json
{storage_root}/{endpoint}/{timestamp}/response_002.json
...
```

It is simple and easy to implement, but it treats each timestamped folder as the only context for a collection run, with no explicit session boundaries or request metadata adjacent to the raw files.

## Failure modes and risks in the current design
- **Collision risk (timestamp precision)**: timestamps are seconds-based. Multiple runs of the same endpoint within the same second can overwrite `response_001.json` and subsequent pages.
- **No run/session grouping**: there is no `run_id` or job boundary in the path, which complicates cleanup, auditing, or replaying a single run.
- **No request metadata near data**: origin parameters, request IDs, and pagination state are not saved alongside the files; you must consult logs to reconstruct intent.
- **No atomic write**: `write_text` can leave partial files if interrupted (power loss, process kill).
- **No integrity checks**: there is no checksum or manifest to detect corruption or missing pages.
- **Manual lookup pain**: a person has to cross-reference logs to identify which files correspond to a specific query or origin.

## Recommended default for this repository
Adopt a **run-scoped, origin-aware, indexed layout** designed for both machine retrieval and human inspection.

```
{root}/runs/{run_id}/
  manifest.jsonl
  endpoint=search/origin=q:HASH/page_001.json
  endpoint=videos/origin=id:HASH/page_001.json
```

### Key properties
- **Run scoped**: all requests for a job live under one `run_id`.
- **Origin hashed**: keeps path length safe while still grouping related requests.
- **Manifest file**: a JSONL index with one row per request containing endpoint, origin, params hash, page, request_id, file path, and credits.
- **Atomic writes**: write to a temp file then rename.
- **Optional compression**: `.json.gz` for large payloads.

### Example manifest line
```json
{"request_id":"run-001-000012","endpoint":"search","origin":"q:imposto de 44%","origin_hash":"9e7d...","page":1,"file":"endpoint=search/origin=q:9e7d/page_001.json","cost":100,"credits_total":1200}
```

## Alternative storage options (tradeoffs)

### 1) Data-lake partitions
```
{root}/dt=YYYY-MM-DD/endpoint=search/part-0001.jsonl
```
- Pros: fast filtering and analytics (DuckDB, Spark), easy partition pruning.
- Cons: less human-friendly per request; you must consult an index for exact origins.

### 2) SQLite/DuckDB index + raw files
- Pros: instant SQL filtering by origin, endpoint, run_id; easy to hand-inspect queries.
- Cons: adds a database file to manage and back up.

### 3) Content-addressed storage
```
{root}/cas/sha256/<hash>.json
```
- Pros: deduplication, integrity by hash, no overwrites.
- Cons: hard to browse manually without a separate index.

## Migration guidance
If you adopt the recommended layout, you can migrate incrementally:
1. Start new runs in the run-scoped layout while keeping old data intact.
2. Create a one-time migration script that reads old timestamp folders and writes new files + manifest entries.
3. Keep the old structure read-only for auditing, and rely on the manifest for primary access.

## Summary recommendation
Use the run-scoped, origin-aware layout with a JSONL manifest. It preserves human readability, improves safety (no overwrites), and makes machine retrieval straightforward. If you need advanced querying, add a lightweight SQLite/DuckDB index on top of the manifest.
