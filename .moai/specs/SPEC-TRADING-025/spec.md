---
id: SPEC-TRADING-025
version: 0.1.0
status: draft
created: 2026-05-19
updated: 2026-05-19
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "Blocked-aware Daily Screener — exclude exchange-overheating tickers at LLM screening stage"
related_specs:
  - SPEC-TRADING-018
  - SPEC-TRADING-019
  - SPEC-TRADING-020
  - SPEC-TRADING-022
  - SPEC-TRADING-023
---

# SPEC-TRADING-025 — Blocked-aware Daily Screener

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-19 | 0.1.0 | Initial draft. Single-file hotfix: load `data/blocked_tickers.json` at the start of `daily_screen.py` and apply set-difference filter before LLM scoring. Driven by empirical 100% overlap between screened picks and exchange short-term overheating set since 2026-05-13, which has yielded zero micro buy candidates for 7 consecutive trading days. | onigunsow |

---

## Overview

`daily_screen.py` produces the daily candidate universe (~20 tickers) that the micro persona uses to propose buy candidates. Since 2026-05-13 the screened set has been a near-complete subset of the exchange "short-term overheating" (단기과열) blocked set, because the screener's scoring features (foreign net buy, low RSI, volume momentum) are highly correlated with the exchange's overheating designation rules. As a result, the micro persona has produced 0 buy candidates for 7 consecutive trading days.

This SPEC introduces a minimal, single-file change to `src/trading/screener/daily_screen.py`: load `data/blocked_tickers.json` at the start of the screening run and exclude blocked tickers from the candidate pool **before** LLM scoring. No penalty scoring, no fallback expansion — the intent is the smallest possible change that resolves the self-collision while remaining graceful under missing or stale blocked data.

### Position and Orthogonality

- **SPEC-TRADING-018** (micro persona blocked-ticker awareness, merged 2026-05-11): made the **micro persona** aware of the blocked list at decision time. This SPEC extends the same awareness one layer upstream to the **screener**, so the persona never even sees blocked candidates. Pattern reuse, no behavioural conflict.
- **SPEC-TRADING-019** (data refresh layer, merged 2026-05-11): guarantees `blocked_tickers.json` is refreshed by scheduled cron jobs. This SPEC consumes the artifact but does not modify the refresh layer.
- **SPEC-TRADING-020** (DEFAULT_WATCHLIST bias removal, merged 2026-05-12): the existing fallback path collapses when both the screened set and the DEFAULT_WATCHLIST are blocked. This SPEC is the upstream fix that prevents the screened set from collapsing in the first place. **Universe fallback expansion is explicitly deferred to SPEC-TRADING-026**.
- **SPEC-TRADING-022 / 023** (universe discovery + auto-expansion): operate on the data universe layer, orthogonal to the screener's LLM scoring stage.
- **SPEC-TRADING-024** (event-driven autonomous trading, Stage 1 deployed 2026-05-15): consumes screened candidates downstream; this SPEC improves the input quality without changing the consumer contract.

This SPEC is **additive only** to SPEC-001 ~ SPEC-024. The screener's public output schema (`data/screened_tickers.json`) is unchanged. The only observable behavioural difference is that blocked tickers no longer appear in the output set.

---

## Background

### Empirical Evidence

Verified via DB `persona_runs` query (2026-05-19):

- **Trading bot**: `src/trading/screener/daily_screen.py` runs at cron `35 6 * * 1-5`, LLM-scores the KOSPI200 ∪ KOSDAQ150 candidate universe, and writes ~20 picks to `data/screened_tickers.json`.
- **Exchange overheating set**: `data/blocked_tickers.json` (~57 tickers today) tracks exchange-designated short-term overheating tickers.
- **Bug**: From 2026-05-13 onward, the intersection between screened tickers and the blocked set has been **100%**. Net effect: micro persona receives 0 buy candidates → 0 trades for 7 consecutive trading days.
- **Prior baseline**: 5/11 ~ 5/12 sessions had 1 ~ 4 micro buy candidates per cycle.

### Root Cause

Screener scoring criteria (foreign net buy momentum, RSI low, volume momentum) are nearly identical to the rules used by the exchange to designate short-term overheating. In strong markets the two converge and self-collide: the screener picks exactly the tickers that the exchange has just blocked.

### Data Contract

`data/blocked_tickers.json` structure (produced by SPEC-019 refresh layer):

```
{
  "date": "YYYY-MM-DD",
  "blocked": {
    "005930": {"stat_cls": "55", "reason": "단기과열", "date": "..."},
    ...
  }
}
```

`daily_screen.py` already exposes `_parse_screened_json`, `load_screened_tickers`, and the main screening function — the change is localised to these existing entry points.

---

## Environment

