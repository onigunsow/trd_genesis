# SPEC Review Report: SPEC-TRADING-050 (dashboard-overhaul)
Iteration: 1/3
Verdict: FAIL
Overall Score: 0.68

Reasoning context ignored per M1 Context Isolation. The task prompt contained author
claims (endpoint→table mappings, "the claimed SPEC-048 stub bug"); these were treated as
hypotheses and independently verified against spec.md/plan.md/acceptance.md and the
codebase, not accepted as fact.

## Must-Pass Results

- [PASS] MP-1 REQ number consistency: REQ-050-1 .. REQ-050-25 sequential, no gaps, no
  duplicates, consistent unpadded format. M1=1-8 (spec.md:117-142), M2=9-14 (146-160),
  M3=15-18 (164-175), M4=19-21 (179-187), M5=22-25 (191-200). 8+6+4+3+4 = 25. PASS.

- [PASS] MP-2 EARS format compliance: All 25 requirements are in well-formed EARS form
  (Ubiquitous / Event-Driven / While-State / If-then-Unwanted). Verified each:
  Event-driven REQ-3/4/5/12/16/17/20/24 all carry "When … shall"; State-driven
  REQ-7/11/18/25 all carry "While … shall"; Unwanted REQ-21 is proper "If … then … shall"
  (spec.md:185-187). No informal language, no Given/When/Then mislabeled as EARS (G/W/T
  lives separately in acceptance.md, the standard MoAI split). PASS at the firewall level.
  NOTE: REQ-050-8 (spec.md:140) is tagged "(Unwanted)" but has no If/then trigger — it is
  an unconditional Ubiquitous prohibition ("shall not include … in any response"). The
  requirement itself is valid EARS; only the pattern label is wrong. Recorded as D1
  (does not trip the MP-2 FAIL condition, which is reserved for informal/G-W-T text).

- [PASS] MP-3 YAML frontmatter validity: id=SPEC-TRADING-050 (string), version="0.1.0",
  status=draft, priority=high, labels=[dashboard, frontend, observability, ui-ux,
  brownfield] (array). Date fields use the project convention `created`/`updated`
  (spec.md:5-6) rather than canonical `created_at`; this is the established convention for
  this repo (SPEC-047/048/049 use the same) and is accepted. All required fields present
  with correct types. PASS.

- [N/A] MP-4 Section 22 language neutrality: N/A — single-project SPEC (Korean trading
  system; Python/FastAPI backend + React/TS frontend). Not a 16-language LSP-tooling SPEC.
  The chart-library mention (ECharts/Recharts/lightweight-charts, spec.md:150) is a
  frontend dependency choice, not the LSP language-server set. Auto-pass.

## Category Scores (0.0-1.0, rubric-anchored)

| Dimension | Score | Rubric Band | Evidence |
|-----------|-------|-------------|----------|
| Clarity | 0.75 | 0.75 | Most REQs single-interpretation (REQ-3/17 cite exact fields). Ambiguity in REQ-10 "전문 금융 테마" + chart lib "예:(e.g.)" deferred (spec.md:149-151) and REQ-6/REQ-7 implementation-path conflict (D4). |
| Completeness | 0.85 | 0.75-1.0 | All sections present: HISTORY (17), Overview (27), Context (47), Assumptions (97), EARS Requirements (113), NFR (202), Exclusions (216, 7 specific entries), Honesty (231). Acceptance.md + edge cases + DoD present. Frontmatter complete. |
| Testability | 0.70 | ~0.75 | Most ACs binary (HTTP 200, exact field lists, null/LEFT-JOIN semantics — acceptance.md:10,16,61). Weak: REQ-10 "professional" theme not binary; AC-M3-1 "명확히 표시"(clearly), AC-M2-1 browser smoke; REQ-20 zoom/pan not asserted in any AC. |
| Traceability | 0.50 | 0.50 | "Multiple REQs lack ACs" + zero ACs cite a REQ-XXX. See D2, D3. |

## Defects Found

