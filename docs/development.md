# Developer Notes

This document is intended for contributors who are extending the project or
running the code locally.

## Setting Up

Follow the installation steps in `docs/usage.md`.  A virtual environment is
recommended to isolate dependencies.

## Running Tests

Execute all tests with:

```bash
pytest -q
```

Individual tests may be targeted by file name (e.g. `pytest tests/test_cli.py`).
Mock clients are used extensively to avoid hitting the real YouTube API during
unit tests.

## Adding Features

1. Add or update functions in `src/collection_flow.py` or other modules as
   needed.
2. If new resource types are persisted, update `src/storage.py` with table
   definitions and upsert helpers.
3. Write unit tests in the `tests/` directory to cover the new behavior.
4. Update the CLI (`src/cli.py`) if new commands or options are required,
   ensuring tests for the CLI are added.
5. Run `pytest` to verify all tests pass.

## Coding Standards

- Keep external dependencies to a minimum; the project currently uses only
  standard library modules and `pytest` for testing.
- Use type hints where reasonable and keep functions short and focused.
- Logging should use the helpers in `monitoring.py` and not perform side
  effects other than writing to the configured logger.

## Documentation

Documentation is stored in this `docs/` directory.  When making changes to the
project, update the relevant docs file and add a note in `progress.md` if the
change is significant.

## Further Reading

Refer to `instructions.md` for the original project goals and constraints.  It
contains valuable reminders about quota costs and API usage patterns.