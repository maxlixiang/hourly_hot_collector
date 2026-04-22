# hourly_hot_collector

`hourly_hot_collector` is a single-repo Python project for:
- collecting NewsNow and RSS news streams
- writing markdown and raw snapshots
- storing structured news items in SQLite
- discovering hot clusters from recent data
- preparing the project for future Agent and RAG analysis layers

## Core capabilities

### 1. Collectors
- NewsNow hourly snapshot collection
- RSS incremental collection
- markdown output
- raw JSON output
- SQLite ingestion
- failed source logging

### 2. Hot topic pipeline
- reads from SQLite instead of markdown
- 6-hour analysis window
- lightweight deduplication
- separate `newsnow` and `rss` clustering
- NewsNow quality filtering via external rule files
- semantic clustering and heat ranking

## Project structure

```text
hourly_hot_collector/
├─ app/
├─ config/
├─ data/
├─ docs/
├─ logs/
├─ scripts/
├─ tests/
├─ hourly_hot_collector.py
├─ hot_topic_pipeline.py
├─ db.py
└─ requirements.txt
```

Key runtime directories:
- `config/`: runtime configuration and rule files
- `data/db/`: SQLite database
- `data/raw/`: raw collector outputs
- `data/markdown/`: markdown snapshots
- `data/hot/`: hot cluster outputs
- `logs/`: runtime logs

## Configuration

Main runtime config is loaded from `.env`.

Important files:
- `config/rss_sources.txt`
- `config/newsnow_frequency_words.txt`
- `config/newsnow_event_rules.txt`
- `config/collector.example.env`
- `config/pipeline.example.env`

## Setup

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
```

## Run collectors

```bash
python hourly_hot_collector.py
```

This writes data to:
- `data/markdown/newsnow/`
- `data/markdown/rss/`
- `data/raw/newsnow/`
- `data/raw/rss/`
- `data/db/data_hub.db`
- `logs/failed_sources.log`

## Run hot topic pipeline

```bash
python hot_topic_pipeline.py
```

This writes outputs to:
- `data/hot/newsnow/`
- `data/hot/rss/`

## Package-style entry scripts

You can also use:

```bash
python scripts/run_collector.py
python scripts/run_hot_pipeline.py
```

## Architecture notes

Current production entrypoints remain at the repository root for compatibility.
Core logic is being incrementally moved into:
- `app/collectors/`
- `app/pipelines/`
- `app/storage/`

Future layers are reserved in:
- `app/agents/`
- `app/rag/`
- `app/schemas/`
- `app/utils/`

See:
- [Architecture](docs/ARCHITECTURE.md)
- [Data Flow](docs/DATA_FLOW.md)
- [Agents](docs/AGENTS.md)
- [IO Spec](docs/IO_SPEC.md)
- [Release Rules](docs/RELEASE.md)

## Release and tag rules

This repository uses lightweight semantic version tags.

Current rule:
- use tags like `v0.1.0`, `v0.2.0`, `v1.0.0`
- create a GitHub Release for each meaningful milestone
- patch: small fixes
- minor: new features / pipeline upgrades
- major: breaking layout or runtime changes

Detailed rules are documented in [docs/RELEASE.md](docs/RELEASE.md).
