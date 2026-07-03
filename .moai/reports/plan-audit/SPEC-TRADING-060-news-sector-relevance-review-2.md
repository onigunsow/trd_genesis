# SPEC Review Report: SPEC-TRADING-060 (news-sector-relevance)
Iteration: 2/3
Verdict: FAIL
Overall Score: 0.78

Reasoning context ignored per M1 Context Isolation. Audit performed against the four SPEC files (spec.md v0.2.0, plan.md, acceptance.md, spec-compact.md) plus independent re-verification against the live DB (read-only SELECT via `docker exec trading-postgres psql`) and the codebase.

## Must-Pass Results

- [PASS] MP-1 REQ number consistency: REQ-060-1 (spec.md:L72), -2 (L83), -3 (L95), -4 (L101), -5 (L115). Sequential, no gaps/duplicates, consistent across plan.md M1-M4, acceptance.md scenario tags, spec-compact.md:L22-26.
- [PASS] MP-2 EARS format compliance: every normative bullet carries exactly one of [Ubiquitous]/[Event-Driven]/[State-Driven]/[Unwanted]/[Optional] with shall/shall-not phrasing (spec.md:L74-121). file:line specifics moved to non-normative "근거/reference (정규 요구사항 아님)" blocks (L81, L93, L113) — D8 remedy applied.
- [PASS] MP-3 YAML frontmatter validity: id/version/status/created/updated/author/priority present; `labels: [news, sector, relevance, alerts, hardcoding, cost-zero]` added (spec.md:L10, array type) — D5 remedy applied. House schema (`created`, matching sibling SPECs) satisfied.
- [N/A] MP-4 Section 22 language neutrality: single-project trading SPEC. Operator multi-market rule honored (REQ-060-1 per-market YAML + `active_market()`, spec.md:L74/L78).

## Regression Check (defects D1–D8 from review-1)

- **D1 — RESOLVED (verified against live DB, not paper-fixed).** Re-derived both clusters from `news_alerts_sent` (`content_hash = art:{id}`, alert_date 2026-07-03) ⋈ `news_articles`:
  - Cluster A (01:15, 35 keys, ids 738-772): `stock_market` x22 · `semiconductor` x9 · `macro_economy` x2 · `finance_banking` x1 · `biotech_pharma` x1 — EXACTLY matches spec.md:L28 / acceptance.md:L9.
  - Cluster B (08:15, 11 keys, ids 773-783): `energy_commodities` x9 (81.8%≈82%) · `defense_aerospace` x2 · `finance_banking` x0 — EXACTLY matches spec.md:L29 / acceptance.md:L10.
  - Every fixture title in acceptance.md:L9-10 matches a real DB row with the exact stored sector (e.g., 669564 "반도체 흔들릴 때 피난처 된 금융주"=energy_commodities, 663104 "은행권 FDI 원스톱 서비스"=semiconductor, 668338 "NH농협은행 농식품펀드"=defense_aerospace).
  - **Corroboration walk-through independently re-scored against ALL 11 real cluster-B member titles** using `sector_classifier._SECTOR_KEYWORDS` (title hit = 2, substring): semiconductor = 4 (659872 "반도체", 669564 "반도체"), steel_materials = 4 (669329 "철강", 669330 "포스코"), stock_market = 2 (659872 "코스피"), finance_banking = 2 (668338 "농협은행" ⊃ "은행"), **energy_commodities = 0** (no 유가/원유/OPEC/정유/LNG/이란/사우디/중동… in any title). argmax ≠ energy, energy score 0 → REQ-060-4 corroboration gate genuinely suppresses cluster B, and the SPEC's own admission that majority+quorum+sector-match alone RE-FIRES (spec.md:L39, acceptance.md:L25 step 4 ⚠️, plan.md:L16) is honest and correct. Gate on/off contrast test mandated (acceptance.md:L103, plan.md:L74).
