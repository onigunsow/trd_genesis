# SPEC-TRADING-043 — Research (news-import dead fallback + KIS TPS governance)

> Pre-implementation investigation artifact. No code is written here.
> Every `file:line` citation was re-verified against the current tree on 2026-06-13.
> Source of evidence: last-week production log audit (2026-06-08 .. 2026-06-12), treated as ground truth.

## 0. One-line summary

Two independent defects surfaced by a production log audit, bundled because both are
"log pollution that masks a latent operational risk":
- **Concern A (P1):** under standing `cli_only_mode`, the news-intelligence import path's
  Haiku-API fallback is **guaranteed-to-fail dead code** — it raises ~20 ERROR/WARN lines/day.
- **Concern B (P2):** uncoordinated concurrent KIS callers breach the broker's per-second
  transaction cap, so balance reads fail and the exit watchdog goes **blind** — a latent
  miss-an-exit risk for a system that just centralized on KIS as single source of truth (SPEC-042).

---

## 1. Concern A — News-intelligence import fallback is dead code under cli_only_mode

### 1.1 Architecture (SPEC-014 export/import handshake)

Container exports `data/pending_analysis.json` → host cron `scripts/analyze_news.sh` runs the
Claude CLI (free, Max subscription) → writes `data/analysis_results.json` → container
`news_import` job reads it into DB, then unlinks the file.

Code path (verified):
- `src/trading/scheduler/runner.py:63` `_run_news_import()` → calls `scheduled_import()`.
- `src/trading/news/intelligence/scheduler.py:173` `scheduled_import()`:
  - L191 `imported = import_host_results()`.
  - L195 `if imported == 0 and not RESULTS_FILE.exists():` → fallback block.
- `src/trading/news/intelligence/analyzer.py:795` `import_host_results()`:
  - L803-805 `if not RESULTS_FILE.exists() ...: LOG.info("import_results: no results file found"); return 0`.
  - L904 success path logs `import_results: stored %d analysis results from host CLI`.
  - L895-898 unlinks `RESULTS_FILE` / `meta_file` / `PENDING_FILE` after a successful import.

### 1.2 The dead fallback (root cause)

`src/trading/news/intelligence/scheduler.py:195-206` (fallback block):
```
if imported == 0 and not RESULTS_FILE.exists():
    LOG.warning("No host CLI results found — falling back to Haiku API")     # L197
    audit("NEWS_INTEL_FALLBACK_HAIKU", ...)
    from trading.news.intelligence.analyzer import analyze_articles
    metrics = analyze_articles()                                             # L203
```
- This block has **NO `cli_only_mode` guard**. It unconditionally calls `analyze_articles()`.
- `analyze_articles()` (analyzer.py:562) batches articles and calls `_call_haiku()`
  (analyzer.py:631 first attempt, analyzer.py:637 retry).
- `_call_haiku` carries `@block_if_cli_only_mode` (analyzer.py:213). Under cli_only_mode this
  decorator **RAISES** `RuntimeError("cli_only_mode=True but ..._call_haiku attempted a direct
  Anthropic API call ... SPEC-TRADING-016 REQ-016-1-3")` (base.py:78-118).
- The analyzer wraps each `_call_haiku` in try/except with one retry (analyzer.py:628-638):
  attempt-1 logs `WARNING Haiku batch N failed (attempt 1)`, sleeps, attempt-2 logs
  `ERROR Haiku batch N failed (attempt 2)`. So every fallback invocation produces
  **2 log lines per batch × ~10 batches ≈ ~20 ERROR/WARN lines** plus the audit row.

### 1.3 Schedule and the single recurring miss

- 6 export slots and 6 import slots/day (runner.py ~L197-235). Host `analyze_news.sh` cron at
  :10 / :40; container import ~5 min later.
- Observed: 5 of 6 import slots/day succeed. **Exactly ONE slot/day** (the 08:15 KST import,
  after the 08:10 host run) logs `no results file found` → fallback → guaranteed Haiku failure.
- The single recurring miss strongly suggests the 08:15 import fires **before the 08:10 host
  run reliably finishes** (the morning batch is the largest). Started 2026-06-08 23:15 UTC,
  repeats daily.

### 1.4 cli_only_mode detection (for the fix)

- `block_if_cli_only_mode` (base.py:78) reads `system_state` via `get_system_state()` and treats
  both `cli_only_mode` (SPEC-016) and legacy `cli_personas_enabled` (SPEC-015) as equivalent
  (base.py:112-116). **Important:** it "falls open" — if `get_system_state()` itself throws, the
  wrapped function executes normally so a DB outage cannot wedge the only working path.
