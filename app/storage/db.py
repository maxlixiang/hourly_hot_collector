# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL;")
    connection.execute("PRAGMA foreign_keys=ON;")
    return connection


def ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_sql: str,
) -> None:
    columns = {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def init_db(db_path: str | Path) -> None:
    with get_connection(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS fetch_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_type TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                note TEXT
            );

            CREATE TABLE IF NOT EXISTS news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fetch_run_id INTEGER,
                source_type TEXT NOT NULL,
                source_id TEXT,
                source_name TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                summary TEXT,
                published_at TEXT,
                fetched_at TEXT NOT NULL,
                normalized_title TEXT,
                content TEXT,
                content_fetch_status TEXT DEFAULT 'pending',
                content_fetch_error TEXT,
                raw_file_path TEXT,
                raw_source_index INTEGER,
                raw_item_index INTEGER,
                category_hint TEXT,
                language TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (fetch_run_id) REFERENCES fetch_runs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_news_items_fetched_at
            ON news_items (fetched_at);

            CREATE INDEX IF NOT EXISTS idx_news_items_source_id
            ON news_items (source_id);

            CREATE INDEX IF NOT EXISTS idx_news_items_source_type
            ON news_items (source_type);

            CREATE INDEX IF NOT EXISTS idx_news_items_normalized_title
            ON news_items (normalized_title);

            CREATE INDEX IF NOT EXISTS idx_news_items_content_fetch_status
            ON news_items (content_fetch_status);
            """
        )
        ensure_column(connection, "news_items", "raw_source_index", "raw_source_index INTEGER")


def create_fetch_run(
    run_type: str,
    db_path: str | Path,
    started_at: str,
    status: str = "running",
    note: str | None = None,
) -> int:
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO fetch_runs (run_type, started_at, status, note)
            VALUES (?, ?, ?, ?)
            """,
            (run_type, started_at, status, note),
        )
        return int(cursor.lastrowid)


def finish_fetch_run(
    run_id: int,
    status: str,
    db_path: str | Path,
    finished_at: str,
    note: str | None = None,
) -> None:
    with get_connection(db_path) as connection:
        connection.execute(
            """
            UPDATE fetch_runs
            SET finished_at = ?, status = ?, note = ?
            WHERE id = ?
            """,
            (finished_at, status, note, run_id),
        )


def get_previous_successful_run_finished_at(
    db_path: str | Path,
    current_run_id: int,
) -> str | None:
    with get_connection(db_path) as connection:
        row = connection.execute(
            """
            SELECT finished_at
            FROM fetch_runs
            WHERE id < ?
              AND status IN ('success', 'partial')
              AND finished_at IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (current_run_id,),
        ).fetchone()
        if row is None:
            return None
        return row[0]


def insert_news_item(item: dict[str, Any], db_path: str | Path) -> int:
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO news_items (
                fetch_run_id,
                source_type,
                source_id,
                source_name,
                title,
                url,
                summary,
                published_at,
                fetched_at,
                normalized_title,
                content,
                content_fetch_status,
                content_fetch_error,
                raw_file_path,
                raw_source_index,
                raw_item_index,
                category_hint,
                language,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.get("fetch_run_id"),
                item["source_type"],
                item.get("source_id"),
                item["source_name"],
                item["title"],
                item["url"],
                item.get("summary"),
                item.get("published_at"),
                item["fetched_at"],
                item.get("normalized_title"),
                item.get("content"),
                item.get("content_fetch_status", "pending"),
                item.get("content_fetch_error"),
                item.get("raw_file_path"),
                item.get("raw_source_index"),
                item.get("raw_item_index"),
                item.get("category_hint"),
                item.get("language"),
                item["created_at"],
            ),
        )
        return int(cursor.lastrowid)


def bulk_insert_news_items(items: list[dict[str, Any]], db_path: str | Path) -> int:
    if not items:
        return 0

    rows = [
        (
            item.get("fetch_run_id"),
            item["source_type"],
            item.get("source_id"),
            item["source_name"],
            item["title"],
            item["url"],
            item.get("summary"),
            item.get("published_at"),
            item["fetched_at"],
            item.get("normalized_title"),
            item.get("content"),
            item.get("content_fetch_status", "pending"),
            item.get("content_fetch_error"),
            item.get("raw_file_path"),
            item.get("raw_source_index"),
            item.get("raw_item_index"),
            item.get("category_hint"),
            item.get("language"),
            item["created_at"],
        )
        for item in items
    ]

    with get_connection(db_path) as connection:
        connection.executemany(
            """
            INSERT INTO news_items (
                fetch_run_id,
                source_type,
                source_id,
                source_name,
                title,
                url,
                summary,
                published_at,
                fetched_at,
                normalized_title,
                content,
                content_fetch_status,
                content_fetch_error,
                raw_file_path,
                raw_source_index,
                raw_item_index,
                category_hint,
                language,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        return len(rows)