- **D2 — RESOLVED.** Dominant failure mode ("유효 canonical 하지만 의미상 틀림") is now diagnosed with real examples (spec.md:L34), explicitly declared out of REQ-060-2's fallback-chain reach (scope-honesty note spec.md:L91, plan.md:L12/M2.1, Exclusion spec.md:L135), and remedied at cluster level by the corroboration gate.
- **D3 — RESOLVED (verified against live `ticker_metadata`).** Live: 54 rows, 13 distinct sectors, exact middle-dot/short forms (`금융` 14 · `전기·전자` 13 · `운송장비·부품` 8 · `금속` · `IT 서비스` · `화학` · `운송·창고` · `제약` · `기계·장비` · `통신` · `음식료·담배` · `전기·가스` · `유통`); 015760 = `전기·가스`/`전기·가스`, 316140 = `금융`/`기타금융`. plan.md:L23-38 mapping table lists exactly these 13 literals; acceptance scenario 4 (L56-67) and RC6 (spec.md:L50) corrected; old literals explicitly banned (plan.md:L41 [HARD]); DoD requires 100% seed coverage + integration test (acceptance.md:L105).
- **D4 — RESOLVED (with a caveat, see D9).** REQ-060-4 [Unwanted] clause (spec.md:L110) forbids ungated full-coverage critical firing when mapped sectors = 0; edge case (acceptance.md:L93), plan M1.5/M3.4, DoD item (acceptance.md:L107).
- **D5 — RESOLVED.** spec.md:L10.
- **D6 — RESOLVED.** Over-merge named as explicit accepted residual risk with rationale in Exclusions (spec.md:L133) and background fact 3 (L35); mirrored in spec-compact.md:L47.
- **D7 — RESOLVED.** "21종목" (spec.md:L48, plan.md:L50, spec-compact.md:L16); independently recounted: 21 ticker entries in `context_builder.py` TICKER_SECTOR_MAP.
- **D8 — RESOLVED.** file:line removed from normative EARS text; residual code identifiers (`group_articles[0]`, `sector_classifier`) tolerable per review-1's own minor grading.

## Category Scores (0.0-1.0, rubric-anchored)

| Dimension | Score | Rubric Band | Evidence |
|-----------|-------|-------------|----------|
| Clarity | 0.75 | Minor ambiguity a reasonable engineer must resolve | REQ-060-4(a) "클러스터가 언급/연관하는 티커" mechanism undefined (D9, spec.md:L104); corroboration argmax tie undefined (D12, spec.md:L106) |
| Completeness | 0.75 | One non-critical gap | No milestone/file for ticker-mention extraction despite path (a) being normative (plan.md:L66-67, 영향 파일 L95-110 lacks it); all sections + frontmatter otherwise complete |
| Testability | 0.75 | One AC not precisely binary-testable | Scenario 1 step 2 / Scenario 2 path (a) depend on undefined ticker extraction (acceptance.md:L17/L23/L40); catch-all edge case as written contradicts mandated keyword reuse (D10, acceptance.md:L95); everything else binary + real-data-anchored |
| Traceability | 1.00 | Every REQ has AC; every AC traces | REQ-060-1→S3/S4, -2→S6, -3→S1/S5, -4→S1/S2+edge cases, -5→DoD; all scenario tags reference existing REQs |

## Defects Found (new in v0.2.0)

**D9. spec.md:L104 / plan.md:L66-67 / acceptance.md:L17,L23,L40 — MAJOR: REQ-060-4(a) "티커 직접일치" has no defined mechanism or data source anywhere in the pipeline.**
Verified: `news_articles` has NO tickers column (schema checked), `news_analysis` has none (only impact/keywords/sentiment), `story_clusters` has only `relevance_tickers` (an OUTPUT of sector matching, not cluster-mentioned tickers), and no ticker/entity extraction exists in the news module. Yet path (a) is: (1) a new alert-FIRING surface, (2) the sole anti-over-suppression safety net cited by R4 and R8 (plan.md:L89-90), (3) the only surviving alert path under D4's full-coverage-disabled mode ("티커 직접일치 경로는 여전히 동작", spec.md:L110 — currently vacuous), and (4) load-bearing in the HARD-gate acceptance scenario ("언급 티커가 실보유에 없음 → 미발화", acceptance.md:L17/L23). Concretely: real cluster A member 661135 "우리은행 삼성월렛머니, 가입자 250만명 돌파" — whether this "언급/연관"s holding 316140 우리금융지주 (parent of 우리은행) is entirely implementation-defined; a naive name/substring matcher would make the "발화 0" HARD gate FAIL for cluster A, while exact-listed-name matching passes. The plan's 영향 파일 list contains no extraction component. The mechanism MUST be specified (e.g., exact `ticker_metadata.name` match against member titles, holdings-only, no fuzzy matching) or path (a) re-scoped, before implementation. Severity: major.