- Existing SPEC-001 ~ SPEC-024 infrastructure (Docker compose, Postgres 16-alpine, Telegram dev/cron/trading bot separation, KIS API mock).
- Target file: `src/trading/screener/daily_screen.py` (single-file change).
- Data dependency: `data/blocked_tickers.json` (produced by SPEC-019 refresh cron, written before `35 6 * * 1-5` screener invocation).
- Output contract: `data/screened_tickers.json` schema unchanged.
- Consumer: micro persona (SPEC-018-aware), unaffected by this change beyond input cardinality.
- Test scope: existing tests under `tests/screener/` must keep passing; one new unit test covering the blocked filter behaviour.

---

## Assumptions

- A-1: `data/blocked_tickers.json` is refreshed by SPEC-019's cron schedule and present on disk by the time `daily_screen.py` runs at 06:35 KST. If absent, graceful degrade per REQ-025-3 applies.
- A-2: The `blocked` field is a dict keyed by 6-digit ticker code (KRX format), matching the keys already used by `daily_screen.py`. No code-normalisation step is needed.
- A-3: The `date` field is the date the file was last refreshed. Mismatch with today's date in KST is treated as staleness (REQ-025-3).
- A-4: The screener will continue to consume the KOSPI200 ∪ KOSDAQ150 universe as today. No universe expansion happens in this SPEC.
- A-5: Post-filter candidate count can legitimately fall below historical norms during heavily-overheated sessions. Recovery via expanded universe is deferred to SPEC-TRADING-026 (see REQ-025-5).
- A-6: All existing micro / decision / risk persona prompts are unchanged. This SPEC only narrows the input to the persona pipeline.
- A-7: KST is the canonical timezone for the `date` comparison, consistent with the rest of the codebase.

---

## Requirements (EARS)

### REQ-025-1 (P0, Event-driven) — Load blocked list at screener start

**WHEN** `daily_screen.py` begins a screening run, **THEN** the system SHALL load `data/blocked_tickers.json` from disk and parse it into an in-memory set of blocked ticker codes prior to candidate scoring.

### REQ-025-2 (P0, State-driven) — Exclude blocked tickers from candidate pool

**WHILE** scoring candidates, **THEN** the system SHALL exclude any ticker present in the loaded blocked set from LLM scoring and from the final output. No penalty / weighted-score variant — pure set difference.

### REQ-025-3 (P1, Unwanted) — Graceful degrade on missing or stale blocked file

**IF** `data/blocked_tickers.json` is missing on disk **OR** its `date` field is not equal to today's date in KST, **THEN** the system SHALL log a WARNING (with the exact failure reason — `missing` or `stale: file_date=… today=…`) and proceed with an empty blocked set. The screener SHALL NOT raise an exception, and SHALL NOT block the daily cycle.

### REQ-025-4 (P0, Ubiquitous) — Output guarantee

The system SHALL ensure that `data/screened_tickers.json` contains zero tickers from the loaded blocked set on every successful screening run. A post-write assertion (or equivalent integrity check) SHALL verify this invariant before the file is considered final.

### REQ-025-5 (P2, Event-driven) — Low-candidate warning

**IF** the post-filter candidate count falls below `min_candidates_warn` (default 5, configuration-only knob — no code-path change), **THEN** the system SHALL log a WARNING indicating the low yield. Recovery via universe expansion is deferred to SPEC-TRADING-026 and is explicitly out of scope here.

---

## Specifications

### Implementation Notes

- **Scope**: Single file — `src/trading/screener/daily_screen.py`. No new modules, no new config sections.
- **Load step**: Add `_load_blocked_set() -> set[str]` helper that reads `data/blocked_tickers.json`, validates the `date` field against today's KST date, and returns `set(parsed["blocked"].keys())`. On missing file or stale date, return `set()` and emit a WARNING via the existing logger used elsewhere in `daily_screen.py`.
- **Filter step**: Apply the blocked set as a set-difference filter against the candidate pool **before** LLM scoring — this is the key efficiency point (avoid spending LLM tokens on tickers that will be discarded).
- **Logging contract**: Emit one INFO line `filtered N tickers from blocked list` where N is `len(candidates_before) - len(candidates_after)`. Emit one WARNING per failure mode (missing / stale / low-yield).
- **Post-write check**: After serialising `screened_tickers.json`, verify `set(output) & blocked_set == ∅`. If non-empty, log an ERROR and refuse to write (defensive — should be unreachable given REQ-025-2, but ensures REQ-025-4 invariant is observable).
- **No persona prompt changes**: micro and decision personas remain untouched.
- **No fallback expansion**: deliberately deferred to SPEC-TRADING-026 to keep this change minimal.

### Configuration (no new file, in-source defaults acceptable)

| Knob | Default | Rationale |
|---|---|---|
| `min_candidates_warn` | 5 | Triggers REQ-025-5 WARNING; tuneable later without code change. |
| `blocked_path` | `data/blocked_tickers.json` | Matches SPEC-019 refresh layer output path. |

