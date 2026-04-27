#!/usr/bin/env python3
"""Apply the additive saved_jurisdictions schema to data/cache.db with a backup."""

import os
import shutil
import sqlite3
from datetime import datetime, timezone

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.environ.get("CACHE_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or os.path.join(REPO_ROOT, "data")
CACHE_DB = os.environ.get("CACHE_DB") or os.path.join(DATA_DIR, "cache.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_jurisdictions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL,
  city TEXT NOT NULL,
  state TEXT NOT NULL,
  trade TEXT,
  display_name TEXT,
  added_at TEXT NOT NULL,
  last_lookup_at TEXT,
  lookup_count INTEGER DEFAULT 0,
  notes TEXT,
  UNIQUE(email, city, state, trade)
);

CREATE INDEX IF NOT EXISTS idx_saved_jurisdictions_email ON saved_jurisdictions(email);
"""


def main() -> int:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CACHE_DB):
        # sqlite would create it anyway; make the behavior explicit in stdout.
        open(CACHE_DB, "a").close()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = f"{CACHE_DB}.bak-saved-jurisdictions-{stamp}"
    shutil.copy2(CACHE_DB, backup)
    conn = sqlite3.connect(CACHE_DB)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.execute("SELECT * FROM saved_jurisdictions LIMIT 1").fetchall()
    conn.close()
    print(f"backup={backup}")
    print(f"applied={CACHE_DB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