- The fix for Concern A must reuse the **same** cli_only_mode predicate so behavior is consistent
  (do not invent a second mode source).

### 1.5 Impact assessment

- Data loss: **low** — the missed articles remain pending and are re-processed by the next
  successful import slot (the export/import handshake is idempotent on `news_articles`).
- Real harm: persistent ERROR-level log pollution (~20 lines/day) that desensitizes operators,
  plus a ~3h analysis delay for that one slot. The Haiku direct-API fallback is **effectively
  dead** under cli_only_mode, which is the system's standing mode.

### 1.6 Desired behavior

When cli_only_mode is active AND host results are absent, the import path must NOT invoke
`_call_haiku`. It should log at INFO (not ERROR/WARN), return gracefully, and leave the articles
pending for the next import slot. The existing successful host-CLI import path must be unchanged.
Optionally, widen the single 08:15 slot's delay so it reliably trails the 08:10 host run — but
the **core** requirement is the graceful no-fallback behavior.

---

## 2. Concern B — KIS broker API TPS limit breached → balance reads fail → watchdog blind

### 2.1 Error and counts

- Error: `trading.kis.client.KisError rt_cd=1 msg='원장에서 허용 가능한 초당 거래건수를
  초과하였습니다.'` (exceeded allowed transactions-per-second).
- Counts: 23 on 2026-06-09, 1 on 2026-06-10.

### 2.2 Downstream failures (last week)

| Symptom | Count | Days |
|---|---|---|
| `fill_sync failed` | 24 | 6/9, 6/11, 6/12 |
| `intraday_adaptive failed` | 7 | — |
| `position_watchdog: portfolio value read failed — trim disabled this poll` | 20 | — |
| `position_watchdog: could not read balance — skipping poll` | 9 | 6/9=13*, 6/11=11, 6/12=4 |
| `Tool get_portfolio_status failed: ReadTimeout / KisError` | — | — |

(* the skip counts and TPS-error counts overlap across the same windows.)

### 2.3 Existing reactive handling (verified, NOT sufficient)

`src/trading/kis/client.py`:
- L26 `RATE_LIMIT_RETRIES = 4`, L27 `RATE_LIMIT_BACKOFF_SECONDS = 1.0`,
  L29 `RATE_LIMIT_MSG_CODES = {"EGW00201"}`.
- L94-97 `_is_rate_limited()` matches `rt_cd == "1"` and (`msg_cd in RATE_LIMIT_MSG_CODES` or
  `"초당 거래건수" in msg`).
- `get()` (L99-114) and `post()` (L116-131) each loop `RATE_LIMIT_RETRIES + 1` times with
  `RATE_LIMIT_BACKOFF_SECONDS * (attempt+1)` backoff.

The handling is **purely REACTIVE and per-call**: each individual call retries after it is
already rejected. There is **no PROACTIVE pacing and no cross-caller coordination**. Multiple
independent callers hit KIS concurrently, so the *aggregate* request rate exceeds the broker's
per-second cap even though each call retries — and under sustained pressure all `RATE_LIMIT_RETRIES`
are exhausted, surfacing the `KisError` to the caller (balance read fails → watchdog skips poll).

### 2.4 Concurrent callers (no shared throttle)

| Caller | Location | Cadence |
|---|---|---|
| `fill_sync` / intraday reconcile (SPEC-042/029) | `runner.py:_run_fill_sync` → `kis/fills.py fill_sync` | scheduler cycles |
| `position_watchdog` (SPEC-033) | `src/trading/watchers/position_watchdog.py poll_position_watchdog` | `*/5` |
| `get_portfolio_status` (tools) | `tools/executor` | on-demand / LLM tool calls |

These call `inquire-balance` / inquiry TRs independently with no shared gate, so during busy
windows they collide and breach the cap.

### 2.5 Impact assessment (HIGH — ties to SPEC-042)

During TPS-breach windows the position watchdog cannot read balance → stop-loss / take-profit /
trim evaluation is **blind**. Last week **zero exits** were actually triggered (327 polls; all
stop/take/trim/rotate = 0), so no missed exit is *proven*. But a stop condition occurring inside a
blind window would be missed — an unacceptable latent risk for a system that just centralized on
KIS as the single source of truth (SPEC-042). The watchdog already degrades safely (it skips the
poll), but the **rate** of forced skips must drop to ~0.

### 2.6 Desired behavior

Introduce PROACTIVE client-side TPS governance:
1. A **process-wide minimum-interval / token-bucket pacing gate** in the KIS client so concurrent
   callers serialize beneath the broker cap (kept *below* the reactive retry, which remains as a
   safety net).
