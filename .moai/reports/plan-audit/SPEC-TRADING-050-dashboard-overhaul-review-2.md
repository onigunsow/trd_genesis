# SPEC Review Report: SPEC-TRADING-050 (dashboard-overhaul)
Iteration: 2/3
Verdict: PASS
Overall Score: 0.93

Reasoning context ignored per M1 Context Isolation. The task prompt restated the author's
account of what was fixed (D4 unification, adapter claim, etc.); these were treated as
claims-to-disprove and independently verified against spec.md / plan.md / acceptance.md
(v0.2.0) and the live codebase (queries.py, edge/postmortem.py, edge/confidence.py), not
accepted as fact.

## Must-Pass Results

- [PASS] MP-1 REQ number consistency: REQ-050-1..25 all present exactly once, contiguous,
  no gaps, no duplicates. M1=1,2,3,4,5,6,6a,7,8 (spec.md:125-168); M2=9-14 (172-187);
  M3=15-18 (191-202); M4=19-21 (206-214); M5=22-25 (218-227). The new "6a"
  (spec.md:158-159) is a deliberate letter-suffixed atomic split of the former compound
  REQ-6 (D6 fix) — not a gap or duplicate. Unpadded REQ-050-N format consistent with repo
  convention (SPEC-047/048/049). PASS.

