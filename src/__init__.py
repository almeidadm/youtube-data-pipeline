"""Top-level package for YouTube API Collector (MVP).

This module exists primarily to make the library easier to import from
interactive environments such as Jupyter notebooks.  It also exposes a
handful of convenience symbols so that users can call entry points without
needing to import from deep submodules.
"""

__version__ = "0.1.0"

# re-export some useful helpers
from .cli import do_collect, do_export, do_quota, do_logs  # noqa: F401