2. And/or a **read-through cache** (few-second TTL) so reconcile + watchdog + executor share a
   single balance/portfolio read within a short poll window, eliminating duplicate
   `inquire-balance` calls.

Measurable target: zero `초당 거래건수 초과` errors over a representative trading day, and zero
`could not read balance` skips attributable to TPS.

---

## 3. Existing assets — reuse targets (do not reinvent)

| Asset | Location | Role in SPEC-043 |
|---|---|---|
| reactive rate-limit retry | `kis/client.py` `_is_rate_limited` + `get`/`post` (L94-131) | keep as safety net *under* the new proactive pacer |
| RATE_LIMIT_* constants | `kis/client.py` L26-29 | reuse codes/backoff; add pacing interval constant |
| balance read | `kis/account.py balance()` (VTTC8434R / TTTC8434R) | the read to be paced + cached |
| fill_sync / reconcile | `kis/fills.py fill_sync` ; `runner.py:_run_fill_sync` | caller of the paced/cached read |
| watchdog | `watchers/position_watchdog.py poll_position_watchdog` | caller; must reuse cached read |
| portfolio status tool | `tools/executor get_portfolio_status` | caller; must reuse cached read |
| cli_only_mode predicate | `personas/base.py block_if_cli_only_mode` / `get_system_state()` | reuse same predicate to guard the news fallback |
| host import path | `news/intelligence/scheduler.py scheduled_import` ; `analyzer.py import_host_results` | guard the fallback branch only; success path unchanged |

---

## 4. Key design principles (must be encoded)

1. **A — graceful no-fallback under cli_only_mode.** When host results are absent AND cli_only_mode
   is active, skip `analyze_articles()` entirely; log INFO; leave articles pending. Never invoke
   `_call_haiku`. Reuse the existing cli_only_mode predicate (single source of truth).
2. **A — success path untouched.** The host-CLI import path (`import_host_results` returning >0)
   and its file-unlink semantics are byte-for-byte unchanged.
3. **B — proactive pacing is process-wide and shared.** The pacing gate is a single high-fan-in
   chokepoint (reconcile, watchdog, executor all depend on it) → marked `@MX:ANCHOR`.
4. **B — reactive retry remains a safety net.** Proactive pacing keeps the aggregate rate below the
   cap; the existing retry stays for residual bursts.
5. **B — watchdog still degrades safely.** Skipping a poll on read failure remains, but forced
   skips attributable to TPS must drop to ~0.
6. **reproduction-first.** Both concerns get failing reproduction tests before any fix
   (project HARD rule; TDD mode). Concern B is mockable via fake clock / call-counter — no live
   broker calls in tests.

---

## 5. Constraints (mandatory)

- TDD mode → RED-GREEN-REFACTOR; reproduction test precedes each fix.
- Concern B tests must be deterministic: a fake/monotonic clock and a call-counter, not wall-clock
  sleeps or live KIS calls. The pacer must accept an injectable clock/sleep for testability.
- Do not alter `live_unlocked` / trading-mode gates; pacing is mode-agnostic and must not change
  order semantics (only the *timing* of requests).
- Reuse existing assets (§3). EARS requirement groups = 2 (A news-import, B KIS-TPS).
- No DB migration anticipated. The TTL cache is in-process; the news fallback guard reads existing
  `system_state`. (If, during run, a persisted pacing metric is desired, reserve migration 031 —
  but the default plan needs no migration.)

---

## 6. Open questions (deferred to run phase)

- Q-A1: Fix the fallback guard only, or also widen the 08:15 import slot delay so it reliably
  trails the 08:10 host run? (Core req = guard; slot-delay is optional hardening.)
- Q-A2: Should the graceful-skip emit an audit row (e.g. `NEWS_INTEL_IMPORT_DEFERRED`) in addition
  to the INFO log, for observability?
- Q-B1: Pacing strategy — global minimum-interval gate vs token-bucket; what interval/rate keeps
  the aggregate safely below the broker cap (measure against observed concurrency)?
- Q-B2: Read-through cache scope — balance only, or balance + portfolio-status; TTL length
  (a few seconds) that eliminates duplicate reads without serving stale exit decisions?
- Q-B3: Pacer granularity — pace all KIS GET/POST, or only inquiry/balance TRs? (Order-submit TRs
  must not be unduly delayed during exit execution.)
- Q-B4: Where does the pacer live so all three callers share one instance (module-level singleton
  in `kis/client.py` vs injected dependency)?
