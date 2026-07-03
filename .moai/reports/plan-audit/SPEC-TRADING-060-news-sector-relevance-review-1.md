# SPEC Review Report: SPEC-TRADING-060 (news-sector-relevance)
Iteration: 1/3
Verdict: FAIL
Overall Score: 0.55

Reasoning context ignored per M1 Context Isolation. Audit performed against the four SPEC files plus independent verification against the codebase (origin/main d50fe04, confirmed) and read-only live DB queries (`docker exec trading-postgres psql`, SELECT only).

## Must-Pass Results

- [PASS] MP-1 REQ number consistency: REQ-060-1 (spec.md:L62), REQ-060-2 (L70), REQ-060-3 (L78), REQ-060-4 (L84), REQ-060-5 (L91). Sequential, no gaps, no duplicates, consistent numbering across all four files (spec-compact.md:L17-21, plan.md M1-M4, acceptance.md scenario tags).
- [PASS] MP-2 EARS format compliance: Every requirement bullet in spec.md is labeled with exactly one of the five patterns ([Ubiquitous]/[Event-Driven]/[State-Driven]/[Unwanted]/[Optional]) with shall/shall-not phrasing (spec.md:L64-97). acceptance.md uses Given-When-Then correctly in the separate acceptance document, not mislabeled as EARS.
- [FAIL] MP-3 YAML frontmatter validity: `labels` field is MISSING (spec.md:L1-L10 contains only id/version/status/created/updated/author/priority/issue_number). Every sibling SPEC carries `labels` — verified SPEC-TRADING-051 (`labels: ["trading","kis",...]`), 052, 053, 054, 059 (`labels: [factor, quality, ...]`). Missing required field = FAIL per M5.
- [N/A] MP-4 Section 22 language neutrality: single-project trading SPEC, not template-bound multi-language tooling. (Note: the operator's multi-market neutrality rule IS honored — REQ-060-1 externalizes market-dependent mapping to per-market YAML + `active_market()`, spec.md:L64, L102.)

## Evidence Verification (caller-requested checks)

All cited file:line evidence was independently verified and is ACCURATE:

| Claim | Verified |
|---|---|
| `clustering.py:193` `sector_val = group_articles[0]["sector"]` | EXACT — line 193, with comment "should be same for all; take first" |
| `context_builder.py:43` `TICKER_SECTOR_MAP`, `:78-80` `get(t,"stock_market")`, `:210` call in `build_micro_news` | EXACT |
| `prompts.py:44-52` sector field requested with canonical set + routing rules | EXACT |
| `analyzer.py` `_corrected_sector:81`, PAID_CALL:235, `_validate_results:400`, `_store_results:503` call:545, `import_host_results:800` call:890 | ALL EXACT |
| `relevance.py` `:14` import, `:22` IMPACT_CRITICAL_THRESHOLD=5, `:25/:41/:47/:72/:75/:110/:128/:184/:187-215/:228` | ALL EXACT |
| `sources.py:14/:37`, `rss_fetcher.py:275/316/345`, `web_scraper.py:293`, `sector_classifier.py:84-109`, `normalizer.py:188`, `reporter.py:23/315` | ALL EXACT |
| migrations 036/037, `dashboard/sector_loader.py`, `scripts/analyze_news.sh`, `sector_taxonomy.py` `active_market()`/lru_cache, tests `test_sector_classifier/relevance/sector_from_analysis.py` | ALL EXIST |
| Holdings 015760/316140 | CONFIRMED in live `positions` (qty 2 / 7) |

The claim "CLI already emits sector and it is wired" is TRUE (prompts.py:44 + analyzer.py:81/545/890). However, see D2 — the SPEC does not close the gap this truth opens.

## Category Scores (0.0-1.0, rubric-anchored)

| Dimension | Score | Rubric Band | Evidence |
|-----------|-------|-------------|----------|
| Clarity | 0.75 | Minor ambiguity a reasonable engineer resolves | full_coverage_mode interaction undefined (D4); plan.md:L24 "빈 리스트(full coverage) 또는 실보유만" undecided |
| Completeness | 0.75 | One non-critical gap; frontmatter missing one field | HISTORY/배경/근거/EARS/제약/Exclusions all present (spec.md:L16, L20, L33, L60, L99, L106); `labels` missing (L1-10) |
| Testability | 0.50 | Core scenario requires judgment/contradicts reality | Scenario 1 Given (acceptance.md:L8-9) contradicts live DB (D1); Scenario 4 Given (L42) uses wrong 업종명 literals (D3); DoD live-replay item (L90) unachievable as specced |
| Traceability | 1.00 | Every REQ has AC; every AC tagged to existing REQ | Scenarios 1-6 tagged [REQ-060-N] (acceptance.md:L5,L19,L29,L40,L50,L61); DoD covers REQ-060-1~5 (L81) |

## Defects Found

**D1. acceptance.md:L8-16 / spec.md:L89 — CRITICAL: The 08:15 false-alert replay scenario is a strawman; under the actual live cluster composition the new rules RE-FIRE the false alert.**
Live DB evidence (read-only): the 08:15 alert keys (`news_alerts_sent`, 2026-07-03 08:15:16) cover 11 member articles whose stored sectors are: `energy_commodities` x9 (including the "반도체 흔들릴 때 피난처 된 금융주" article itself, id 669564), `defense_aerospace` x2, `finance_banking` x0. Acceptance scenario 1 asserts "defense 동의 0개, 실제 다수는 finance_banking/stock_market" — factually false. Under REQ-060-3 (impact-weighted majority: energy weight 33 vs defense 8 → `energy_commodities`) + REQ-060-4 (quorum 9/11 = 82% >= 50% → passes) + REQ-060-1 (015760 한국전력 '전기·가스' → `energy_commodities` per the SPEC's own scenario 4 example), the sector-match gate FIRES for this financial-news cluster. The DoD item "07-03 오경보 재실행 시 미발화" (acceptance.md:L90) is unachievable by the specced mechanism; only the fabricated fixture passes. Severity: critical.

**D2. spec.md:L18, L47, L70-76 — CRITICAL: The SPEC correctly proves the CLI sector path is wired, but never diagnoses why live article-level sectors remain systematically wrong despite it — and REQ-060-2 does not fix the observed failure mode.**
Live evidence: analyzed (clustered) articles carry sectors like "은행권 FDI 원스톱 서비스" → `semiconductor`, "피난처 된 금융주" → `energy_commodities`, "임신중절수술" (medical, impact≥1, clustered) → `stock_market`. These are wrong-but-CANONICAL values, which `_corrected_sector` (analyzer.py:87-91) passes through and REQ-060-2's fallback chain (CLI → keyword → feed) only triggers on missing/invalid sectors. The dominant live failure mode (wrong-valid sector, whether LLM echo of feed hint or otherwise) is neither diagnosed nor remedied. plan.md:L29 defers the diagnosis to implementation ("[VERIFY] 기존 경로 실측") — but the entire alert-suppression design (D1) depends on member-article sector quality. Severity: critical.

**D3. acceptance.md:L42,L47 / plan.md:L20 / spec.md:L49 — MAJOR: 업종명 literals contradict live `ticker_metadata`.**
Live values: 015760 = `전기·가스` (NOT '전기가스업'), 316140 = `금융` (NOT '금융업'), plus `전기·전자`, `금속` (middle-dot forms). The SPEC/plan/acceptance repeatedly seed examples with '금융업'/'전기가스업'/'전기전자'. If the YAML `news_sector_map` is written from these literals, `resolve_ticker_sector` returns None for ALL real holdings → `get_watchlist_sectors` empty → `full_coverage_mode` (relevance.py:104-106) → alert on EVERY impact-5 cluster — strictly worse than today. plan.md:L57 (R2 "실 ticker_metadata 54행 전수 커버") partially mitigates, but the acceptance Given is factually wrong and is the exact pykrx-column false-green pattern this project was burned by (commit 7236acd lesson). Severity: major.

**D4. spec.md:L86-88 / acceptance.md:L74 — MAJOR: REQ-060-4 does not specify how the quorum/ticker gate interacts with `full_coverage_mode`.**
relevance.py:104-106: when the sector map is empty, ALL clusters with impact >= threshold are tagged relevant with no sector or ticker matching at all — the gates in REQ-060-4(a)/(b) are structurally bypassed. The acceptance edge case "빈 ticker_metadata → full coverage 모드(기존 동작 보존)" (acceptance.md:L74) explicitly preserves this ungated alert path, and plan.md:L24 leaves the fallback design undecided ("빈 리스트(full coverage) 또는 실보유만"). Combined with D3, this is a plausible route to MORE false alerts. Severity: major.

**D5. spec.md:L1-10 — MAJOR (must-pass): `labels` frontmatter field missing** (see MP-3). Severity: major.

**D6. spec.md:L109 — MINOR: Exclusion "재클러스터링 알고리즘 재설계" conflicts with an observed root cause.**
The real 08:15 cluster merges 금융주/철강 BSI/AWS/SK하이닉스/일양약품 articles via keyword-overlap union-find (clustering.py KEYWORD_OVERLAP_MIN=2) — thematic over-merge is a co-root-cause of the false alert (majority-of-a-garbage-cluster is still garbage), yet it appears in neither RC1-RC5 nor the goals, and the Exclusion locks it out without acknowledging the residual risk. Severity: minor (should at least be named as accepted residual risk).

**D7. spec.md:L40 — MINOR: `TICKER_SECTOR_MAP` has 21 entries, not "20종목"** (counted from context_builder.py:43-77). Severity: minor.

**D8. spec.md:L64, L72 — MINOR: implementation details (file paths, function names, line numbers) embedded in normative EARS text** (e.g., "prompts.py:44 요청", "sector_taxonomy.yaml 확장 또는 형제 설정"). House style for remediation SPECs tolerates this, but WHAT/HOW separation is degraded. Severity: minor.

## Chain-of-Verification Pass

Second-look findings: D1 and D3 were found ONLY in the second pass, by refusing to trust the acceptance fixtures and re-deriving the alert-time cluster composition from `news_alerts_sent` content_hash keys plus `news_articles.sector`, and by querying live `ticker_metadata` for the exact 업종명 strings. First-pass file:line verification (all accurate) would have misleadingly suggested a high-quality SPEC — the citations are excellent; the defect is the gap between diagnosis and remedy. Re-read sections: all five REQ blocks end-to-end, all six scenarios, plan M1-M4, all Exclusions, all four files cross-compared. Cost-zero check: every proposed mechanism is DB read + pure computation; no new prompt fields (plan.md:L62 R7); no paid-call path introduced — strict_cost_zero respected (no defect).

## Regression Check (Iteration 2+ only)

N/A — iteration 1.

## Recommendation

For manager-spec, in priority order:

1. **(D1/D2) Re-derive the false-alert mechanism from the REAL alert-time data before fixing gates.** Query `news_alerts_sent` (07-03 01:15 and 08:15 keys) joined to `news_articles.sector` and record the actual member compositions in the SPEC's 근거 section. Then either (a) add a REQ that addresses wrong-but-canonical article sectors (e.g., cross-check CLI sector against the keyword classifier and demote to a low-confidence state when they disagree AND disagree with feed; or require ticker/entity-level evidence for sector-only alerts), or (b) drop the claim that REQ-060-3+4 alone make the two alerts "불가능" and re-scope the goal honestly. The repro test fixtures MUST mirror the real compositions (energy x9 / defense x2 / finance x0 for cluster B), and the DoD live-replay item must be consistent with the mechanism.
2. **(D3) Replace every 업종명 literal with the actual live values** (`금융`, `전기·가스`, `전기·전자`, `금속`, middle dots included) in acceptance scenario 4, plan M1.1, and spec.md L49. Add an explicit AC that the YAML map covers 100% of distinct live `ticker_metadata.sector` values at implementation time.
3. **(D4) Specify full_coverage_mode behavior under the new gates**: decide (and write as an EARS [Unwanted] clause) what happens when `get_watchlist_sectors` is empty after hardcoding removal — silently entering ungated full-coverage alerting after a mapping failure must be excluded.
4. **(D5) Add `labels` to the frontmatter** (e.g., `[news, sector, relevance, alerts, hardcoding, cost-zero]`).
5. **(D6) Either add cluster over-merge to the root-cause table as accepted residual risk with rationale, or narrow the Exclusion.**
6. (D7/D8) Correct "20종목" → 21; optionally move file:line specifics from normative REQ text into the 근거 table.

Verdict: FAIL
