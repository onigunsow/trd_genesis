# SPEC-TRADING-043 — Implementation Plan

> No code written here. This document captures run-phase plan, ADRs, and risks.
> TDD mode (RED-GREEN-REFACTOR); both concerns are reproduction-first.

## Architecture Decision Records (ADRs)

### ADR-1 (A): Guard the existing fallback, do not delete it
- **Decision:** Keep the Haiku-API fallback path intact but gate it on `cli_only_mode`. When
  cli_only_mode is active and host results are absent, `scheduled_import` returns gracefully
  (INFO log) without calling `analyze_articles()`.
- **Why:** The fallback is legitimate when cli_only_mode is OFF (a genuine degraded mode). The
  defect is only that it runs unconditionally under cli_only_mode, where it is guaranteed to fail.
  Deleting it would remove a sanctioned safety net; guarding it removes the dead-code failure
  while preserving the sanctioned case (REQ-043-A4).

### ADR-2 (A): Single mode source — reuse the block_if_cli_only_mode predicate
- **Decision:** The guard reads cli_only_mode via the same `get_system_state()` predicate used by
  `block_if_cli_only_mode` (base.py:78-118), including its fall-open behavior on DB failure.
- **Why:** Two divergent mode checks would drift and reintroduce the bug. Fall-open is important:
  a DB outage must not wedge the import path — if state is unreadable, behavior matches today
  (fallback may run), which is acceptable because that is a rare degraded case, not the standing one.

### ADR-3 (A): Slot-timing fix is optional hardening, not the core fix
- **Decision:** The core requirement is the graceful guard (REQ-043-A1). Widening the 08:15 import
  slot to reliably trail the 08:10 host run is optional (REQ-043-A6).
- **Why:** Even with perfect slot timing, a host run can occasionally be late; the guard is the
  robust fix. Slot widening only reduces *frequency* of the miss, not its failure mode.

### ADR-4 (B, KEY): Proactive pacing as the primary control; reactive retry as safety net
- **Decision:** Add a process-wide pacing gate (minimum-interval / token-bucket) in `kis/client.py`
  that all KIS requests pass through, keeping the *aggregate* rate below the broker cap. The
  existing reactive per-call retry (client.py L94-131) is retained beneath it.
- **Why:** The root cause is the *absence of coordination* among concurrent callers, not the
  absence of retry. Retry alone cannot prevent a breach because each caller retries independently
  after it is already rejected; under sustained pressure all retries exhaust and the error surfaces.
  A shared proactive gate prevents the breach in the first place.
- **trade-offs (explicit):**
  - *throughput vs cap-safety:* pacing serializes requests, adding latency. Mitigate by pacing at
    an interval just below the cap and by sharing reads (ADR-5) to cut total request volume.
  - *order-submission latency:* exit-execution order TRs must not be unduly delayed. Granularity
    (pace all TRs vs inquiry/balance only) is decided in run (Q-B3) so stop-loss submission is
    not throttled behind balance polls.

### ADR-5 (B): Read-through cache to eliminate duplicate balance reads
- **Decision:** A short-TTL (few-second) read-through cache for `account.balance()` /
  portfolio-status, shared by reconcile + watchdog + executor, so overlapping polls reuse one read.
- **Why:** Much of the concurrency is *redundant* — three callers each reading balance within the
  same window. Caching removes duplicates, reducing the request rate before the pacer even acts.
- **Constraint:** TTL must be short enough that exit decisions never act on stale holdings
  (finalized in run, Q-B2).

### ADR-6: reproduction-first TDD, deterministic Concern-B tests
- **Decision:** Each concern gets a failing reproduction test before the fix. Concern B uses a
  fake/monotonic clock and a call-counter (injectable into the pacer/client) — no live KIS calls,
  no wall-clock sleeps.
- **Why:** Project HARD rule (reproduction-first). Deterministic timing tests are the only way to
  assert "paced below N requests/sec" reliably in CI.

