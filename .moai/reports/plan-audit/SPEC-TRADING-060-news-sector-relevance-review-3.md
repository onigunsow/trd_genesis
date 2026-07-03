# SPEC Review Report: SPEC-TRADING-060 (news-sector-relevance)
Iteration: 3/3 (final)
Verdict: FAIL
Overall Score: 0.86

Reasoning context ignored per M1 Context Isolation. Audit performed against the four SPEC files (spec.md v0.3.0, plan.md, acceptance.md, spec-compact.md) with independent live re-verification (read-only SELECT via `docker exec trading-postgres psql -U trading -d trading`) and codebase reads. Orchestrator claims about "what was fixed" were re-derived from primary evidence, not trusted.

## Must-Pass Results

- [PASS] MP-1 REQ number consistency: REQ-060-1 (spec.md:L73), -2 (L84), -3 (L96), -4 (L102), -5 (L119). Sequential, no gaps/duplicates; consistent in plan.md M1-M4, acceptance.md scenario tags (incl. new 2b → REQ-060-4a), spec-compact.md:L22-26.
- [PASS] MP-2 EARS format compliance: every normative bullet, including all v0.3.0 additions (spec.md:L107 [Ubiquitous] substring predicate, L108 [Unwanted] empty-name guard, L109 [Ubiquitous] corroboration+tie rule, L110 [Unwanted] catch-all exclusion), carries exactly one EARS tag with shall/shall-not phrasing. file:line remains confined to non-normative 근거/reference blocks.
- [PASS] MP-3 YAML frontmatter validity: id/version(0.3.0)/status/created/updated/author/priority/labels(array) present (spec.md:L1-11); house schema (`created`) consistent with sibling SPECs, unchanged from review-2 PASS.
- [N/A] MP-4 Section 22 language neutrality: single-project trading SPEC; operator multi-market rule honored (per-market YAML + `active_market()`, spec.md:L75/L79, Exclusions US adapter entry).

## Regression Check (defects D9–D14 from review-2)

