"""SPEC-TRADING-007 Static Market Context — cron-managed .md files.

Files in `~/trading/data/contexts/`:
- macro_context.md  (06:00 KST cron, no LLM)
- macro_news.md     (Friday 16:30 KST cron, single Sonnet 4.6 call)
- micro_context.md  (06:30 KST cron, no LLM)
- micro_news.md     (06:45 KST trading-day cron, no LLM)
"""
