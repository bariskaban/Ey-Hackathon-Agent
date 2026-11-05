"""Read-only SQLite access. Servers never get a writable connection to the records."""

import sqlite3
from contextlib import contextmanager

from servers.common.config import db_path


@contextmanager
def read_only():
    """Open the database read-only. Use parameterised queries only: never build SQL from input."""
    path = db_path()
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    try:
        yield con
    finally:
        con.close()
