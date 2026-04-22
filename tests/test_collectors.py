from pathlib import Path


def test_collector_paths_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "hourly_hot_collector.py").exists()
    assert (root / "config" / "rss_sources.txt").exists()
