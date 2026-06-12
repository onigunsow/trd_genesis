---
id: SPEC-TRADING-043
version: 0.1.0
status: draft
created: 2026-06-13
updated: 2026-06-13
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "News-import dead-fallback elimination (cli_only_mode) + proactive KIS TPS governance for exit-watchdog reliability"
related_specs:
  - SPEC-TRADING-016   # cli_only_mode / news intelligence — block_if_cli_only_mode is the predicate reused by the news fallback guard
  - SPEC-TRADING-014   # host export/import handshake — the import path whose fallback branch is dead code under cli_only_mode
  - SPEC-TRADING-029   # balance-reconcile fill sync — fill_sync is one of the concurrent KIS callers that breach the TPS cap
  - SPEC-TRADING-033   # position_watchdog — blind during TPS-breach windows (the latent exit-miss risk)
  - SPEC-TRADING-042   # broker-truth single ledger / exit reliability — TPS governance protects the freshly-centralized KIS source of truth
---

# SPEC-TRADING-043 — News-import dead-fallback elimination + proactive KIS TPS governance

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-06-13 | 0.1.0 | Initial draft from a last-week production log audit (2026-06-08 .. 2026-06-12), bundling two defects. **Concern A (P1):** under standing `cli_only_mode`, the news-intelligence import path's Haiku-API fallback is guaranteed-to-fail dead code. The 08:15 KST import slot (after the 08:10 host CLI run) logs `import_results: no results file found` → `scheduled_import` (scheduler.py:195-206) unconditionally calls `analyze_articles()` → `_call_haiku` (analyzer.py:213, `@block_if_cli_only_mode`) RAISES → analyzer's try/retry (analyzer.py:628-638) emits ~20 ERROR/WARN lines/day. Low data loss (re-processed next slot) but persistent ERROR-level log pollution + ~3h delay. **Concern B (P2):** `kis/client.py` already has REACTIVE per-call rate-limit retry (L94-131) but NO PROACTIVE pacing/coordination; concurrent callers (SPEC-042/029 fill_sync, SPEC-033 position_watchdog, tools.executor get_portfolio_status) breach the broker per-second cap (`KisError ... 초당 거래건수 초과`: 23 on 6/9, 1 on 6/10) → balance reads fail → watchdog goes blind (`could not read balance — skipping poll` ×9; `portfolio value read failed — trim disabled` ×20). Zero exits actually fired last week (no proven miss), but a stop inside a blind window would be missed — latent risk for the SPEC-042 broker-truth foundation. Reproduction-first TDD; Concern B mockable via fake clock / call-counter. No migration anticipated. Develop on a dedicated branch (current branch `fix/SPEC-TRADING-026-overheating-softening` is stale/unrelated). — 2026-06-13 | onigunsow |

---

## Overview (Environment & Assumptions)

### Environment
- Paper (모의) automated trading in operation; LIVE transition imminent (per SPEC-042).
- `cli_only_mode` is the system's standing mode (news/personas driven by the free host Claude CLI).
- News intelligence uses the SPEC-014 export/import handshake: container exports
  `pending_analysis.json` → host cron runs the Claude CLI → writes `analysis_results.json` →
  container `news_import` job imports it. 6 import slots/day; 5 succeed, 1 (08:15 KST) misses.
- KIS account (paper VTTC8434R / live TTTC8434R) reports holdings via `inquire-balance`. Multiple
  independent jobs poll KIS with no shared throttle.

### Assumptions
- The existing `cli_only_mode` predicate (`block_if_cli_only_mode` / `get_system_state()`,
  base.py:78-118) is the single source of truth for mode detection and is reused verbatim.
- The host-CLI import success path (`import_host_results` > 0) and its file-unlink semantics are
  correct and must remain unchanged.
- The missed-slot articles are re-processed idempotently by the next successful import slot, so
  skipping the fallback causes no data loss.
- KIS balance/portfolio reads can be both **paced** (a process-wide minimum-interval/token-bucket
  gate) and **shared** (a short-TTL read-through cache) without serving stale exit decisions.
- A proactive pacer keeps the aggregate request rate below the broker cap; the existing reactive
  retry remains as a residual-burst safety net.
- Pacing changes only the *timing* of KIS requests; it must not change order semantics or any
  trading-mode / `live_unlocked` gate.

---

## Requirements (EARS) — 2 groups → A (news import), B (KIS TPS)

### Group A (REQ-043-A) — News-import dead-fallback elimination under cli_only_mode

- **REQ-043-A1 (State-driven, graceful no-fallback):**
  IF host CLI results are absent AND `cli_only_mode` is active, THEN the import path
  (`scheduled_import`) shall NOT invoke `analyze_articles()` / `_call_haiku`; it shall return
  gracefully and leave the articles pending for the next import slot.

- **REQ-043-A2 (Event-driven, INFO not ERROR):**
  WHEN the import path skips the fallback under cli_only_mode (REQ-043-A1), THEN it shall log at
  INFO level (not WARNING/ERROR) and shall NOT emit the recurring `No host CLI results found —
  falling back to Haiku API` warning nor the `Haiku batch failed` ERROR lines.

- **REQ-043-A3 (Ubiquitous, success path preserved):**
  The system shall leave the host-CLI import success path (`import_host_results` returning a
  positive count) and its file-unlink semantics byte-for-byte unchanged.

- **REQ-043-A4 (State-driven, fallback retained when sanctioned):**
  IF `cli_only_mode` is NOT active AND host CLI results are absent, THEN the existing Haiku-API
  fallback shall remain available (the guard narrows the fallback to the sanctioned case only).

