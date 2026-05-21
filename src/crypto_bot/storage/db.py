from __future__ import annotations

import sqlite3
from pathlib import Path


def sqlite_path_from_url(url: str) -> Path:
    if not url.startswith("sqlite:///"):
        raise ValueError("only sqlite:/// storage URLs are supported in the MVP")
    return Path(url.replace("sqlite:///", "", 1))


def connect_sqlite(url: str) -> sqlite3.Connection:
    db_path = sqlite_path_from_url(url)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path)
