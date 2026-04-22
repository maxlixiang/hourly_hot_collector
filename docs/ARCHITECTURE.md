# hourly_hot_collector Architecture

## Current runtime entrypoints
- `hourly_hot_collector.py`: collector entrypoint
- `hot_topic_pipeline.py`: hot topic discovery entrypoint
- `db.py`: SQLite storage helpers

## Directory responsibilities
- `app/`: future modular code layout; current business logic will be moved here incrementally
- `config/`: runtime configuration files and rule files
- `data/db/`: SQLite database files
- `data/raw/newsnow/`: NewsNow raw JSON snapshots
- `data/raw/rss/`: RSS raw JSON snapshots
- `data/markdown/newsnow/`: NewsNow markdown snapshots
- `data/markdown/rss/`: RSS markdown snapshots
- `data/hot/newsnow/`: NewsNow hot cluster outputs
- `data/hot/rss/`: RSS hot cluster outputs
- `data/analysis/`: reserved for downstream agent analysis outputs
- `data/cache/embeddings/`: reserved for future embedding and retrieval caches
- `logs/`: runtime logs such as failed source records
- `scripts/`: convenience wrappers that keep root entry scripts untouched
- `tests/`: smoke tests and future unit tests

## Refactor policy for this stage
1. Keep root entry scripts working.
2. Move runtime outputs into `data/` and `logs/`.
3. Move mutable config into `config/`.
4. Add modular package skeletons first; move logic later.
5. Do not change SQLite schema or core algorithms in this stage.

## Next extraction steps
1. Move `db.py` into `app/storage/`
2. Move `hot_topic_pipeline.py` into `app/pipelines/`
3. Split `hourly_hot_collector.py` into `app/collectors/newsnow_collector.py` and `app/collectors/rss_collector.py`
4. Extract shared schemas and utilities into `app/schemas/` and `app/utils/`
5. Add agent and RAG implementations on top of `data/hot/` outputs
