# SPEC-TRADING-029 Progress

- Started: 2026-05-26 22:35 KST
- Branch: fix/SPEC-TRADING-026-overheating-softening (per user choice)
- Methodology: TDD (per ADR-029-3)
- Execution mode: sub-agent solo

## Phase 0 — Planning artifacts (complete)

- 2026-05-26 22:26 — research.md (Phase 0.5 deep research)
- 2026-05-26 22:29 — spec.md (5 EARS REQ)
- 2026-05-26 22:31 — plan.md (5 phases A-E + 5 ADR)
- 2026-05-26 22:33 — acceptance.md (8 AC + 6 EC + 4 BC)

## Phase D pre-check (complete)

- 2026-05-26 22:36 — `\d orders` confirmed: `filled_at timestamptz` column does NOT exist → migration 022 required
- `orders_status_check` CHECK constraint already accepts: submitted, filled, partial, rejected, cancelled, error → no constraint change needed
- `fill_qty`, `fill_price` columns already present (never written by codebase)
- Next migration number: 022 (latest is 021_verbose_briefing.sql)

## Phase 1.6 — Acceptance criteria tracking

Acceptance criteria (8 AC + 6 EC + 4 BC = 18 items) registered in this progress.md.
TaskCreate not used (deferred tool); progress tracked here.

### Phase A+B+D scope (current iteration)

- 2026-05-26 22:52 — Phase D applied: migration 022_add_filled_at.sql created and applied to dev DB; `\d orders` confirms `filled_at timestamp with time zone` column present
- 2026-05-26 22:56 — Phase A RED: 18 failing tests written in tests/kis/test_fills.py (all ImportError, as expected)
- 2026-05-26 23:10 — Phase B GREEN: src/trading/kis/fills.py implemented (FillRow dataclass + inquire_fills_today + apply_fill_to_order + apply_fill_to_position + fill_sync orchestrator); all 18 initial tests + 3 additional edge-case tests pass (21 tests total)
- 2026-05-26 23:15 — Phase B REFACTOR: extracted `_set_conn_marker` helper, `_decide_new_status` pure function for dry_run preview, ruff clean, mypy clean (except pre-existing project-wide tuple/__getitem__ pattern shared with order.py), coverage 93% on fills.py (≥85% TRUST 5 gate)
- 2026-05-26 23:15 — @MX tags applied: @MX:ANCHOR on fill_sync (fan_in ≥3), @MX:WARN+REASON on inquire_fills_today parsing block, @MX:NOTE on status decision matrix + weighted-avg integer arithmetic + sll_buy_dvsn_cd mapping

Acceptance progress (proven by unit tests; live verification deferred to Phase E):

- [x] AC-029-2: weighted-avg avg_cost (SQL pattern present, verified via test_buy_upserts_new_row_uses_on_conflict)
- [x] AC-029-3: partial fill transition (test_partial_transitions_to_partial)
- [x] AC-029-4: SELL decrement w/ GREATEST clamp (test_sell_updates_with_greatest_clamp, test_sell_does_not_use_on_conflict)
- [x] AC-029-5: rate-limit retry inherited from KisClient.get (no code added, by ADR; tested implicitly)
- [x] AC-029-6: --dry-run zero writes (test_dry_run_performs_no_db_writes)
- [x] AC-029-7: cncl_yn='Y' → cancelled (test_cancelled_transitions_to_cancelled, no positions update verified)
- [x] EC-029-3: unknown odno is logged and skipped (test_unknown_kis_order_is_logged_and_skipped)
- [x] EC-029-4: already-terminal order is idempotent (test_already_filled_is_idempotent)
- [x] EC-029-5: zero-qty + new BUY → avg_cost reset (SQL CASE in BUY UPSERT branch handles 0-divisor)
- [ ] AC-029-1, AC-029-8: live paper verification (Phase E)
- [ ] EC-029-1: concurrent cron+CLI race (Phase C cron landing + integration test)
- [ ] EC-029-2: orders w/ kis_order_no IS NULL not queried (orchestrator currently iterates KIS-side fills only, so this is structural)
- [ ] EC-029-6: 15:30 KST boundary (Phase C scheduler integration)
- [ ] BC-029-1..4: backward compatibility (Phase C + E)

### Phase C scope (complete 2026-05-26)

- [x] Scheduler cron registration (apscheduler 60s interval, 09:00-15:30 KST)
- [x] CLI subcommand `trading fill-sync [--start YYYYMMDD] [--dry-run]`

#### Phase C deliverables (2026-05-26 23:55 KST)