- **REQ-043-A5 (Unwanted, single mode source):**
  The system shall NOT introduce a second mode-detection mechanism; the fallback guard shall reuse
  the same `cli_only_mode` predicate as `block_if_cli_only_mode`, including its fall-open behavior
  on `get_system_state()` failure.

- **REQ-043-A6 (Optional, slot-timing hardening):**
  Where feasible, the system may widen the single 08:15 KST import slot's delay so it reliably
  trails the 08:10 host CLI run, reducing the recurring miss. (Optional; REQ-043-A1 is the core fix.)

### Group B (REQ-043-B) — Proactive KIS TPS governance

- **REQ-043-B1 (Ubiquitous, process-wide pacing gate):**
  The system shall enforce a process-wide proactive pacing gate (minimum-interval / token-bucket)
  in the KIS client so that concurrent callers serialize beneath the broker's per-second
  transaction cap.

- **REQ-043-B2 (Event-driven, shared read within window):**
  WHEN multiple callers (SPEC-042/029 reconcile/`fill_sync`, SPEC-033 `position_watchdog`,
  `tools.executor get_portfolio_status`) request a balance/portfolio read within a short window,
  THEN they shall reuse a single read-through cached read (few-second TTL) rather than issuing
  duplicate `inquire-balance` calls.

- **REQ-043-B3 (Ubiquitous, reactive retry retained):**
  The system shall retain the existing reactive rate-limit retry (`_is_rate_limited` + backoff,
  client.py L94-131) as a residual-burst safety net beneath the proactive pacer.

- **REQ-043-B4 (State-driven, watchdog safe degradation):**
  IF a balance read still fails despite pacing, THEN the watchdog shall continue to degrade safely
  (skip the poll, disable trim for that poll) — but the rate of forced skips attributable to TPS
  shall drop to approximately zero.

- **REQ-043-B5 (Unwanted, no semantic/timing harm to orders):**
  The pacing gate shall NOT change order semantics, the `live_unlocked`/trading-mode gates, nor
  unduly delay order-submission TRs during exit execution; it governs request *timing* only.

- **REQ-043-B6 (Trackable, testability & observability):**
  The pacer shall accept an injectable clock/sleep so its behavior is deterministically testable
  via a fake clock and a call-counter (no live broker calls in tests), and pacing/cache events
  shall be observable in logs.

---

## Specifications

- **A — guard placement:** insert the `cli_only_mode` check in `scheduled_import`
  (scheduler.py:195) before the fallback branch; reuse `get_system_state()` / the same predicate
  as `block_if_cli_only_mode`. On skip: INFO log (e.g. `news_import: cli_only_mode active and no
  host results — deferring to next slot`), optional `NEWS_INTEL_IMPORT_DEFERRED` audit (Q-A2).
- **A — success path:** `import_host_results` (analyzer.py:795) and unlink (L895-898) unchanged.
- **A — optional slot timing:** widen the 08:15 KST import slot in runner.py so it trails the
  08:10 host run (Q-A1) — optional hardening, not required for REQ-043-A1.
- **B — pacer:** process-wide minimum-interval / token-bucket gate in `kis/client.py`, shared by
  all callers (module-level singleton or injected, Q-B4). Strategy/interval finalized in run by
  measuring observed concurrency (Q-B1). Granularity (all TRs vs inquiry/balance only) per Q-B3.
- **B — cache:** read-through cache (few-second TTL) for `account.balance()` / portfolio-status
  shared by reconcile + watchdog + executor (scope/TTL per Q-B2).
- **B — retry:** keep reactive retry (client.py L94-131) under the pacer (REQ-043-B3).
- **B — measurable target:** zero `초당 거래건수 초과` errors over a representative trading day;
  zero `could not read balance` skips attributable to TPS.
- **Migration:** none anticipated (in-process cache; fallback guard reads existing
  `system_state`). Reserve **031** only if a persisted pacing metric is later required
  (current latest 029; 027 vacant, 030 reserved by SPEC-040 / unused).

## @MX annotations (targets)

- `@MX:ANCHOR` — KIS client **pacing gate** (high fan_in): `fill_sync`/reconcile (SPEC-042/029),
  `position_watchdog` (SPEC-033), and `tools.executor get_portfolio_status` all depend on it.
  Invariant: every KIS request passes through the gate; the gate keeps aggregate rate below the
  broker cap; an injectable clock preserves deterministic testing.
- `@MX:NOTE` — `scheduled_import` fallback guard: documents that the Haiku fallback is sanctioned
  only when cli_only_mode is OFF, and that the guard reuses the `block_if_cli_only_mode` predicate
  (single mode source, fall-open on DB failure).

## Traceability

| REQ | Group | Reused asset | Verification (acceptance) |
|---|---|---|---|
| REQ-043-A1~A2 | A news import | `scheduler.scheduled_import` (L195-206), `analyzer._call_haiku` (L213), `base.block_if_cli_only_mode` | AC-1 |
| REQ-043-A3~A4 | A news import | `analyzer.import_host_results` (L795/L895), fallback branch | AC-1 |
| REQ-043-A5 | A news import | `base.get_system_state` / cli_only_mode predicate | AC-1 |
| REQ-043-A6 | A news import | `runner.py` import-slot schedule | AC-1 (optional) |
| REQ-043-B1~B3 | B KIS TPS | `kis/client.py` get/post + RATE_LIMIT_* (L26-131), pacing gate (new) | AC-2 |
| REQ-043-B2 | B KIS TPS | `kis/account.balance()`, read-through cache (new) | AC-3 |
| REQ-043-B4 | B KIS TPS | `position_watchdog.poll_position_watchdog` | AC-2, AC-4 |
| REQ-043-B5~B6 | B KIS TPS | trading-mode gates; injectable clock; logs | AC-2~4 |