- **D9 — RESOLVED (verified against live DB and code).** Ticker direct-match is now fully and deterministically defined in all four files: `ticker_metadata.name` exact substring against member `news_articles.title` OR `news_analysis.keywords` array elements (spec.md:L105/L107; plan.md design #7 + M3.4; acceptance scenario 2b + edge cases; spec-compact:L25). `news_analysis.keywords` verified to exist as ARRAY (information_schema). No-alias rule explicit (우리은행 ≠ 우리금융지주) with Exclusions entry (spec.md:L136). **Empty-name guard verified live**: `SELECT count(*), count(*) FILTER (WHERE name=''), count(*) FILTER (WHERE btrim(name)='') FROM ticker_metadata` → 54|44|44 — the SPEC's "44/54 name=''" claim is exact. Guard present and consistent in all four files: spec.md:L108 [Unwanted], plan.md M3.4 (빈 name 가드) + M4.1 test item, acceptance.md edge case (`{'005930': ''}` → False) + DoD, spec-compact:L25. Holdings names verified filled (015760=`한국전력`, 316140=`우리금융지주`). Determinism re-walked with the NEW keywords surface: scanned all 46 member articles of clusters A/B (alert ids 738-783, `content_hash=art:{id}`) — neither `한국전력` nor `우리금융지주` appears in any member title or `news_analysis.keywords` element; the only filled-name hit anywhere is `현대로템` in cluster-A title 661706, and 064350 is not held (live `positions WHERE qty>0` = exactly {015760, 316140}), no `watchlist` table exists, so 발화 0 holds. Scenario 2b positive/negative pair is binary-testable.
- **D10 — RESOLVED.** The false "catch-alls have no keywords" premise is removed from all four files and replaced with an explicit normative exclusion: spec.md:L110 [Unwanted] (catch-all winning sector excluded from sector-path eligibility; may still win argmax and deny others — suppression-safe, exactly the recommended denominator decision), plan.md M3.3 + Reference, acceptance.md:L18 (false parenthetical gone) + edge case L109, spec-compact:L25. `stock_market` keyword set re-verified in `sector_classifier.py` (`코스피`·`코스닥`·`국민연금`·`증시`·`코스피지수`·`코스닥지수`); `macro_economy` has no key — the SPEC now states both facts correctly (spec.md:L117).
- **D11 — RESOLVED.** `석유화학` no longer appears in the cluster-B walk-through (acceptance.md:L26); it remains only in cluster-A context where it belongs (art 661698, acceptance.md:L9).
- **D12 — RESOLVED.** Deterministic tie rule `score(S) >= 1 AND score(S) == max(전 섹터 득점)`, ties including S count as corroborated — identical in spec.md:L109, plan.md M3.3 + gate-order note, acceptance.md:L26 + DoD, spec-compact:L25. Cluster-B walk-through correctly notes energy=0 makes tie handling moot.
- **D13 — RESOLVED (live-verified).** `SELECT ticker,name,industry FROM ticker_metadata WHERE sector='전기·전자'` → 13 rows, ALL name='' and industry='전기·전자' (no sub-signal) — plan.md's D13 근거 dump matches live data exactly. `전기·전자` demoted to `None`; mapping table now 명확 6 / 모호 7, consistently updated in spec.md HISTORY, plan.md M1 table, acceptance.md DoD, spec-compact:L22. Holdings 015760/316140 unaffected (verified: 전기·가스/금융).
- **D14 — RESOLVED.** spec-compact.md:L18 now reads "13개 distinct".

## Category Scores (0.0-1.0, rubric-anchored)

| Dimension | Score | Rubric Band | Evidence |
|-----------|-------|-------------|----------|
| Clarity | 0.95 | ~1.0: single interpretation | All D9/D12 ambiguities closed with explicit predicates (spec.md:L107-L109); walk-through arithmetic independently recomputed and exact (semi 4 · steel 4 · energy 0 · finance 2 · stock_market 2 from `_SECTOR_KEYWORDS`; cluster A impact-weighted majority stock_market 79 vs semi 32 — live SQL) |
| Completeness | 0.75 | One non-critical-section gap, load-bearing | N1: plan omits the required `_load_watchlist_tickers` query fix and asserts a false premise ("이미 DB 조회", plan.md M3.6); N4 dangling Exclusions cross-reference (spec.md:L108) |
| Testability | 0.90 | ~1.0 with one matrix gap | All scenarios binary and real-data-anchored; N2: whitespace-only name guard normative but untested; live 미발화 check (M4.4b) confounded by N1 (would pass under a dead loader) |
| Traceability | 1.00 | Every REQ has AC; every AC traces | REQ-060-1→S3/S4, -2→S6, -3→S1/S5, -4→S1/S2/S2b+edges, -5→DoD; scenario 2b tagged REQ-060-4a |

## Defects Found (new in v0.3.0 audit — found via live SQL, not present in prior reviews)

**N1. plan.md M3.6 ("기존 `_load_watchlist_tickers`(47) 재사용(이미 DB 조회)") — MAJOR: false codebase premise; the loader's DB path is dead code, and the plan schedules no fix.**
Live evidence: `positions` has column `qty`, NOT `quantity` (information_schema verified; `SELECT ... WHERE quantity > 0` reproduces `ERROR: column "quantity" does not exist`). The existing `_load_watchlist_tickers` executes exactly that query, so it ALWAYS raises, is caught by the broad `except Exception`, and returns hardcoded `TICKER_SECTOR_MAP.keys()` — it has never returned DB holdings. Consequence chain if implemented literally per plan: M1.5 changes the exception fallback to 빈 리스트 (correct per REQ) + M3.6 says reuse the loader as-is ("이미 DB 조회") → every call raises → returns `[]` → `get_watchlist_sectors` = {} → per the D4 rule sector-based critical alerts are disabled BY DESIGN, and `_ticker_direct_match` has 0 candidates → **total, silent alert blackout indistinguishable from D4's designed silence**. The SPEC's own verification net does not reliably catch this: M4.3 integration tests are scoped to `resolve_ticker_sector`·quorum (loader not listed); M4.4(b) "07-03 오경보 재발 없음" passes trivially under the blackout (미발화 for the wrong reason — it inverts the live HARD gate into a false positive); only M4.4(c) "정상 연관 뉴스 정상 발화" catches it, and that check is contingent on a suitable live cluster existing at deploy time. This is the same defect species as D10 (false factual premise about the codebase inside a load-bearing instruction) and the exact mock-거짓그린/silent-live-failure pattern this project has been burned by repeatedly. The normative spec.md text is correct ("실보유+워치리스트는 알림 시점에 DB에서 조회해야 한다", spec.md:L113) — the defect is that plan.md affirmatively directs reuse of a function that cannot satisfy it and lists no [MODIFY] for the query. Severity: major.

**N2. spec.md:L108 vs acceptance.md:L108 / plan.md M4.1 — MINOR: whitespace-only name guard is normative but untested.** The [Unwanted] clause covers "빈 문자열이거나 공백뿐"; the test matrix covers only `name=''`. An `if not name:` implementation passes the specced tests, yet a future whitespace-only name (`' '` is a substring of nearly every title) would cause full-coverage firing. Live data currently has no whitespace-only names (blank==empty==44), so latent only. Add a `'  '` → False test case. Severity: minor.

**N3. spec-compact.md:L41 — MINOR: 영향 파일 test list omits `test_ticker_direct_match.py`** (added in v0.3.0 per plan.md M4.1); compact lists only 4 of 5 new test files. Severity: minor.

**N4. spec.md:L108 — MINOR: dangling cross-reference.** "name 전수 backfill 은 … 업스트림 개선 항목이다(**Exclusions 참조**)" — the Exclusions section (spec.md:L134-142) contains no `ticker_metadata.name` backfill entry (the 백필 entry there concerns `story_clusters` re-labeling, a different thing). Add the entry or drop the reference. Severity: minor.

## Cost-Zero / Consistency Check

- strict_cost_zero preserved: ticker path + quorum + corroboration = DB reads (`article_ids` → title/sector/keywords joins, columns verified live) + pure computation; no new prompt fields (plan R7); no paid-call path. PASS.
- Telegram format, dedup window, Impact threshold unchanged. PASS.
- Scenario 6 re-verified against `classify_sector` code: "반도체 HBM" scores semiconductor 4 ≥ 2 > fallback(macro_economy)=0 → override — walk-through correct. PASS.
- Scenario 2 fixture keywords (은행·지주·증권사) verified present in `finance_banking` keyword set. PASS.
- Cluster fixtures re-verified byte-level against live `news_alerts_sent` ⋈ `news_articles` ⋈ `news_analysis` (35+11 members; impact sums stock_market 79 / semi 32 / energy 33 / defense 8 — impact-weighted majority claims exact). PASS.

## Chain-of-Verification Pass

Second-look findings: N1 was found only by executing the loader's actual SQL premise against the live schema (triggered by the empty-name investigation touching `positions`); N4 by walking every cross-reference in the new L108 clause to its target section; N2 by diffing the normative guard wording against the test matrix; N3 by cross-comparing the five [NEW] test files in plan vs compact. Re-read end-to-end: all five REQ blocks, all seven scenarios + edge cases + DoD, plan M1-M4 + risk table + 영향 파일 + Reference + @MX plan, full Exclusions honesty check (over-merge residual risk retained verbatim), REQ sequencing and per-REQ traceability re-walked. All six review-2 defects re-verified against primary evidence (live SQL / code), not the SPEC's assertions.

## Regression Check Summary (full history)

- Iteration 1 (0.55): D1–D8 — all RESOLVED (verified in review-2, spot re-confirmed unchanged in v0.3.0).
- Iteration 2 (0.78): D9–D14 — all RESOLVED (this review, primary-evidence verified).
- Iteration 3 (0.86): N1 (major), N2/N3/N4 (minor) — NEW. No stagnating defect exists (nothing survived across iterations unchanged; each iteration's fixes were genuine).

## Escalation Report (iteration 3/3 FAIL — user intervention recommended)

The SPEC improved monotonically (0.55 → 0.78 → 0.86); the diagnosis, live-data anchoring, and normative requirements are now sound, and all 14 prior defects were genuinely fixed. The remaining blocker is a single false premise in plan.md discovered by executing the code's own SQL against the live schema:

1. **(N1 — required)** Amend plan.md M3.6 (and M1.5 scope): `_load_watchlist_tickers`'s positions query references a non-existent `quantity` column (real column: `qty`) and has therefore NEVER returned DB holdings — it always falls into the exception fallback (`TICKER_SECTOR_MAP.keys()`). The milestone must (a) fix the query to `WHERE qty > 0`, (b) remove the hardcoded fallback (already planned), (c) add the loader to the M4.3 integration-test scope (real Postgres, asserting {015760, 316140} returned) so the blackout cannot ship silently, and (d) note in M4.4 that the 미발화 live check alone cannot distinguish "gates work" from "loader dead" — the 정상 발화 check (c) is mandatory, not best-effort.
2. **(N2)** Add whitespace-only name test case (`'  '` → False) to test_ticker_direct_match matrix.
3. **(N3)** Add `test_ticker_direct_match.py` to spec-compact 영향 파일.
4. **(N4)** Add a `ticker_metadata.name` backfill entry to Exclusions or drop the "(Exclusions 참조)" pointer at spec.md:L108.

All four fixes are small, targeted text changes; no re-diagnosis is needed. Options for the user: (a) authorize one out-of-band revision cycle for the four fixes above (recommended — N1 is a one-paragraph plan amendment), or (b) proceed to /moai run only with N1's four sub-items injected verbatim into the implementation prompt as HARD constraints.

Verdict: FAIL