D1. spec.md:140 — REQ-050-8 labeled "(Unwanted)" but contains no If/then trigger; it is a
   Ubiquitous prohibition ("the system shall not include 민감 필드 … in any response").
   Either relabel to Ubiquitous, or restructure as Unwanted ("If a response would contain
   credentials/KIS payload/kis_order_no, then the system shall exclude it"). — Severity: major

D2. acceptance.md (all of AC-M1-1 .. AC-M5-2) — None of the 13 acceptance scenarios
   reference a REQ-050-N id. REQ↔AC linkage is only implicit via the M1-M5 milestone
   grouping. Audit checklist AC-4 ("Each AC references a valid REQ-XXX") fails. Add explicit
   "(covers REQ-050-N)" tags to every AC. — Severity: major

D3. spec.md:129-131,138-139,194-195 — Acceptance coverage gaps (checklist AC-5):
   - REQ-050-4 (/api/status + halt-reason/cool_down/late_cycle field additions): no AC
     exercises the /api/status contract; AC-M3-1 only tests the UI halt banner.
   - REQ-050-7 (lazy-compute "최근 N일(기본 30) 제한 + 서버측 캐시"): no AC verifies the
     N-day window or caching — and this is also flagged risk R2 (plan.md:63), so the most
     load-sensitive behavior is untested.
   - REQ-050-23 (news_trends keyword trends + individual-article display): no dedicated AC.
   - Partial: REQ-050-5 drawdown has no backend-contract AC (only UI render in AC-M4-1);
     REQ-050-20 zoom/pan untested (AC-M4-1 asserts only hover tooltip); REQ-050-2's
     /api/news and /api/trends endpoint contracts have no AC (only /api/story-clusters is
     covered by AC-M1-1/AC-M5-1). — Severity: major

D4. spec.md:134-139 — Internal inconsistency for /api/postmortem + /api/confidence-analysis.
   REQ-050-6 + AC-M1-3 mandate "fix the SQL stub `pd.run_id` → `pd.persona_run_id`"
   (i.e. keep and repair the queries.py SQL functions), while REQ-050-7 + Assumption A5
   (spec.md:108) mandate computing postmortem/confidence "via edge/postmortem.py /
   edge/confidence.py pure functions at read time (지연 계산)". These are two divergent
   implementations of the same two endpoints; the SPEC never states which is authoritative
   or how they relate. Compounding fact (verified): the two stub functions
   fetch_postmortem_distribution/fetch_calibration_scores are currently DEAD CODE — they
   are referenced only by mocked unit tests (tests/dashboard/test_queries.py:235,271) and
   are NOT wired to any app.py endpoint, so the FK bug never surfaces at runtime. The SPEC
   should decide: (a) wire+fix the SQL stub, OR (b) replace it with edge/ pure-function
   lazy compute (and delete the dead stub) — not both. — Severity: major

D5. spec.md:149-151 — REQ-050-10 "다크 전문 금융 테마" and "전문 차트 라이브러리(예:
   ECharts/Recharts; … expert-frontend 최종 선택)" is not binary-testable: "professional"
   is subjective, "예:(e.g.)" is non-binding, and the library choice is deferred. No AC
   covers it. Either move the library decision out of the normative REQ (it is already a
   plan-phase 자문 item, plan.md:83) and keep REQ-10 to testable claims (dark theme applied,
   a charting lib is present), or add a concrete AC. — Severity: minor

D6. spec.md:134-137 — REQ-050-6 bundles two distinct normative statements (reuse edge/
   queries.py AND use the correct FK). Split into two atomic requirements for clean
   traceability. — Severity: minor

D7. spec.md:140-142 vs 172 / queries.py:17 — Sensitive-field policy reuse is ambiguous.
   `_SENSITIVE_FIELDS = {request, response, kis_order_no}` is documented (queries.py:16) as
   "KIS 요청/응답 페이로드", but persona_runs also has a column literally named `response`
   (LLM text) and `response_json`, and REQ-050-17/AC-M3-2 require SHOWING `response_json`.
   No code conflict (filter keys exact name `response`, not `response_json`), but the SPEC
   reuses a KIS-scoped redaction set for LLM-payload tables without clarifying whether LLM
   `response`/`response_json` may contain sensitive content. Clarify the redaction scope per
   table. — Severity: minor

