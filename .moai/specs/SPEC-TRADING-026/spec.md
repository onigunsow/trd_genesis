---
id: SPEC-TRADING-026
version: 0.1.0
status: in-progress
created: 2026-05-23
updated: 2026-05-23
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "Overheating (단기과열) softening — keep 55 as a de-weighted, size-capped candidate instead of a hard block"
related_specs:
  - SPEC-TRADING-018
  - SPEC-TRADING-020
  - SPEC-TRADING-023
  - SPEC-TRADING-025
---

# SPEC-TRADING-026 — Overheating Softening

## Overview

SPEC-025 hard-excluded every exchange-blocked ticker — including 단기과열
(stat_cls 55) — from the screener. On a market-wide surge day (2026-05-22,
KOSPI +8.42%) the exchange designated 53 tickers 단기과열, which was a **100%
overlap** with the screener's 20 picks → **zero actionable candidates → zero
signals for the entire session**. Worse, SPEC-025's screener filter was *inert*
because its blocked-file freshness check ran before the refresh cron
(screener 06:30 vs refresh 07:25), so the real exclusion happened downstream at
the persona + execution layers, which treated 단기과열(55) identically to a true
trading halt (거래정지 54).

This SPEC reclassifies 단기과열(55) as **tradeable-but-cautioned** across all three
layers, while keeping the genuine risk states (관리 51 / 투자위험 52 / 투자경고 53 /
거래정지 54, and any unknown/missing stat_cls) a hard block.

## Root Cause (verified 2026-05-23)

1. `data/blocked_tickers.json` 2026-05-22: 53 tickers, all reason 단기과열,
   stat_cls 55. `data/screened_tickers.json`: 20 picks — **20/20 in the blocked
   set**.
2. Cron ordering: screener (06:30) ran before the blocked refresh (07:25) → the
   screener saw a stale file → SPEC-025 set-difference degraded to empty → the
   20 overheated picks passed through, then were blocked at decision/execution.
3. `market_safety.check_pre_order_safety` blocked any non-normal stat_cls,
   conflating 단기과열(55) with 거래정지(54).

## Requirements (EARS)

### REQ-026-1 (P0) — Screener keeps 55
WHEN the screener runs, THEN 단기과열(55) tickers SHALL remain in the candidate
pool (not set-differenced out). Hard blocks (51~54 / unknown) remain excluded.

### REQ-026-2 (P0) — Screener de-weights 55
WHILE scoring, THEN 단기과열(55) candidates SHALL receive a score penalty so
strong names can still surface but rank below equivalent non-overheated names.

### REQ-026-3 (P0) — Conservative classification
The system SHALL treat ONLY an explicit `stat_cls == "55"` as overheated; any
entry missing `stat_cls` (e.g. intraday safety-recorded blocks) SHALL be a hard
block.

### REQ-026-4 (P1) — Threshold guard
WHEN the overheated count is market-wide (>= 15 tickers OR >= 30% of the pool),
THEN the penalty SHALL be light (regime artifact); otherwise strong
(stock-specific). `_overheat_penalty(count, pool) -> (penalty, market_wide)`.

### REQ-026-5 (P1) — Blocked-file freshness + cron order
The screener SHALL accept a blocked file dated today OR yesterday (KST), and the
refresh cron SHALL run before the screener (moved 07:25 → 06:20).

### REQ-026-6 (P0) — Persona layer keeps 55
The micro watchlist filter and the decision candidate filter SHALL exclude only
hard blocks; 단기과열(55) stays. `_split_blocked(blocked) -> (hard, overheated)`.

### REQ-026-7 (P1) — Prompt caution section
micro.jinja / decision.jinja SHALL render 단기과열(55) in a "비중 축소 / 주의"
section, separate from the hard-exclude / 제안 금지 section.

### REQ-026-8 (P0) — Execution gate softening
`check_pre_order_safety` SHALL NOT hard-block 단기과열(55); it sets
`overheated=True`. For an overheated BUY the orchestrator SHALL size-cap by
`OVERHEAT_SIZE_FACTOR` (0.5) and force a limit order at the reference price
(single-price auction). Sells are unchanged so risk exits are never throttled.
51~54 remain hard blocks.

## Implementation status (2026-05-23)

DONE (TDD, all green, zero regressions vs main):
- REQ-026-1/2/3/4/5: `screener/daily_screen.py`, `kis/market.py`
  (`is_overheated`/`is_hard_block`/`OVERHEAT_STAT_CLS`), `scheduler/runner.py`
  (06:20 cron). Tests: `tests/screener/test_overheat_softening.py`.
- REQ-026-6/7: `personas/orchestrator.py` (`_split_blocked`, watchlist +
  candidate filters), `personas/prompts/{micro,decision}.jinja`. Tests:
  `tests/personas/test_overheat_persona.py`.
- REQ-026-8: `risk/market_safety.py` (`overheated`, `OVERHEAT_SIZE_FACTOR`),
  `personas/orchestrator.py` (`_apply_overheat_order_policy`, `_execute_signal`).
  Tests: `tests/risk/test_market_safety_overheat.py`,
  `tests/personas/test_overheat_persona.py::TestOverheatOrderPolicy`.
- Daily-report message accuracy (separate concern, same session): cli_only_mode
  vs missing-key vs failure now distinguished. `reports/daily_report.py`,
  `tests/reports/test_daily_report_llm_skip.py`.

DONE — c3 (news sector reclassification):
- Root cause: `Article.sector = source.sector` (feed-level) in `news/normalizer.py`,
  so generic/overlap feeds mislabel content (bio article from a semiconductor or
  energy feed). New `news/sector_classifier.py::classify_sector(title, text,
  fallback)` overrides the feed sector only when content matches a different
  sector with high confidence (title-weighted score >= 2, strictly beating the
  feed score); wired into the normalizer. Tests:
  `tests/news/test_sector_classifier.py` (incl. the real 5/22 misclassifications).

DONE — c4 (stable news-alert dedup):
- Root cause: dedup hashed `representative_title`, which changes when
  re-clustering admits a higher-impact member → same story re-alerted under a new
  headline. (`news_alerts_sent` table itself was healthy.) Now dedups on stable
  per-article keys (`art:{id}` from `cluster.article_ids`) over an 18h rolling
  window; records only after a successful send. `news/intelligence/relevance.py`
  (`_alert_keys`, `_any_alerted_recently`, `_record_alerts`). Tests:
  `tests/news/intelligence/test_alert_dedup.py`. No schema migration (reuses the
  existing `content_hash` column).

REQ-026-9 (c3): article sector SHALL be content-derived, overriding the feed
sector only on a confident mismatch; otherwise the feed sector is kept.
REQ-026-10 (c4): a critical alert SHALL be suppressed if any member article of
the cluster was alerted within the dedup window, independent of the
representative title.

## Notes
- TRADING_MODE=paper; live KIS keys exist → execution-gate change confirmed with
  the user (option [2]: reduce-size + limit-only) before implementation.
- Persona/cost accounting shows 0 tokens by design (cli-claude-max subscription),
  not a bug.
