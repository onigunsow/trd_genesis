---
id: SPEC-TRADING-017
version: 0.1.0
status: draft
created: 2026-05-10
updated: 2026-05-10
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "CLI-level root logger bootstrap (stdlib logging) for all long-running subcommands"
related_specs:
  - SPEC-TRADING-016
  - SPEC-TRADING-015
---

# SPEC-TRADING-017 -- CLI-level root logger bootstrap (stdlib logging) for all long-running subcommands

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-10 | 0.1.0 | Initial draft -- discovered during SPEC-016 Phase 1 redeploy verification on commit `4efb8c5` | onigunsow |

---

## Scope Summary

While verifying SPEC-TRADING-016 Phase 1 redeploy on commit `4efb8c5`, all four containers (`app`, `bot`, `scheduler`, `postgres`) came up healthy and the SPEC-016 redeploy guard (`HOST_BUILD_COMMIT == /app/.build_commit`) passed. However, `docker compose logs scheduler` and `docker compose logs bot` returned **empty output**, breaking observability for the SPEC-016 intraday cycle verification (no way to confirm the cycle ran from logs).

Investigation traced the empty logs to a **single, system-wide gap**: the entire codebase has no configured root logger when subcommands are invoked through the `trading` CLI.

### Root cause

- `src/trading/scheduler/runner.py:198-200` calls `logging.basicConfig(...)` only inside an `if __name__ == "__main__":` guard.
- The container entrypoint is `trading scheduler`, which routes through `src/trading/cli.py:108-110`:
  ```python
  if cmd == "scheduler":
      from trading.scheduler.runner import main as run
      run()
  ```
  So `runner.main()` is called as an imported function; the `__main__` guard never fires; no log handlers are registered. `LOG.info("trading scheduler starting (KST cron)")` and every other `LOG.*` call silently disappears into the void.
- The bot is identical: `cli.py:104-106` does `from trading.bot.telegram_bot import run; run()`, and `telegram_bot.py` has no logging configuration of its own.
- A repo-wide search confirms there is exactly **one** logging configuration call in `src/`, and it is the dead one in `runner.py`. The system effectively has no configured root logger for any code path that goes through the CLI dispatcher.
- `structlog==25.5.0` is in `pyproject.toml` and `uv.lock` but is **never imported anywhere** in `src/`. It is unused dead weight.

### Decision (already made by user)

- **Smallest possible change.** Add a single `logging.basicConfig(...)` call at the top of `cli.py:main()`, before any subcommand dispatch. All current and future subcommands (`scheduler`, `bot`, `daily-report`, `calendar`, etc.) automatically benefit.
- **No structlog adoption** in this SPEC. Structured/JSON logging, log shipping, log files, log rotation -- all deferred to a future SPEC.
- **Keep** the existing `runner.py` `__main__`-guarded `basicConfig` call as a fallback for direct module execution (`python -m trading.scheduler.runner`). It is harmless: stdlib `basicConfig` is a no-op when a handler is already configured. The TDD/DDD agent may remove it during implementation if clearly dead, at their discretion.

### Why now

This SPEC is a follow-up dependency-style addition discovered during SPEC-TRADING-016 Phase 1 verification. Without it:

- SPEC-016 Phase 1 acceptance criterion AC-1-2 (`docker compose logs scheduler --since 24h | grep "UndefinedError.*hold_warnings"` returns 0 hits) cannot be **positively** verified -- a 0-hit grep on an empty log stream is indistinguishable from a 0-hit grep on a working stream that simply has no errors.
- SPEC-016 Phase 1 AC-1-3 (`docker compose logs scheduler --since 24h | grep -i "anthropic.*RateLimitError\|429"` returns 0 hits) has the same problem.
- General debuggability of the live trading system is degraded: no way to confirm cron jobs fire, no trace of persona invocations, no evidence of API failures.

This is why SPEC-017 should land **before or alongside** the next redeploy that closes out SPEC-016 Phase 1.

---

## Environment

