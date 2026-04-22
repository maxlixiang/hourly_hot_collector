from pathlib import Path


def test_pipeline_entry_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "hot_topic_pipeline.py").exists()