- [PASS] MP-2 EARS format compliance: All requirements well-formed.
  Re-verified the changed/added ones: REQ-050-6 (Ubiquitous, "shall", spec.md:153-157),
  REQ-050-6a (Ubiquitous, "shall", 158-159), REQ-050-7 (State-Driven "While ... the system
  shall (a)/(b)/(c)", 160-164), REQ-050-8 now (Ubiquitous, 165-168). The doubled
  "shall ... shall한다" in REQ-8 is the bilingual EARS template marker + Korean verb, used
  identically across REQ-1/2/6/6a — a consistent doc convention, not a defect. No informal
  language; Given/When/Then lives separately in acceptance.md (standard MoAI split). PASS.

- [PASS] MP-3 YAML frontmatter validity: id=SPEC-TRADING-050 (string), version="0.2.0"
  (bumped, string), status=draft, created/updated ISO dates (repo convention vs canonical
  created_at — accepted per iter-1 precedent and SPEC-047/048/049), priority=high,
  labels=[dashboard, frontend, observability, ui-ux, brownfield] (array). All required
  fields present, correct types (spec.md:2-11). PASS.

- [N/A] MP-4 language neutrality: N/A — single-project Korean trading SPEC (Python/FastAPI
  + React/TS). Not a 16-language LSP-tooling SPEC. Chart-library mention is now confined to
  plan design notes, not a normative REQ. Auto-pass.

## Category Scores (0.0-1.0, rubric-anchored)

| Dimension | Score | Rubric Band | Evidence |
|-----------|-------|-------------|----------|
| Clarity | 0.90 | 0.75-1.0 | D4 contradiction removed: single coherent pipeline stated at spec.md:144-151 + plan.md:17-26 ("원시 행 → 어댑터 → edge 순수함수 → JSON"); REQ-6/6a/7 explicitly "이 단일 구현을 가리킨다"(150). REQ-10 "전문" subjectivity removed from normative text → "다크 테마(어두운 배경+충분한 대비)" (175-178). Residual: REQ-2 endpoint→AC mapping in matrix understated (minor). |
| Completeness | 0.92 | 0.75-1.0 | All sections present: HISTORY(15), Overview(35), Context w/ file:line(55), Assumptions(105), EARS(121), NFR(229), Exclusions 7 entries(243), Honesty(258), 관련 SPEC(268). acceptance.md adds traceability matrix(133-162)+edge cases+DoD. Adapter pipeline documented in both spec & plan. |
| Testability | 0.90 | ~0.90 | New binary ACs added: AC-M1-4 (adapter conversion + N=30 window + cache hit, two-call, "두 번째 호출은 캐시에서 응답하여 DB 재질의 없음", acceptance.md:28-32) covers risk R2; AC-M1-5 (/api/status contract, 34-39); AC-M5-3 (trends + news, 117-122); zoom/pan now asserted in AC-M4-1 (90-96). DoD checklist concrete. |
| Traceability | 0.95 | 1.0 band | Every REQ (all 26 incl. 6a) → ≥1 AC via matrix (acceptance.md:135-162); every AC heading carries "— REQ-050-N"; every AC references a valid REQ. No uncovered REQ, no orphan AC. Knocked off 0.05 only because REQ-050-5 (drawdown) maps via UI-consumption AC-M4-1 rather than a dedicated backend-contract AC (indirect, rubric 0.75-band example) and REQ-2's matrix row cites fewer ACs than the endpoints actually covered. Up from 0.50 at iter-1. |

## Prior-Defect Regression Check (D1-D7)

- D4 (BLOCKING) — RESOLVED. The dead/broken stub is now mandated for REMOVAL, not repair:
  REQ-050-6 "SPEC-048의 죽은/깨진 stub ... 을 제거하고, 이를 대체하는 ... 지연계산 쿼리
  함수가 올바른 FK(persona_decisions.persona_run_id, pd.run_id 아님)로 ... 조인"
  (spec.md:153-157). The single pipeline is explicit at spec.md:144-151 and plan.md:17-26:
  "원시 DB 행(ro_connection SELECT) → 어댑터(raw rows → edge 도메인 객체) → edge 순수
  함수(classify_decision_outcome / confidence.analyze) → JSON", and "REQ-050-6 과 REQ-050-7
  은 이 단일 구현을 가리킨다." The adapter requirement is EXPLICIT in REQ-050-7(a)
  (spec.md:160-162) and tested by AC-M1-4. No residual contradiction: REQ-6 (stub removal +
  FK-correct raw-row JOIN) and REQ-7 (adapter + N-day + cache over those rows) describe one
  pipeline, not two. RESOLVED.

- D2 — RESOLVED. All 17 ACs + 4 edge cases carry "— REQ-050-N" tags (acceptance.md:8,14,20,
  28,34,41,50,55,61,67,75,82,90,98,106,112,117 + E1-E4 at 126-131); forward traceability
  matrix present (133-162). (Task framing said "16 scenarios"; actual count is 17 ACs —
  immaterial, all tagged.)

- D3 — RESOLVED. New ACs: REQ-050-4 → AC-M1-5 (status contract); REQ-050-7 → AC-M1-4
  (adapter + N-day + cache, the R2 risk behavior); REQ-050-23 → AC-M5-3. Completed:
  REQ-050-5 drawdown → AC-M4-1 (mapped, consumed); REQ-050-20 zoom/pan now asserted in
  AC-M4-1; REQ-050-2 endpoints all covered across AC-M1-1/AC-M1-3/AC-M1-4/AC-M3-1/AC-M5-3
  + E4. (Minor: matrix row for REQ-2 cites only AC-M1-1,E4 though more ACs cover its
  endpoints — see D8-new.)

- D1 — RESOLVED. REQ-050-8 relabeled "(Ubiquitous)" (spec.md:165).

- D5 — RESOLVED. Subjective "professional theme" removed from normative REQ-10; now
  "다크 테마(어두운 배경 + 충분한 대비)" with interaction measured by M4 AC (spec.md:175-178).
  Chart-library choice moved to plan.md:40-45 "디자인 노트 (정상 REQ 아님; D5)". AC-M2-4
  tests dark theme via theme class/token check (acceptance.md:67-71).

- D6 — RESOLVED. Compound REQ-050-6 split into REQ-050-6 (stub removal + FK; "단일 책임:
  stub 제거·FK 교정", spec.md:157) and REQ-050-6a (reuse edge/db/queries; "단일 책임:
  재사용", 158-159).

- D7 — RESOLVED. Redaction scope now explicit in REQ-050-8: exclude = credentials / KIS
  request/response payload / kis_order_no; include = LLM rationale / confidence /
  prob_bull/base/bear / verdict (spec.md:165-168). Mirrored in plan.md:29-30 and DoD
  (acceptance.md:171). Adequate at the spec-clarity level (the spec surfaces `response_json`,
  `trigger_context`, `rationale` — not the bare `response` key that _SENSITIVE_FIELDS pops —
  so no runtime collision).

All 7 prior defects RESOLVED. No stagnation, no blocking defect carried forward.

## Codebase Fact-Check (adapter claim + FK, re-verified fresh)

VERIFIED TRUE:
1. Stub FK bug still present in code: queries.py:223 and queries.py:252 both
   `LEFT JOIN persona_runs pr ON pr.id = pd.run_id`; correct FK is `pd.persona_run_id`
   (queries.py:67 fetch_recent_decisions uses the correct form). The SPEC's mandate to
   remove/replace these is accurate.
2. Adapter is genuinely required (REQ-050-7 claim is technically correct):
   - edge/postmortem.py classify_decision_outcome (postmortem.py:87) takes
     `decision: dict, roundtrip_or_none: dict|None, relative_5d, relative_20d, regime,
     thresholds` — assembled inputs, NOT raw joined rows. Returns DecisionOutcome.
   - edge/confidence.py analyze (confidence.py:106) takes `Sequence[RoundTrip]` (domain
     objects from trading.edge.roundtrips), NOT raw rows.
   A row→domain-object adapter is therefore mandatory, and REQ-050-7(a) + AC-M1-4 now make
   it explicit and testable. The iter-1 gap ("REQ-7 omits the adapter") is closed.
3. _SENSITIVE_FIELDS = frozenset({"request","response","kis_order_no"}) at queries.py:17,
   applied via pop-filter (queries.py:98). Redaction inheritance claim stands.

## Other requested confirmations

- Traceability: every REQ has AC coverage (matrix complete, 26 rows). Effectively 1.0-band
  (no uncovered REQ, no orphan AC); scored 0.95 only for one indirect mapping (REQ-5).
- Module count: 5 (M1-M5) — confirmed ≤5. REQ count is 26 due to 6a; modules unchanged at 5.
  Acceptable.
- New contradictions: none found. NFR-1/EXC-4 ("새 지표 추가 없음, 표시·지연 분류만") are
  consistent with the lazy-compute-via-existing-edge-functions design (adapter feeds
  existing pure functions; no new metric defined).
- Brownfield markers: correct — label `brownfield` (spec.md:10), plan.md:5 brownfield TDD
  note, [DELTA] markers on SPEC-047 extension (spec.md:30,37,270).
- Frontmatter: valid (see MP-3).

## Defects Found (this iteration)

D8. acceptance.md:138 (matrix) — MINOR. The matrix row for REQ-050-2 cites only
   "AC-M1-1, E4", but REQ-050-2 introduces six endpoints whose coverage is actually spread
   across AC-M1-1 (/story-clusters), AC-M1-3/AC-M1-4 (/postmortem, /confidence-analysis),
   AC-M3-1 (/pipeline), AC-M5-3 (/news, /trends). Coverage exists; the matrix row understates
   it. Cosmetic precision only — does not create an uncovered REQ. Severity: minor.

D9. spec.md:60-62 — MINOR (carried from iter-1, pre-existing, not introduced). Context prose
   says "FastAPI 7개 엔드포인트" then lists eight paths in the parenthetical. Trivial wording
   slip; actual route count is 7 GET routes + index. Severity: minor.

## Chain-of-Verification Pass

Second-look findings (re-read each section):
- Re-counted REQ ids end-to-end (1..25 + 6a): contiguous, unique. MP-1 holds — not a skim.
- Re-checked every EARS keyword against its clause for the four changed/added REQs (6,6a,7,8):
  6/6a/8 Ubiquitous, 7 State-Driven — all correct. Doubled-"shall" in REQ-8 confirmed as a
  doc-wide template artifact (same in REQ-1/2/6/6a), not unique → not a defect.
- Re-mapped every REQ → AC line-by-line against the matrix AND against the AC bodies (not
  just the matrix): confirmed REQ-4 (AC-M1-5), REQ-7 (AC-M1-4, incl. cache + N-day), REQ-23
  (AC-M5-3) now covered; zoom/pan asserted (AC-M4-1). Confirmed adapter is tested in AC-M1-4,
  not just declared.
- Re-read Exclusions EXC-1..7: all specific, no conflict with included REQs (read-only
  throughout); EXC-3 correctly defers mig034 persona_step_progress live tracking.
- Contradiction scan across requirements: the iter-1 blocking D4 (REQ-6 SQL-stub-fix vs
  REQ-7 edge lazy-compute) is genuinely gone — both now describe one pipeline. No new
  cross-requirement contradiction surfaced.
- Verified against live source that the adapter requirement matches the actual edge function
  signatures (dict/dict|None inputs; Sequence[RoundTrip] input) — the claim is not just
  plausible but necessary. This sharpens confidence that D4 is substantively (not just
  cosmetically) resolved.
No new blocking defects on second pass; only the two minors above (D8, D9).

## Recommendation (PASS)

All four must-pass criteria pass with cited evidence (MP-1 contiguity, MP-2 EARS forms,
MP-3 frontmatter fields, MP-4 N/A). All seven iteration-1 defects — including the blocking
D4 — are substantively resolved and verified against the codebase, not merely asserted:
the dead/broken stub is mandated for removal, the single "raw rows → adapter → edge pure
functions → JSON" pipeline is explicit in both spec and plan, the adapter is both required
(REQ-050-7a) and tested (AC-M1-4), traceability is now matrix-backed with every REQ covered,
and the redaction scope is explicit. Two residual MINOR cosmetic defects (D8 matrix-row
under-citation for REQ-2; D9 "7개 엔드포인트" wording slip) do not warrant a FAIL and can be
tidied opportunistically during run. The SPEC is ready to proceed to /moai run.

Verdict: PASS