- Existing SPEC-001 ~ SPEC-016 infrastructure (Docker compose, Postgres 16-alpine, Telegram, KIS API).
- Entrypoint: `src/trading/cli.py:main()` (single CLI dispatcher for all subcommands).
- Long-running subcommands invoked from container entrypoints: `trading scheduler` (APScheduler cron loop), `trading bot` (Telegram long-poll listener).
- Short-lived subcommands invoked ad-hoc from host or container: `trading calendar`, `trading healthcheck`, `trading status`, `trading daily-report`, `trading build-context`, etc.
- Existing logging-related code (only one location): `src/trading/scheduler/runner.py:198-200` -- inside a `__main__` guard, never executed when reached via `cli.py`.
- Standard library `logging` module is the **only** logging dependency in scope. `structlog` is in `pyproject.toml` but unused in `src/` -- not touched by this SPEC.
- Container log capture: `docker compose logs <service>` reads from container stdout/stderr. Logs must therefore go to stdout (default `StreamHandler` behavior).

## Assumptions

- A-1: stdlib `logging.basicConfig(...)` writes to stderr by default; both stdout and stderr are captured by Docker's default `json-file` log driver, so `docker compose logs <service>` will surface either. Use the explicit `stream=sys.stdout` argument to keep operator expectations simple ("logs go to stdout").
- A-2: `logging.basicConfig(...)` is a no-op when the root logger already has at least one handler attached. This makes the bootstrap idempotent and safe even if called multiple times (e.g., once from `cli.main()` and once from `runner.main()`'s `__main__` block in a hypothetical direct-invocation scenario).
- A-3: All current `LOG = logging.getLogger(__name__)` usages across the codebase will inherit the root logger's handler and level once bootstrap runs -- this is standard Python logging behavior and does not require per-module changes.
- A-4: No third-party library currently configures the root logger ahead of `cli.main()` (verified by repo-wide search returning only the dead `runner.py` call). If a future dependency does, A-2's idempotency protects us.
- A-5: The `TRADING_LOG_LEVEL` environment variable is currently unused in the codebase, so introducing it does not collide with any existing convention.

---

## Goals

- **G-1**: Every long-running subcommand (`trading scheduler`, `trading bot`) emits its existing `LOG.info(...)` calls to stdout, visible via `docker compose logs <service>`.
- **G-2**: Operators can raise log verbosity to DEBUG without code changes by setting `TRADING_LOG_LEVEL=DEBUG` in the container environment.
- **G-3**: Bootstrap is **single-source-of-truth**: one call in `cli.py:main()`, no per-subcommand duplication, no per-module configuration.
- **G-4**: SPEC-016 Phase 1 acceptance criteria (AC-1-2, AC-1-3) become positively verifiable rather than vacuously satisfied by silence.
- **G-5**: Zero behavior change for short-lived subcommands (`trading calendar`, `trading status`, etc.) -- they must continue to run without raising. Logging bootstrap is additive only.

---

## Requirements

### REQ-017-1-1: Root logger configured before subcommand dispatch (Ubiquitous)

The `trading` CLI **shall** configure the root logger before dispatching to any subcommand.

Detail:

- (a) The bootstrap call **shall** execute as the **first non-trivial statement** of `cli.py:main()`, after argument normalization (`args = list(argv) if argv is not None else sys.argv[1:]`) and before any branching on `cmd`.
- (b) The bootstrap **shall not** be deferred into individual subcommand branches (no per-cmd duplication).
- (c) The bootstrap **shall** apply to all subcommands listed in `cli.py:_print_help()`, present and future, without per-subcommand modification.

**Files affected**:
- `src/trading/cli.py` (bootstrap call added at top of `main()`)

---

### REQ-017-1-2: Log output goes to stdout (Ubiquitous)

Log output **shall** go to stdout so that `docker compose logs <service>` captures it.

Detail:

- (a) The bootstrap **shall** explicitly pass `stream=sys.stdout` to `logging.basicConfig(...)` (rather than relying on the stderr default), to make the destination explicit and operator-obvious.
- (b) No log files, log rotation, or log shipping are configured by this SPEC. stdout-only.

**Files affected**:
- `src/trading/cli.py`

---

### REQ-017-1-3: Log level defaults to INFO, overridable via `TRADING_LOG_LEVEL` env var (Ubiquitous)

Log level **shall** default to `INFO` and **shall** be overridable via the `TRADING_LOG_LEVEL` environment variable.

Detail:

- (a) Accepted values: `DEBUG`, `INFO`, `WARNING`, `ERROR`. Comparison **shall** be case-insensitive (`"debug" == "DEBUG"`).
- (b) When `TRADING_LOG_LEVEL` is unset or empty, the level **shall** be `INFO`.
- (c) When `TRADING_LOG_LEVEL` is set to an unrecognized value (e.g., `TRACE`, `verbose`, `42`), the level **shall** fall back to `INFO` and **shall** emit a single `WARNING`-level log line to the root logger noting the invalid value. The bootstrap **shall not** raise.
- (d) The bootstrap **shall not** read any other environment variable (no `LOG_LEVEL`, no `PYTHONLOGLEVEL`, no `LOGLEVEL` -- explicitly `TRADING_LOG_LEVEL` only, to avoid namespace collisions).

**Files affected**:
- `src/trading/cli.py`

---

### REQ-017-1-4: Log format includes timestamp, level, logger name, and message (Ubiquitous)

Log format **shall** include timestamp, level, logger name, and message, matching the existing `runner.py` format string for continuity.

Detail:

- (a) The format string **shall** be exactly `"%(asctime)s %(levelname)s %(name)s %(message)s"` -- byte-identical to `src/trading/scheduler/runner.py:199`.
- (b) The `datefmt` argument **shall not** be set (use stdlib default; this matches current `runner.py` behavior and produces ISO-like `YYYY-MM-DD HH:MM:SS,mmm` timestamps).

**Files affected**:
- `src/trading/cli.py`

---

### REQ-017-1-5: Idempotent bootstrap, no duplicate handlers (State-Driven)

**If** a root logger handler is already configured (e.g., a test fixture or a library caller has preconfigured logging), **then** the bootstrap **shall not** duplicate handlers.

Detail:

- (a) The implementation **shall** rely on stdlib `logging.basicConfig(...)`'s built-in no-op-when-handlers-exist behavior. **Do not** call `logging.getLogger().handlers.clear()` or `force=True` -- both would silently override caller-supplied handlers and violate the contract.
- (b) When called from a pytest test that has already attached a `caplog` handler, the bootstrap **shall** leave `caplog`'s handler intact and add nothing.
- (c) When `cli.main()` is invoked twice in the same Python process (rare but possible in test scenarios), the second call **shall** also be a no-op for handler installation. The level, however, **may** be re-applied from `TRADING_LOG_LEVEL` -- this is acceptable and matches `basicConfig` semantics.

**Files affected**:
- `src/trading/cli.py`

---

### REQ-017-1-6: stdlib-only, no external configuration (Unwanted-behavior)

The bootstrap **shall not** depend on `structlog`, `dictConfig` files, YAML/JSON logging configuration, or any external configuration source other than the single `TRADING_LOG_LEVEL` environment variable.

Detail:

- (a) The implementation **shall** use only `import logging`, `import os`, and `import sys` from the standard library.
- (b) The implementation **shall not** import `structlog`, `loguru`, `python-json-logger`, or any other third-party logging library.
- (c) The implementation **shall not** read any logging configuration file (no `logging.conf`, no `logging.yaml`, no `pyproject.toml` `[tool.logging]` section).
- (d) Out of scope for this SPEC, explicitly: structured/JSON log output, log file destinations, log rotation, log shipping to external aggregators, per-logger level overrides, custom handlers, custom formatters beyond the format string in REQ-017-1-4.

**Files affected**:
- `src/trading/cli.py`

---

## Specifications

### S-1: Bootstrap call shape

The intended call shape (for reference; the TDD/DDD agent will produce the actual implementation):

```python
# At the top of cli.py:main(), after `args = ...` and before any `cmd ==` branching.
_bootstrap_logging()  # internal helper, defined in cli.py
```

`_bootstrap_logging()` resolves `TRADING_LOG_LEVEL` (case-insensitive, with INFO fallback for unset/invalid), then calls:

```python
logging.basicConfig(
    level=resolved_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
```

When the env var is invalid, it additionally emits a single `logging.warning(...)` line *after* `basicConfig` runs (so the warning itself is captured by the freshly-installed handler).

### S-2: Existing `runner.py:198-200` block

Current code:

```python
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    main()
```

Status under this SPEC: **harmless, may be retained or removed at implementer's discretion.** Idempotency (REQ-017-1-5) guarantees the call is a no-op once `cli.main()` has bootstrapped first. Removing it has the small upside of eliminating dead code; keeping it has the small upside of preserving a fallback for `python -m trading.scheduler.runner` direct invocation. No acceptance criterion depends on either choice.

### S-3: Subcommand coverage matrix

| Subcommand | Long-running? | Source of `LOG.*` calls | Visible in `docker compose logs` after fix? |
|---|---|---|---|
| `trading scheduler` | yes | `scheduler/runner.py`, `personas/*`, `risk/*`, `news/*`, `db/session.py`, etc. | **yes** -- primary acceptance target |
| `trading bot` | yes | `bot/telegram_bot.py`, `risk/emergency.py`, `db/session.py` | **yes** -- secondary acceptance target |
| `trading daily-report` | no (one-shot) | `reports/daily_report.py` | yes when run interactively |
| `trading calendar` | no (one-shot) | `scheduler/calendar.py` | yes when run interactively |
| `trading healthcheck` | no | `healthcheck.py` | yes when run interactively |
| `trading build-context [...]` | no | `contexts/build_*.py` | yes when run interactively |
| `trading crawl-news` | no | `news/crawler.py`, `news/health.py` | yes when run interactively |
| `trading analyze-news` | no | `news/intelligence/scheduler.py` | yes when run interactively |
| `trading status`, `trading halt`, `trading resume` | no | minimal | yes when run interactively |
| `trading paper-buy`, `trading check-kis`, `trading fetch-data`, `trading backtest`, `trading run-personas`, `trading migrate`, `trading news-health` | no | varies | yes when run interactively |

### S-4: Acceptance verification commands

After redeploy, an operator should be able to run, in order:

1. `docker compose logs scheduler --tail 50` -- non-empty, contains `trading scheduler starting (KST cron)`.
2. `docker compose logs bot --tail 50` -- non-empty, contains at least one INFO line from telegram_bot startup or python-telegram-bot's own logger.
3. Set `TRADING_LOG_LEVEL=DEBUG` in `docker-compose.yml` for the `scheduler` service, then `make redeploy`, then `docker compose logs scheduler --tail 100 | grep DEBUG` -- non-empty.
4. `docker compose exec app trading calendar 2026-05-11` -- exits 0, prints calendar table, does not raise.
5. `python -c "from trading.cli import main; main(['calendar', '2026-05-11'])"` from inside the container -- exits 0, root logger has at least one handler attached afterward.

---

## Acceptance Criteria

**AC-017-1** (covers REQ-017-1-1, REQ-017-1-2, REQ-017-1-4): After redeploy on a commit that includes this SPEC's implementation:

- [ ] `docker compose logs scheduler --tail 100` returns **non-empty** output and contains the literal string `trading scheduler starting (KST cron)`.
- [ ] Each captured log line matches the format `<timestamp> <LEVEL> <logger.name> <message>` (e.g., `2026-05-10 23:14:08,392 INFO trading.scheduler.runner trading scheduler starting (KST cron)`).
- [ ] No subcommand branches in `cli.py:main()` were modified to add their own `logging.basicConfig` -- the bootstrap is single-source.

**AC-017-2** (covers REQ-017-1-1, REQ-017-1-2): After redeploy, `docker compose logs bot --tail 100` returns **non-empty** output containing at least one INFO-level log line. (Acceptable sources: `trading.bot.telegram_bot`, `trading.db.session`, `httpx`, or any other module whose loggers fire during bot startup.)

**AC-017-3** (covers REQ-017-1-3): With `TRADING_LOG_LEVEL=DEBUG` set in the `scheduler` service environment in `docker-compose.yml` and the service restarted via `make redeploy` (or equivalent), `docker compose logs scheduler --tail 200` contains at least one line at `DEBUG` level. With the env var unset (or set to `INFO`), no `DEBUG`-level lines appear.

**AC-017-4** (covers REQ-017-1-3 fallback path): With `TRADING_LOG_LEVEL=BOGUS` (or any unrecognized value), the container starts successfully and the logs contain a single `WARNING`-level line noting the invalid value, after which `INFO`-level operation proceeds.

**AC-017-5** (covers REQ-017-1-1, G-5): Running `docker compose exec app trading calendar 2026-05-11` from the host (or `trading calendar 2026-05-11` from inside the container) exits 0 and prints the 14-day calendar table. No exception is raised by the logging bootstrap for short-lived subcommands.

**AC-017-6** (covers REQ-017-1-5, idempotency): A unit test calling `trading.cli.main(['calendar', '2026-05-11'])` confirms that `logging.getLogger().handlers` is non-empty after the call. A second call to `trading.cli.main([...])` in the same test process leaves the handler count unchanged (does not double).

**AC-017-7** (covers REQ-017-1-6, stdlib-only): A grep over the new code in `src/trading/cli.py` shows zero imports of `structlog`, `loguru`, or any third-party logging library. The only logging-related imports introduced are `import logging`, `import os`, and (if not already present) `import sys`.

---

## MX Tag Targets

The TDD/DDD agent **shall** add the following `@MX` annotations during implementation, per `.claude/rules/moai/workflow/mx-tag-protocol.md`:

| File | Function / Location | Tag | Rationale |
|---|---|---|---|
| `src/trading/cli.py` | `main()` | `@MX:NOTE` (with `@MX:SPEC: SPEC-TRADING-017`) | Root logger bootstrap for all subcommands; required for SPEC-016 cycle observability. Note that the bootstrap is the first non-trivial statement and must remain so. |
| `src/trading/cli.py` | `_bootstrap_logging()` (new helper) | `@MX:NOTE` (with `@MX:SPEC: SPEC-TRADING-017`) | Reads `TRADING_LOG_LEVEL` env var; idempotent via stdlib `basicConfig` semantics; do not add `force=True`. |
| `src/trading/scheduler/runner.py` | `if __name__ == "__main__":` block at lines 198-200 | `@MX:NOTE` (only if the block is retained) | Fallback `basicConfig` for direct module execution (`python -m trading.scheduler.runner`); no-op when CLI bootstrap has already run. May be removed; if retained, this NOTE marks the dead-code-but-intentional status. |

No `@MX:WARN` or `@MX:ANCHOR` tags are expected for this SPEC -- the change is small, low-risk, and not on a high-fan-in path beyond `cli.main()` itself (which is already a system entrypoint and does not need an additional ANCHOR for logging).

---

## Constraints / Non-Goals

- C-1: **No structlog adoption.** `structlog==25.5.0` remains in `pyproject.toml` and `uv.lock` untouched. Do not import it. Do not configure it. A future SPEC may revisit structured logging if/when the operator workflow demands JSON output for log aggregation.
- C-2: **No JSON logging, no log files, no log rotation, no log shipping.** All out of scope.
- C-3: **No changes to scheduler trigger times.** Cron schedules belong to SPEC-016; this SPEC touches only logging plumbing.
- C-4: **No changes to `telegram_bot.py` logging behavior** beyond what comes for free from root logger configuration. Do not add per-module `basicConfig`, do not add custom handlers, do not change log levels of `httpx` or `python-telegram-bot` (they will inherit the root level via stdlib defaults).
- C-5: **No changes to existing `LOG = logging.getLogger(__name__)` patterns** anywhere in `src/`. Module-level loggers are correct as-is; they will simply start producing visible output once the root is configured.
- C-6: **Single environment variable surface.** Only `TRADING_LOG_LEVEL` is read. Do not introduce per-module env vars (e.g., `TRADING_BOT_LOG_LEVEL`), do not read generic Python conventions like `LOG_LEVEL` or `PYTHONLOGLEVEL`.
- C-7: **Idempotency via stdlib semantics, not via custom logic.** Do not write `if not logging.getLogger().handlers: ...`; use `logging.basicConfig`'s built-in no-op-when-handlers-exist behavior. Do not pass `force=True`.
- C-8: User is a CLI beginner. The implementation **must** keep the existing CLI command surface unchanged -- no new flags, no new help text changes beyond what is incidental. `make redeploy` from SPEC-016 remains the single deployment entrypoint.

---

## Risks

| ID | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| R-1 | Adding bootstrap inadvertently changes log level for a third-party library that was previously silent (e.g., `httpx`, `apscheduler`), creating log noise | Low | Medium | Default level is `INFO`; most chatty libraries (e.g., `httpx`) emit at `DEBUG` and will remain quiet. If a specific library proves noisy at INFO, defer per-logger silencing to a follow-up SPEC rather than expanding scope here. |
| R-2 | `runner.py`'s `__main__` `basicConfig` is removed and a future operator runs `python -m trading.scheduler.runner` directly, expecting logs | Low | Low | The current container entrypoint is `trading scheduler` (via CLI), not direct module execution. If the implementer chooses to remove the dead block, document the removal in the commit message. If retained (recommended), no risk. |
| R-3 | Bootstrap call placement (top of `main()`) gets moved by a future refactor, silently re-breaking observability | Medium | Low | `@MX:NOTE` tag on `cli.main()` documents the constraint. AC-017-1 third bullet ("bootstrap is single-source") provides a regression check. |
| R-4 | `TRADING_LOG_LEVEL=DEBUG` in production produces large log volumes, filling disk via Docker's default `json-file` driver | Medium | Low | Documented behavior; operator opt-in only. Docker log rotation (`docker-compose.yml` `logging.options.max-size` / `max-file`) is out of scope for this SPEC but a known follow-up. |
| R-5 | A test that expects `caplog` to capture logs from `trading.cli` regresses because the bootstrap installs a competing handler | Low | Medium | REQ-017-1-5 (idempotency) ensures `caplog` (which attaches a handler before the test code runs) is not displaced. AC-017-6 explicitly tests the multi-call path. |
| R-6 | The `WARNING` line emitted on invalid `TRADING_LOG_LEVEL` (REQ-017-1-3 (c)) confuses operators who expected silent behavior | Low | Low | Acceptable trade-off: silent fallback would mask typos in `docker-compose.yml`. The single warning is informative and self-limiting (one line per process start). |

---

## Rollout Plan

1. (Plan) This SPEC document approved.
2. (Run, single agent, single iteration) TDD/DDD agent implements `_bootstrap_logging()` helper and the call site at the top of `cli.py:main()`, plus a unit test covering AC-017-6 (idempotency, handler count after `cli.main([...])`). Optional: remove the dead `__main__` block in `runner.py`.
3. (Verify locally) Operator runs `make redeploy` (per SPEC-016 runbook). Operator runs the five verification commands from S-4. All AC items pass.
4. (Sync) Documentation updated; the empty-log gap discovered during SPEC-016 verification is now closed. SPEC-016 Phase 1 acceptance criteria AC-1-2 and AC-1-3 can be re-verified with confidence (non-vacuous 0-hit greps).
5. No phased rollout; no feature flag; no migration. Single small change with single redeploy.

---

## Open Questions

- Q-1: Should the implementer **remove** the dead `if __name__ == "__main__":` block in `src/trading/scheduler/runner.py:198-200` as part of this SPEC, or **keep** it as a fallback for direct module execution? -- This SPEC permits either choice (S-2). Recommendation: keep for now (low cost, small fallback value); revisit in a future cleanup SPEC if dead-code analysis flags it.
- Q-2: Should `TRADING_LOG_LEVEL` be wired into `docker-compose.yml` as a documented env var for both `scheduler` and `bot` services as part of this SPEC? -- **RESOLVED (2026-05-10, user confirmed):** YES. `docker-compose.yml` shall declare `TRADING_LOG_LEVEL=${TRADING_LOG_LEVEL:-INFO}` under the `environment:` block of both `scheduler` and `bot` services. This is now in scope for `/moai run` (not optional). The `app` service is excluded (sleep-infinity utility, not a long-running consumer of the bootstrap).
- Q-3: Are there any third-party libraries (`httpx`, `apscheduler`, `python-telegram-bot`, `pgvector`, `pykrx`) currently emitting noisy `INFO` logs that would become visible for the first time and create log clutter? -- Unknown until first deploy. If confirmed during AC verification, file a follow-up SPEC for selective silencing (`logging.getLogger("httpx").setLevel("WARNING")`); do not expand this SPEC's scope.

---

## Traceability

| Requirement | Acceptance Criteria | Files Affected (representative) |
|---|---|---|
| REQ-017-1-1 | AC-017-1, AC-017-2, AC-017-5 | `src/trading/cli.py` |
| REQ-017-1-2 | AC-017-1, AC-017-2 | `src/trading/cli.py` |
| REQ-017-1-3 | AC-017-3, AC-017-4 | `src/trading/cli.py`, `docker-compose.yml` (Q-2 RESOLVED: in scope) |
| REQ-017-1-4 | AC-017-1 | `src/trading/cli.py` |
| REQ-017-1-5 | AC-017-6 | `src/trading/cli.py`, `tests/cli/test_logging_bootstrap.py` (new) |
| REQ-017-1-6 | AC-017-7 | `src/trading/cli.py` |

Cross-reference: SPEC-TRADING-016 Phase 1 AC-1-2 and AC-1-3 become positively verifiable once SPEC-017 lands.