### Non-goals (Out of Scope)

- Universe fallback expansion when post-filter pool is too small — **deferred to SPEC-TRADING-026**.
- Micro persona prompt changes — the prompt already handles blocked tickers (SPEC-018) and does not need adjustment.
- Decision persona (박세훈) prompt changes — orthogonal to this SPEC.
- New configuration files / new config sections.
- Penalty-based scoring (e.g. weight blocked tickers downward instead of excluding) — rejected as more complex and less predictable than set-difference.
- Refreshing `blocked_tickers.json` from within the screener — that responsibility belongs to SPEC-019's cron layer.
- Multi-source blocked lists (e.g. user-defined exclusions) — single-source from `data/blocked_tickers.json` only.

---

## Acceptance Criteria

Detailed acceptance scenarios live in `acceptance.md`. Summary of the gates:

1. Given today's `blocked_tickers.json` (~57 tickers), running `daily_screen.py` produces `screened_tickers.json` with **zero** blocked tickers (REQ-025-4).
2. All existing tests under `tests/screener/` continue to pass (no regression).
3. A new unit test exercises the blocked filter against a mocked `blocked_tickers.json` and asserts the set-difference behaviour (REQ-025-2).
4. Log output includes the exact line `filtered N tickers from blocked list` where N matches the difference between pre- and post-filter candidate counts.
5. With `blocked_tickers.json` deleted, screening still succeeds (no exception) and logs the missing-file WARNING (REQ-025-3).
6. With `blocked_tickers.json` present but `date` set to a prior day, screening still succeeds and logs the stale-file WARNING (REQ-025-3).
7. When post-filter candidate count falls below 5, the low-yield WARNING is emitted (REQ-025-5).

---

## Related SPECs

- **SPEC-TRADING-018** — micro persona blocked-ticker awareness. This SPEC reuses the same data source (`blocked_tickers.json`) and extends awareness one stage upstream into the screener.
- **SPEC-TRADING-019** — data refresh layer. This SPEC is a consumer of the refresh artifact; no changes to the refresh layer itself.
- **SPEC-TRADING-020** — DEFAULT_WATCHLIST bias removal. Resolves the *fallback* arm of the same self-collision problem; this SPEC resolves the *primary* arm.
- **SPEC-TRADING-022 / 023** — universe discovery and auto-expansion. Operate on a different layer (data universe), unaffected by this SPEC.
- **SPEC-TRADING-026 (planned)** — universe fallback expansion for low-yield screening sessions. Picks up where this SPEC stops.

---

## Dependencies and Rollout

### Dependencies

- **REQUIRES**: SPEC-019 refresh cron must continue to produce `data/blocked_tickers.json` with a `date` field. Already in production since 2026-05-11.
- **COMPATIBLE-WITH**: SPEC-018 / 020 / 022 / 023 / 024 — backward compatible. No public contract changes.

### Rollout Plan

- **Pre-deploy gate**: Bug-reproduction unit test added and passing (blocked filter), all existing `tests/screener/` tests green.
- **Phase 1 (1 day, target 2026-05-20 KST)**: Implementation via `/moai:2-run SPEC-TRADING-025`. Single-file change + new unit test. Merge to `main`, redeploy.
- **Phase 2 (1 trading day, target 2026-05-21 KST)**: Live observation of the 06:35 KST run. Verify (a) screened output excludes all blocked tickers, (b) micro persona receives a non-zero candidate set in non-pathological sessions, (c) low-yield warnings fire correctly on heavily-overheated days.
- **Phase 3 (gate)**: If two consecutive trading days produce non-zero micro buy candidates, SPEC-025 graduates from `draft` → `done` and SPEC-026 (fallback expansion) is opened to address the residual low-yield scenarios.

---

## Open Questions (Flag Only)

- **Q-1 (Stale tolerance window)**: Should staleness allow a small grace period (e.g. weekend / holiday) where `date < today` is acceptable for a bounded number of days? Current design: strict equality, weekend handling deferred to SPEC-026 or a refresh-layer fix.
- **Q-2 (Low-yield action)**: Should the screener emit a Telegram WARNING when REQ-025-5 fires, or is a log line sufficient? Default: log only; Telegram alert deferred to a future ops-grade SPEC.
- **Q-3 (Post-write assertion failure)**: On an unexpected REQ-025-4 invariant violation (output ∩ blocked ≠ ∅), should the cycle abort or retry? Default: abort + ERROR log. Retry policy is overkill for a defensive check.

---

## Acceptance & Traceability

Detailed acceptance criteria: see `acceptance.md`.
Implementation plan and milestones: see `plan.md`.

Status: `draft` until implementation gate in `/moai:2-run SPEC-TRADING-025`.
