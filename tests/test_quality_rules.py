from pathlib import Path


def test_quality_rule_files_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "config" / "newsnow_frequency_words.txt").exists()
    assert (root / "config" / "newsnow_event_rules.txt").exists()
