#!/usr/bin/env python3
from __future__ import annotations

"""Local-only helper for separating PermitAssist operational DB tables from caches.

This script does not touch production or Railway.  It copies selected operational
application tables from a source SQLite database into app.db and selected
purgeable cache tables into cache.db so the migration can be tested locally
before any approval-gated production run.
"""

import argparse
import sqlite3
from pathlib import Path

APP_TABLES = {
    "users",
    "user_sessions",
    "magic_tokens",
    "api_keys",
    "webhook_integrations",
    "jobs",
    "permit_reminders",
    "email_captures",
    "referrals",
    "feedback",
}

CACHE_TABLES = {
    "permit_cache",
    "search_cache",
    "url_patterns",
    "pdf_cache",
    "lookup_stats",
    "beta_events",
}


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    return {str(row[0]) for row in rows}


def _copy_table(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> None:
    create_row = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if not create_row or not create_row[0]:
        return
    dst.execute(create_row[0])
    cols = [row[1] for row in src.execute(f"PRAGMA table_info({table})").fetchall()]
    if not cols:
        return
    placeholders = ",".join("?" for _ in cols)
    quoted_cols = ",".join(f'"{col}"' for col in cols)
    rows = src.execute(f"SELECT {quoted_cols} FROM {table}").fetchall()
    if rows:
        dst.executemany(f"INSERT INTO {table} ({quoted_cols}) VALUES ({placeholders})", rows)


def split_db(source: Path, app_db: Path, cache_db: Path) -> dict[str, list[str]]:
    if not source.exists():
        raise FileNotFoundError(source)
    app_db.parent.mkdir(parents=True, exist_ok=True)
    cache_db.parent.mkdir(parents=True, exist_ok=True)
    for path in (app_db, cache_db):
        if path.exists():
            path.unlink()
    copied = {"app_tables": [], "cache_tables": [], "skipped_tables": []}
    with sqlite3.connect(source) as src, sqlite3.connect(app_db) as app, sqlite3.connect(cache_db) as cache:
        tables = _existing_tables(src)
        for table in sorted(tables):
            if table in APP_TABLES:
                _copy_table(src, app, table)
                copied["app_tables"].append(table)
            elif table in CACHE_TABLES or table.endswith("_cache"):
                _copy_table(src, cache, table)
                copied["cache_tables"].append(table)
            else:
                copied["skipped_tables"].append(table)
        app.commit()
        cache.commit()
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description="Split a local PermitAssist cache.db into app.db + cache.db copies")
    parser.add_argument("--source", required=True, type=Path, help="source SQLite DB (local copy only)")
    parser.add_argument("--app-db", required=True, type=Path, help="output operational app DB")
    parser.add_argument("--cache-db", required=True, type=Path, help="output purgeable cache DB")
    args = parser.parse_args()
    result = split_db(args.source, args.app_db, args.cache_db)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
