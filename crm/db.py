"""SQLite database connection management and migration runner for the CRM."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app import config


def get_db_path() -> Path:
    """Resolve the CRM SQLite database path."""
    override = os.environ.get("CRM_DB_PATH")
    if override:
        path = Path(override).resolve()
    else:
        path = (config.OUTPUT_DIR / "crm.db").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


@contextmanager
def get_db(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a transactional SQLite connection."""
    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