**D10. spec.md:L106 / plan.md:L65 + Reference L118 / acceptance.md:L18,L95 / spec-compact.md:L25 — MAJOR: the claim "캐치올(macro_economy·stock_market)은 키워드 세트가 없으므로 결코/자연히 확증되지 않는다" is factually FALSE for `stock_market`.**
`sector_classifier.py:43-46` (SPEC-026 c3 r2) gives `stock_market` a keyword set: `코스피`, `코스닥`, `국민연금`, `증시`, `코스피지수`, `코스닥지수` (the module docstring's "catch-alls have no keyword set" is stale; the code is authoritative). Since REQ-060-4 mandates reusing `_SECTOR_KEYWORDS` verbatim ("신규 키워드 세트 금지", plan.md:L65), a literal implementation WOULD corroborate `stock_market` for clusters with 코스피/코스닥 titles — real cluster A contains 661821 "코스닥 퇴출…" and 661822 "코스피·코스닥 급락…", so the acceptance walk-through's parenthetical "(설령 매칭돼도 캐치올은 코로보레이션 불가로 이중 차단)" (acceptance.md:L18) is false, and the edge-case test "캐치올 → 코로보레이션 불가" (acceptance.md:L95) fails as specced, forcing an unspecified deviation. Blast radius is contained (precision-first mapping never maps any 업종명 to a catch-all, so a catch-all winning sector can never match holdings → no false-alert route; cluster A/B suppression conclusions unaffected), but the false factual premise sits inside the load-bearing [Ubiquitous] corroboration definition and is repeated in all four files. Fix: replace the rationale with an explicit normative exclusion ("캐치올 섹터는 코로보레이션 대상에서 명시적으로 제외한다(shall)") — an explicit exclusion is not a "new keyword set" and stays within constraints. Severity: major.

**D11. acceptance.md:L26 — MINOR: walk-through cites "석유화학" among cluster B title keyword hits, but 석유화학 appears only in cluster A (art 661698 "한화솔루션 석유화학 제품 가격 인하").** Conclusion unchanged (steel_materials scores 4 via 철강/포스코 in real cluster B titles), but the fixture narrative should not mix cluster A evidence into cluster B. Severity: minor.

**D12. spec.md:L106 — MINOR: corroboration argmax tie behavior undefined.** Real cluster B produces a semiconductor=steel_materials tie (4 vs 4). Irrelevant for S=energy (score 0), but "S 가 최고 득점 섹터" is ambiguous when S ties for max — a legitimate sector could be non-deterministically confirmed/denied. One sentence fixes it (e.g., "S must strictly or jointly hold the max; ties involving S count as corroborated" — pick one, deterministically). Severity: minor.

**D13. plan.md:L31 — MINOR: `전기·전자` → `semiconductor` judged "명확(삼성전자류)" contradicts the SPEC's own precision-first rule.** Live roster (13 tickers) includes 373220 LG에너지솔루션(battery), 006400 삼성SDI(battery), 066570 LG전자(appliance), 272210 한화시스템(defense), 267260 HD현대일렉트릭/298040 효성중공업(power equipment) — only ~5/13 are semiconductor firms; heterogeneity is comparable to `화학`, which was ruled 모호→None. No current-holdings impact (015760/316140 only), but a future 전기·전자 holding would receive semiconductor-cluster alerts. Either justify explicitly or demote to None. Severity: minor.

**D14. spec-compact.md:L18 — MINOR: typo "013개 distinct" (should be 13개).** Severity: minor.

## spec-compact.md Sync Check

In sync with spec.md v0.2.0: version/labels (L3-4), real cluster compositions (L8-9), corroboration gate incl. D1 admission (L25), D4 full-coverage disable (L25), precision-first 7-mapped/6-None (L22), residual-risk Exclusion (L47), cost-zero/prompt-unchanged (L23). Two blemishes: D14 typo and it repeats D10's false catch-all claim (L25, already counted under D10).

## Cost-Zero / Consistency Check

- strict_cost_zero preserved: quorum + corroboration = `article_ids` → `news_articles.sector`/`title` join + pure computation (columns verified to exist); no new prompt fields (plan.md R7); no paid-call path. PASS.
- REQ-060-3 impact-weighted majority is implementable at clustering time: `get_analyzed_articles_for_clustering` already selects `na.impact_score` per member (clustering.py:127). PASS.
- Telegram format claim verified byte-for-byte against `_send_critical_alert` (relevance.py: `[NEWS ALERT] {title} (Impact 5/5, Sector: {sector}) — 포트폴리오 관련 고위험 뉴스 감지`). PASS.
- DoD item "`TICKER_SECTOR_MAP.keys()` 예외 폴백 제거" (acceptance.md:L108) matches real code path (relevance.py:72-73). PASS.
- Scenario 5 arithmetic checked (finance 7 vs semi 6). PASS.

## Chain-of-Verification Pass

Second-look findings: D9 and D10 were found ONLY by refusing to trust the SPEC's claims about the codebase — (a) tracing where "클러스터가 언급/연관하는 티커" could possibly come from (schema dumps of news_articles/news_analysis/story_clusters + grep of the news module: nothing exists), and (b) reading `_SECTOR_KEYWORDS` end-to-end instead of trusting the module docstring, which itself falsely says catch-alls have no keyword set (the r2 revision added stock_market keywords below it — the SPEC author was likely misled by that stale docstring). Re-read end-to-end: all five REQ blocks, all six scenarios + edge cases + DoD, plan M1-M4 + risk table + reference, all Exclusions, all four files cross-compared, REQ sequencing and traceability re-walked per-REQ. All eight review-1 defects were re-verified against primary evidence (live DB / code), not the SPEC's own assertions.

## Recommendation

For manager-spec (iteration 3 — all fixes are small, targeted text changes; the diagnosis and data are now sound):

1. **(D9)** Define the ticker-direct mechanism normatively in REQ-060-4(a) and add it to plan M3 + 영향 파일. Recommended minimal design: match holdings/watchlist tickers' `ticker_metadata.name` (exact listed name, e.g., "우리금융지주") against member titles/`representative_title`; no fuzzy/subsidiary matching (so cluster A's "우리은행" does NOT match 316140 — state this explicitly in acceptance scenario 1 step 2 to make the HARD gate deterministic). If no mechanism is wanted this iteration, delete path (a) and fix R4/R8/D4-residual claims accordingly.
2. **(D10)** In all four files, replace "키워드 세트가 없으므로 자연히 미확증" with an explicit normative exclusion: catch-all sectors (`macro_economy`, `stock_market`) shall be excluded from corroboration eligibility regardless of keyword scores (note `stock_market` HAS keywords since SPEC-026 c3 r2: 코스피·코스닥·국민연금·증시). Also decide whether catch-alls participate in the argmax denominator (recommended: they may still WIN argmax and thereby deny corroboration to specific sectors — that is suppression-safe; only their own confirmation is banned). Delete the false parenthetical at acceptance.md:L18.
3. **(D12)** Add one deterministic tie rule to the corroboration definition.
4. **(D11)** Remove "석유화학" from the cluster B walk-through.
5. **(D13)** Either annotate `전기·전자`→`semiconductor` with an explicit precision waiver (e.g., "혼재 인정, 반도체 대형주 지배적 — 운영자 승인") or demote to None.
6. **(D14)** Fix "013개" typo in spec-compact.md.

Verdict: FAIL