## Milestones (priority-based, no time estimates)

- **Primary Goal:** REQ-043-A1~A5 — guard the news-import fallback under cli_only_mode (INFO, no
  `_call_haiku`, success path unchanged). Removes the daily ~20-line ERROR/WARN pollution. (P1)
- **Secondary Goal:** REQ-043-B1, B3, B5, B6 — process-wide pacing gate in the KIS client
  (`@MX:ANCHOR`), reactive retry retained, injectable clock. Removes the TPS-breach errors. (P2)
- **Tertiary Goal:** REQ-043-B2, B4 — read-through cache shared by reconcile/watchdog/executor;
  confirm watchdog forced-skips drop to ~0.
- **Optional Goal:** REQ-043-A6 — widen the 08:15 import slot to trail the host run.

## Technical approach

1. **A — fallback guard:** in `scheduler.scheduled_import` (L195), before the fallback branch,
   evaluate cli_only_mode via the shared predicate; if active → INFO log + (optional) audit +
   `return` (articles stay pending). Leave `import_host_results` and unlink untouched.
2. **B — pacer:** introduce a module-level pacing gate in `kis/client.py` (shared singleton or
   injected) that `get()`/`post()` (or just inquiry TRs, Q-B3) call before issuing a request.
   Accept an injectable clock/sleep (REQ-043-B6). Keep `_is_rate_limited` retry underneath.
3. **B — cache:** wrap `account.balance()` / portfolio-status in a read-through cache (TTL) that
   `fill_sync`, `position_watchdog`, and `tools.executor get_portfolio_status` all consult.
4. **Verification:** reproduction tests (RC-A, RC-B) first; then assert paced rate and zero
   blind-skip under simulated concurrency.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Guard accidentally suppresses the *legitimate* fallback (cli_only_mode OFF) | Guard checks mode explicitly; REQ-043-A4 keeps fallback when mode OFF; AC-1 asserts both branches |
| DB outage makes mode unreadable → guard misbehaves | Reuse fall-open behavior of `block_if_cli_only_mode`; on read failure behave as today (AC-1 edge) |
| Pacing over-throttles order-submission during exits | Decide granularity in run (Q-B3); pace inquiry/balance TRs, not exit order TRs (REQ-043-B5) |
| Read-through cache serves stale holdings to an exit decision | Short TTL (few seconds), finalized in run (Q-B2); exit decisions tolerate the chosen TTL (AC-3) |
| Pacer interval too conservative → throughput collapse | Tune interval just below cap by measuring observed concurrency (Q-B1); cache cuts volume first |
| Concern-B tests flaky on wall-clock | Injectable fake clock + call-counter, no real sleeps (REQ-043-B6, AC-2) |
| Two mode sources drift | Single predicate reused (REQ-043-A5, ADR-2) |

## Migration
- None anticipated. The TTL cache is in-process; the fallback guard reads existing `system_state`.
- Reserve **031** only if a persisted pacing/observability metric is later required (current
  latest 029; 027 vacant; 030 reserved by SPEC-040, unused).

## reproduction-first targets (TDD RED before GREEN)
- **RC-A reproduction:** with cli_only_mode active and no results file present, calling
  `scheduled_import()` currently invokes `analyze_articles()` → `_call_haiku` raises → ERROR/WARN
  logs emitted. RED test asserts (pre-fix) that `_call_haiku` is reached / ERROR is logged; GREEN
  asserts it is NOT reached and only an INFO line is emitted, with articles left pending (AC-1).
- **RC-B reproduction:** with the pacer absent and a fake clock, N concurrent callers issue KIS
  requests faster than the cap → a simulated `초당 거래건수 초과` / exhausted-retry path is hit and
  the watchdog records a forced skip. RED asserts the breach/skip; GREEN asserts the paced rate
  stays ≤ cap (call-counter under fake clock) and the watchdog reuses the cached read within TTL,
  yielding zero TPS-attributable skips (AC-2, AC-3, AC-4).
