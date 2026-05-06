"""Build micro_news.md — delegates to SPEC-013 context_builder.

Legacy entry point kept for scheduler (runner.py) and CLI (cli.py) compatibility.
Actual generation logic lives in trading.news.context_builder.write_micro_news().
"""

from __future__ import annotations

import logging

from trading.news.context_builder import write_micro_news

LOG = logging.getLogger(__name__)


def main() -> int:
    """Entry point for scheduler cron and CLI ``build-context news-micro``."""
    return write_micro_news()


if __name__ == "__main__":
    raise SystemExit(main())
