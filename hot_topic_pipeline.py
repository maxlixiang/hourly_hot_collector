"""Compatibility wrapper. Prefer scripts/run_hot_pipeline.py for new usage."""

from app.pipelines.hot_topic_pipeline import *  # noqa: F401,F403
from app.pipelines.hot_topic_pipeline import main


if __name__ == "__main__":
    main()
