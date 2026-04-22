# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time
import traceback
from pathlib import Path

from app.collectors.collector_common import (
    DB_FILE,
    FAILED_LOG_FILE,
    build_run_note,
    ensure_output_dirs,
    format_dt,
    now_local,
    now_text,
    sleep_until_next_run,
    summarize_run_status,
)
from app.collectors.newsnow_collector import collect_newsnow
from app.collectors.rss_collector import collect_rss
from app.storage.db import create_fetch_run, finish_fetch_run, init_db


def run_once() -> dict[str, Path | str]:
    ensure_output_dirs()
    init_db(DB_FILE)

    generated_at = now_local()
    fetch_run_id = create_fetch_run("mixed", DB_FILE, started_at=format_dt(generated_at))

    try:
        newsnow_result = collect_newsnow(generated_at, fetch_run_id)
        rss_result = collect_rss(generated_at, fetch_run_id)

        status = summarize_run_status(
            newsnow_result["failed"],
            rss_result["failed"],
            rss_result["source_count"],
        )
        note = build_run_note(newsnow_result["failed"], rss_result["failed"])
        finish_fetch_run(fetch_run_id, status, DB_FILE, finished_at=now_text(), note=note)
    except Exception as exc:
        finish_fetch_run(
            fetch_run_id,
            "failed",
            DB_FILE,
            finished_at=now_text(),
            note=f"Fatal error: {type(exc).__name__}: {exc}",
        )
        raise

    if FAILED_LOG_FILE.exists():
        print(f"[INFO] Failed source log: {FAILED_LOG_FILE}")
    print(f"[INFO] Database file: {DB_FILE}")

    return {
        "newsnow_markdown": newsnow_result["markdown_path"],
        "newsnow_raw": newsnow_result["raw_path"],
        "rss_markdown": rss_result["markdown_path"],
        "rss_raw": rss_result["raw_path"],
        "db_file": str(DB_FILE),
        "fetch_run_status": status,
    }


def main() -> None:
    run_immediately = os.getenv("RUN_IMMEDIATELY", "true").lower() in {"1", "true", "yes", "y"}

    if run_immediately:
        try:
            run_once()
        except Exception:
            print("[FATAL] Initial run failed")
            traceback.print_exc()

    while True:
        try:
            sleep_until_next_run()
            run_once()
        except KeyboardInterrupt:
            print("[INFO] Received shutdown signal, exiting")
            break
        except Exception:
            print("[FATAL] Scheduled run failed")
            traceback.print_exc()
            print("[INFO] Retry after 60 seconds")
            time.sleep(60)


if __name__ == "__main__":
    main()
