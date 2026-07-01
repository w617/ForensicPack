import sys

import pytest


_WINDOWS_TK_TARGET = "test_queue_filter_all_shows_all_rows"


def pytest_collection_modifyitems(items):
    for item in items:
        if sys.platform.startswith("win") and item.name == _WINDOWS_TK_TARGET:
            item.add_marker(
                pytest.mark.xfail(
                    reason="Tk reports packed queue rows as unmapped until the Windows event loop paints them.",
                    strict=False,
                )
            )
