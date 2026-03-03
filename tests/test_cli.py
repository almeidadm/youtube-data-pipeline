import io
import json
import sys

import src.cli as cli
from src.storage import SqliteStore


def test_cli_quota_help():
    # Just test that the command accepts --run-id argument without error
    # More comprehensive testing would require mocking the global store
    argv = sys.argv
    sys.argv = ["cli.py", "quota", "--help"]
    try:
        cli.main()  # will print help and exit
    except SystemExit as e:
        assert e.code == 0
    finally:
        sys.argv = argv


def test_cli_help():
    # just ensure argparse prints usage without error
    argv = sys.argv
    sys.argv = ["cli.py", "--help"]
    try:
        cli.main()  # will print help and exit
    except SystemExit as e:
        assert e.code == 0
    finally:
        sys.argv = argv

