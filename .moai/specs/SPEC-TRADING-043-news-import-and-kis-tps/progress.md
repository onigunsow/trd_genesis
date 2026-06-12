## SPEC-TRADING-043 Progress

- Started: 2026-06-13 (run phase)
- Branch: fix/SPEC-TRADING-043-news-import-and-kis-tps (from HEAD b123d8f / SPEC-042)
- Mode: TDD (RED-GREEN-REFACTOR), sub-agent
- Phase 1 (strategy/analysis): complete — 10 atomic TASKs, A/B disjoint
- Decisions confirmed: TPS pace ~2.5 req/s (min_interval 0.4s, GET-only, orders unthrottled); cache TTL 2s + force_fresh bypass; is_cli_only_mode() helper extraction; A6 slot-widen DEFERRED; INFO-only (no audit row); separate kis/balance_cache.py transparent inside balance()
- Phase 2 (TDD impl): complete — 7 new test files (24 SPEC tests), balance_cache.py new module; 1259 passed / 6 pre-existing failures (git-stash verified unrelated) / 0 new regressions
- Phase 2.10 (simplify): complete — applied: removed dead invalidate/clear, lifted cache key to KisClient.cache_key (fixes paper/live ":" collision hazard), comment dedup, lock-across-fetch doc note, docstring trim. 38 affected tests green, balance_cache.py ruff-clean, 0 new lint.
- Decisions: TPS pace 0.4s (~2.5 req/s, GET-only, post/orders unthrottled); cache TTL 2s + force_fresh on reconcile-after-fill; is_cli_only_mode() single source; A6 DEFERRED.
- Phase 3 (git): commit 282cbb8 on fix/SPEC-TRADING-043-news-import-and-kis-tps (NOT pushed)
- Deploy: make redeploy OK — app+postgres healthy, deployed commit 282cbb8 verified, scheduler clean boot (0 import/errors). Live smoke: pacer 0.4s, cache 2s, is_cli_only_mode()=True, cache_key=TradingMode.PAPER:50185724-01.
- Validation gates: (1) next 08:15 KST news_import slot should log INFO "deferring to next slot" + zero Haiku ERROR; (2) next trading day (6/15 Mon) watchdog/reconcile load → zero '초당 거래건수 초과', zero TPS-attributable 'could not read balance' skips.