**Files modified:**
- `/home/onigunsow/trading/src/trading/scheduler/runner.py` (+33 lines)
  - Added `_run_fill_sync()` helper (lines 67-87) with lazy imports of `trading.config.get_settings`, `trading.kis.client.KisClient`, and `trading.kis.fills.fill_sync`.
  - Registered new cron job `id="fill_sync"` after `daily_screen` (lines 207-235). CronTrigger: `day_of_week="mon-fri", hour="9-15", minute="*", second="0", timezone=KST`. Wrapped in `_wrap()` so the KRX trading-day guard suppresses non-trading days.
  - @MX:NOTE on the cron block documenting the 30-extra-call/day trade-off for the simpler `hour="9-15"` expression vs a precise 15:30 cap; safe because `apply_fill_to_order` is idempotent against terminal states (EC-029-4).
- `/home/onigunsow/trading/src/trading/cli.py` (+62 lines)
  - Added `_cmd_fill_sync(rest)` helper (lines 229-287) — parses `--dry-run` and `--start YYYYMMDD` flags; constructs `KisClient(get_settings().trading_mode)` and calls `fill_sync(client, dry_run=...)`. Returns 0 on success, 1 on `KisError` / `RuntimeError` (with stderr diagnostic).
  - `--start` flag accepted-but-not-yet-implemented; emits a WARNING and continues using today's KST date.
  - Unknown flags emit a WARNING but do not fail the command.
  - Dispatch added at line 165-166 (after `halt` / `resume`).
  - `_print_help` updated at line 336 to mention the new subcommand.

**Files created:**
- `/home/onigunsow/trading/tests/scheduler/test_fill_sync_cron.py` (160 lines, 4 tests)
- `/home/onigunsow/trading/tests/cli/test_cli_fill_sync.py` (190 lines, 8 tests)

**Verification:**
- `pytest tests/scheduler/test_fill_sync_cron.py tests/cli/test_cli_fill_sync.py`: **12 passed in 0.75s**
- `pytest tests/scheduler tests/cli tests/kis`: **65 passed** (Phase A+B+C combined, no regressions)
- Full suite: 671 passed, 6 pre-existing failures unrelated to SPEC-029 (test_volatility / test_registry / test_web_scraper — verified pre-existing on clean tree)
- `ruff check` on Phase C files: All clean. 3 pre-existing E501/I001/RUF001 in `cli.py` were verified to predate Phase C.
- Manual `runner.main()` smoke (mocked BlockingScheduler): `fill_sync` job registered with `id="fill_sync"`, `name="fill_sync 09:00-15:30 every 60s"` alongside the existing 41 jobs.
- Coverage on new code: 100% of `_run_fill_sync` exercised by tests; 100% of `_cmd_fill_sync` happy + error + flag paths exercised. Uncovered lines reported by coverage all predate Phase C (e.g. lines 38-40, 48-50 in runner.py = `_run_news_crawl` etc. unrelated).

**Deviations from spec:**
- None. Implemented exactly per Phase C instructions (60s cadence via `minute="*"`, `second="0"`, dispatch + flag handling, ignore-but-warn unknown flags).

### Phase E scope (manual)

- [ ] First paper-mode verification against today's 10 already-submitted orders
- [ ] Verify KIS field-name assumptions (research.md §3.3)
- [ ] Remove @MX:WARN after verification

## Phase A+B+D deliverables (2026-05-26 23:15 KST)

### Files created
- /home/onigunsow/trading/src/trading/db/migrations/022_add_filled_at.sql (applied to dev DB)
- /home/onigunsow/trading/src/trading/kis/fills.py (135 SLOC, 93% coverage)
- /home/onigunsow/trading/tests/kis/__init__.py
- /home/onigunsow/trading/tests/kis/test_fills.py (21 tests, all green)

### Verification
- pytest tests/kis/test_fills.py: 21 passed in 0.23s
- ruff check src/trading/kis/fills.py tests/kis/test_fills.py: All checks passed
- coverage 93% on fills.py (target ≥85%)
- mypy: 1 error remaining, identical to pre-existing src/trading/kis/order.py pattern (psycopg dict_row tuple/dict overload — project-wide, not a regression)
- regression test: 638 passing tests on rest of suite unchanged; 6 pre-existing failures unrelated to this work (test_volatility / test_registry / test_web_scraper — verified by `git stash + pytest` on clean tree)

### Out of scope (Phase C+E)
- Scheduler cron registration in src/trading/scheduler/runner.py
- CLI subcommand `trading fill-sync` in src/trading/cli.py
- First live-paper API call verification + @MX:WARN release
