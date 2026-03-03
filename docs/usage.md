# Usage Guide

This document expands on the quickstart material found in the repository root
`README.md`.  It collects all the commands and sample workflows in one place.

## Installation

1. Clone the repository and `cd` into it:
   ```bash
   git clone <repo-url> && cd youtube-api
   ```
2. Create a virtual environment and activate it (optional but recommended):
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
3. Install development dependencies (primarily `pytest`):
   ```bash
   python -m pip install -U pytest
   ```
4. Set up credentials via environment variables or the `config/credentials.md`
   instructions:
   ```bash
   export YOUTUBE_API_KEY=YOUR_KEY
   export YOUTUBE_DB_PATH=data/youtube.db
   ```

## CLI Commands

The CLI entry point is `src/cli.py` and may be invoked directly or as a module:

```bash
python src/cli.py <command>
# or
python -m src.cli <command>
```

### collect

Collects data according to the plan defined in `config/collection_plan.json`.

- `--seeds <path>` – override plan file (default uses configuration).  Seeds may
  include `channel_ids`, `playlist_ids`, `queries` and `video_ids` depending on
  the pipeline type.
- `--incremental` – skip videos already present in the database.
- `--pipeline-type` – choose one of `channel_videos`, `search_channels`,
  `search_videos` or `video_comments` (defaults to the plan value or
  `channel_videos`).

Each pipeline consumes a different portion of the plan's seeds.  For example,
`channel_videos` reads `channel_ids` and ignores others, whereas
`video_comments` only looks at `video_ids`.  The configuration file also
allows specifying `pipeline_options` such as `order` for search pipelines or
`batch_size` for channel flows; see `docs/architecture.md` for details and
examples.

### quota

Display quota consumption summary.  Options mirror the implementation in
`src/storage.py`.

```bash
python src/cli.py quota [--run-id <uuid>]
```

### export

Dump table contents to JSON lines.  Example:

```bash
python src/cli.py export --tables videos comments > data.jsonl
```

### logs

List available log files created during previous runs and show basic metadata.

```bash
python src/cli.py logs
```

## Examples

Several examples are shown in the root `README.md`; users may refer to them for
practical workflows (configuring seeds, running incremental jobs, inspecting
quota and exporting data.

## Jupyter Notebooks

The codebase is intentionally friendly to interactive usage.  A sample notebook
lives in `notebooks/YouTube_API_Collector_Examples.ipynb` and can be opened with
`jupyter notebook` or any compatible viewer.  The notebook demonstrates how to
add the repository root to `sys.path`, set environment variables, import the
core helpers (`do_collect`, `storage.SqliteStore`, etc.) and execute a
collection programmatically.

To work with your own notebooks simply ensure that the Python process can
import the `src` package (e.g. by running from the repository root or
by installing the package in editable mode) and configure
`YOUTUBE_API_KEY` and `YOUTUBE_DB_PATH` as shown in the example.

## Troubleshooting

This file is not exhaustive; refer to the Troubleshooting section of the root
`README.md` for common error messages such as missing API key or quota problems.

## Running Tests

Development and CI workflows rely on the included `pytest` suite. After
activating your virtual environment, simply invoke:

```bash
pytest -q
```

You may target individual files or tests (`pytest tests/test_cli.py`) when
working on specific features. The tests use lightweight dummy clients and do
not contact the real YouTube API, so they are fast and safe to run in offline
or notebook environments.