## Codebase Fact-Check (claims in the prompt + spec, independently verified)

VERIFIED TRUE:
1. SPEC-048 stub FK bug is REAL. queries.py:225 and queries.py:256 both join
   `LEFT JOIN persona_runs pr ON pr.id = pd.run_id`; the real FK is
   `persona_decisions.persona_run_id` (004_personas.sql:29). The adjacent correct query
   fetch_recent_decisions (queries.py:67) uses `ON pr.id = pd.persona_run_id`, confirming
   `run_id` is non-existent → runtime failure against real Postgres. The spec's citation
   (queries.py:204-263, 004_personas.sql:29) is accurate.
2. All 9 endpoints map to real tables/columns: persona_runs (cycle_kind/trigger_context/
   response_json/tokens/latency_ms — 004:5-19; regime_at_decision — 024:51-60),
   persona_decisions (persona_run_id FK 004:29; side/qty/rationale/confidence 004:32-37;
   prob_bull/base/bear — 033:14-17), risk_reviews (decision_id FK 004:48; verdict/
   code_rules_passed 004:49-51), system_state (current_regime 024:18; late_cycle_defense_
   active/late_cycle_level 025:34-43; cool_down_active 033:31), daily_equity_snapshot
   (total_assets/stock_eval/cash/unrealized_pnl/realized_pnl_cum 026:13-19), news_articles
   (014:5-13), news_analysis (summary_2line/impact_score/sentiment 016:5-9), story_clusters
   (portfolio_relevant 016:37; relevance_tickers 016:38), news_trends (mention_count
   016:52). ALL CONFIRMED.
3. dashboard_ro can read all needed tables: mig032 GRANT SELECT ON ALL TABLES + ALTER
   DEFAULT PRIVILEGES … GRANT SELECT ON TABLES (032:27,30). Caveat: ALTER DEFAULT
   PRIVILEGES is per-owner; holds because all tables are created by the same migration
   runner. Claim stands.
4. Dashboard served by existing FastAPI service: compose.yaml:147-170 dashboard-api runs
   `uvicorn trading.dashboard.app:app --port 8080`; app.py:43 index() serves static; 7
   GET endpoints exist (app.py:43-119, not 8 — the spec's "7개 엔드포인트" parenthetical
   at spec.md:52-54 lists 8 paths but says 7; minor wording slip, the actual route count
   is 7 GET routes + index). CONFIRMED (with the trivial count wording slip noted).
5. edge/postmortem.py classify_decision_outcome (postmortem.py:87) and edge/confidence.py
   analyze (confidence.py:106) exist as pure functions. CONFIRMED. Caveat (underspecified
   in spec): both consume assembled domain objects (DecisionOutcome / Sequence[RoundTrip]),
   not raw DB rows, so the lazy-compute path needs a row→object adapter that REQ-050-7 does
   not describe.
6. _SENSITIVE_FIELDS exists at queries.py:17 = {request, response, kis_order_no} with the
   pop-filter applied at queries.py:98-99. CONFIRMED.

OBSERVATION (not a SPEC defect): migrations 027 and 030 are absent from
src/trading/db/migrations/ (sequence jumps 026→028, 029→031). Unrelated to this SPEC;
informational only.

## MVP-Scope Consistency Assessment

The MVP boundary (no orchestrator write path; "현재 의사결정 과정" reconstructed from
latest-cycle persona_runs rather than live persona_step_progress) is internally consistent
and honestly disclosed: EXC-3 (spec.md:221), R5 (plan.md:69), Honesty §3 (spec.md:236),
and the deferral of mig034 persona_step_progress to a follow-up SPEC. REQ-050-16 + AC-M3-1
make the latest-cycle reconstruction testable. This MVP framing is sound — not a defect.

## Open-Questions Assessment (per task prompt)

