# SPEC-TRADING-043 — Acceptance Criteria

> Given-When-Then. AC-1 covers Concern A (RC-A reproduction); AC-2..AC-4 cover Concern B
> (RC-B reproduction). TDD mode: each AC's reproduction (RED) test precedes the fix (GREEN).
> Concern B is fully mockable — fake clock + call-counter, no live broker calls.

## AC-1 — News-import fallback is dead-code-free under cli_only_mode [REQ-043-A] (RC-A reproduction)

- **Given** `cli_only_mode` is active AND the host CLI results file is absent (the daily 08:15 KST
  miss: `import_results: no results file found`).
- **When** `scheduled_import()` runs the import slot.
- **Then** it does NOT invoke `analyze_articles()` / `_call_haiku`; it logs at **INFO** (not
  WARNING/ERROR), emits no `No host CLI results found — falling back to Haiku API` warning and no
  `Haiku batch failed` ERROR lines, and the affected articles remain pending for the next slot.
- **And (success path preserved, REQ-043-A3)** when host results ARE present, `import_host_results`
  imports them and unlinks the files exactly as before (byte-for-byte unchanged behavior).
- **And (sanctioned fallback retained, REQ-043-A4)** when `cli_only_mode` is OFF and results are
  absent, the Haiku-API fallback still runs.
- **And (single mode source + fall-open, REQ-043-A5)** the guard uses the same `cli_only_mode`
  predicate as `block_if_cli_only_mode`; if `get_system_state()` itself fails, behavior falls open
  (matches today's behavior) rather than wedging the import path.
- **And (reproduction gate)** a RED test first demonstrates that, pre-fix, under cli_only_mode with
  no results file, `_call_haiku` is reached and ERROR/WARN lines are emitted; the fix turns this
  green (no `_call_haiku`, single INFO line, articles pending).
- **And (optional, REQ-043-A6)** if the 08:15 slot delay is widened, the slot reliably trails the
  08:10 host run (frequency of the miss drops). Optional — not required to pass AC-1.

## AC-2 — Proactive pacing keeps concurrent KIS callers below the broker TPS cap [REQ-043-B1/B3/B5/B6] (RC-B reproduction)

- **Given** a fake/monotonic clock and a request call-counter injected into the KIS client, and N
  concurrent callers (simulating SPEC-042/029 reconcile/`fill_sync`, SPEC-033 `position_watchdog`,
  `tools.executor get_portfolio_status`) issuing balance/inquiry requests.
- **When** the callers drive requests faster than the broker's per-second cap.
- **Then** the proactive pacing gate serializes them so the measured request rate (per the
  call-counter advanced by the fake clock) stays **≤ the configured cap** — zero simulated
  `초당 거래건수 초과` errors.
- **And (reactive retry retained, REQ-043-B3)** the existing `_is_rate_limited` + backoff path
  remains present as a residual-burst safety net beneath the pacer.
- **And (no order harm, REQ-043-B5)** order-submission TRs are not unduly delayed and no
  trading-mode / `live_unlocked` gate is altered (pacing governs timing only).
- **And (reproduction gate)** a RED test first shows that, without the pacer, the simulated
  aggregate rate exceeds the cap (breach / exhausted-retry path hit); the fix turns this green.

## AC-3 — Shared read-through cache eliminates duplicate balance reads [REQ-043-B2]

- **Given** the read-through cache (few-second TTL) is enabled and reconcile + watchdog + executor
  each request a balance/portfolio read within the same TTL window.
- **When** the three callers run within that window.
- **Then** exactly **one** underlying `inquire-balance` read is issued (the others reuse the cached
  value), verified via the call-counter; subsequent reads after TTL expiry issue a fresh read.
- **And (freshness)** the chosen TTL is short enough that exit decisions never act on stale
  holdings (the cache does not change which holdings an exit evaluates within the window).

## AC-4 — Watchdog forced skips attributable to TPS drop to ~zero [REQ-043-B4]

- **Given** pacing (AC-2) and the shared cache (AC-3) are active under simulated busy-window
  concurrency reproducing last week's load (TPS errors: 23 on 6/9, 1 on 6/10; balance-read skips:
  6/9=13, 6/11=11, 6/12=4).
- **When** the position watchdog polls during the busy window.
- **Then** the count of `position_watchdog: could not read balance — skipping poll` and
  `portfolio value read failed — trim disabled this poll` events **attributable to TPS** is
  approximately **zero**.
- **And (safe degradation preserved)** if a balance read still genuinely fails, the watchdog still
  degrades safely (skips the poll / disables trim) rather than acting on missing data.

## Definition of Done
- [ ] AC-1 RC-A reproduction (RED) precedes fix → green: no `_call_haiku` under cli_only_mode,
      INFO-only log, articles pending, success path unchanged, sanctioned fallback retained.
- [ ] Fallback guard reuses the single `cli_only_mode` predicate (fall-open on DB failure).
- [ ] AC-2 RC-B reproduction (RED) precedes fix → green: process-wide pacing gate keeps aggregate
      KIS rate ≤ cap under fake clock + call-counter; reactive retry retained.
- [ ] Read-through balance/portfolio cache shared by reconcile + watchdog + executor (AC-3).
- [ ] Watchdog forced skips attributable to TPS ≈ 0; safe degradation preserved (AC-4).
- [ ] Pacing changes timing only — no order-semantics / `live_unlocked` / trading-mode changes.
- [ ] `@MX:ANCHOR` on the KIS pacing gate; `@MX:NOTE` on the fallback guard.
- [ ] No migration required (031 reserved only if a persisted pacing metric is later needed).
- [ ] (Optional) 08:15 import slot widened to trail the host run.

## Quality gates
- pytest coverage ≥ 85%; money/risk-adjacent and timing logic reproduction-first (RC-A, RC-B).
- ruff/black pass. EARS traceability maintained (spec ↔ acceptance).
- Concern-B tests deterministic: fake clock + call-counter, no live KIS calls, no wall-clock sleeps.
- Measurable target met: zero `초당 거래건수 초과` errors and zero TPS-attributable balance-read
  skips over a representative simulated trading day.
