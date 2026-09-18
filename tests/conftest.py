"""Close pooled SQLite connections so tmp_path fixtures can delete DB files."""

from __future__ import annotations

import pytest

from sequoia_x.db import close_pooled_connections


@pytest.fixture(autouse=True)
def _close_sqlite_pool() -> None:
    yield
    close_pooled_connections()
