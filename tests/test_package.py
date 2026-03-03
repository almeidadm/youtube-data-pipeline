"""Very small test ensuring the top–level package exports expected symbols."""

import src
from src import do_collect


def test_package_exports():
    assert hasattr(src, "__version__"), "package should define __version__"
    assert callable(do_collect), "do_collect should be importable from package"