The SPEC has no explicit "Open Questions" section; the four items are embedded as
assumptions/plan-deferrals. Disposition:
- Frontend path / build strategy (A1, plan.md:22-29, R4): deferrable to run (expert-devops/
  -frontend 자문). Acceptable.
- Lazy-compute params N/cache TTL (A5, REQ-7, R2): deferrable to run tuning — BUT the
  behavior itself must still have an AC (see D3). Tuning deferrable, verification is not.
- Chart library (REQ-10, plan.md:26,83): deferrable to run. Acceptable — but should not sit
  inside a normative EARS requirement (D5).
- Stub dead-code status: NOT adequately addressed. The SPEC mandates "fix the FK" without
  noting the functions are unwired dead code and without reconciling against the edge/
  pure-function path (D4). This one is blocking-ish: it determines whether M1 fixes or
  replaces the stub.

## Chain-of-Verification Pass

Second-look findings (re-read of each section):
- Re-counted REQ ids end-to-end across M1-M5: 25, contiguous, no dup. MP-1 holds.
- Re-checked every Event/State/Unwanted keyword against its clause: only REQ-8 mislabeled
  (D1); REQ-21 is a correct If/then Unwanted (initially worth double-checking — confirmed).
- Re-mapped every REQ to acceptance.md scenarios line-by-line; confirmed REQ-4, REQ-7,
  REQ-23 have no AC and REQ-2(/api/news,/api/trends)/REQ-5/REQ-20 are only partially
  covered (D3) — this was not a skim; each AC's Given/When/Then was matched to REQ fields.
- Re-read Exclusions (EXC-1..7): all 7 are specific and non-vague; no conflict with included
  REQs (CN-2 OK). EXC-3 correctly scopes the live-step-tracking deferral.
- Contradiction scan across requirements surfaced D4 (REQ-6 SQL-stub-fix vs REQ-7 edge/
  pure-function lazy compute) — confirmed by inspecting the actual stub SQL and the edge
  function signatures; this is a genuine cross-requirement inconsistency, not intra-REQ.
- New defect found on second pass that the first pass had not isolated: the stub functions
  are dead code (grep of src/ + tests/), which sharpens D4 from "ambiguity" to "the
  mandated fix may target code that should instead be deleted/replaced".

## Recommendation (FAIL — actionable fixes for manager-spec)

1. Resolve D4 (blocking): State explicitly whether /api/postmortem and
   /api/confidence-analysis are implemented by (a) the repaired SQL stub
   (fetch_postmortem_distribution/fetch_calibration_scores with persona_run_id) or (b)
   read-time lazy compute via edge/postmortem.py + edge/confidence.py pure functions. Pick
   one. If (b), add a requirement to delete the now-dead stub. If keeping the stub, specify
   the row→domain-object adapter that REQ-7 currently omits. Update REQ-6, REQ-7, A5, AC-M1-3
   to be mutually consistent.
2. Fix D2: Tag every acceptance scenario in acceptance.md with the REQ-050-N id(s) it
   verifies.
3. Fix D3: Add acceptance scenarios for REQ-050-4 (/api/status contract), REQ-050-7
   (N-day window + cache hit/miss), REQ-050-23 (trends + individual news); add backend
   contract coverage for REQ-050-5 drawdown and an interaction AC for REQ-050-20 zoom/pan;
   add contract ACs for /api/news and /api/trends (REQ-050-2).
4. Fix D1: Relabel REQ-050-8 as Ubiquitous, or restate it in proper If/then Unwanted form.
5. Fix D5/D6/D7: Move the chart-library choice out of normative REQ-10 (keep testable
   claims only); split REQ-050-6 into two atomic requirements; clarify the per-table
   redaction scope for LLM `response`/`response_json` vs KIS payloads.

All defects are fixable in-place; no must-pass-four failure. The FAIL is driven by broken
traceability (AC-4 zero REQ links; AC-5 multiple uncovered REQs) plus a genuine internal
inconsistency (D4). Re-audit at iteration 2 after these are addressed.

Verdict: FAIL
