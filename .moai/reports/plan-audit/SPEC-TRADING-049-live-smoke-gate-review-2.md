# SPEC Review Report: SPEC-TRADING-049
Iteration: 2/3
Verdict: PASS
Overall Score: 0.95

> Reasoning context ignored per M1 Context Isolation. The spawn prompt restated the
> author's claims about which defects were fixed; none of those claims were taken at
> face value. Every prior defect was re-verified against the actual revised
> spec.md / acceptance.md / plan.md text and against the live codebase.

## Must-Pass Results

- [PASS] **MP-1 REQ number consistency**: Modular scheme, sequential, no gaps/dupes.
  M1 1-4 (spec.md:L122,L125,L127,L130); M2 1-5 (L135,L145,L150,L153,L156);
  M3 1-4 (L163,L167,L171,L174); NFR 1-3 (L179,L182,L186). Uniform numbering,
  consistent with project convention (SPEC-048). 16 requirements total.

- [PASS] **MP-2 EARS format compliance**: All 16 requirements carry a declared
  pattern matching their text. Verified each: Ubiquitous "the system shall"
  (M1-1 L122, M1-2 L125, M1-4 L130, M2-4 L153) and Korean-localized Ubiquitous
  "시스템은 … 해야 한다(shall)" (M2-2 L145, NFR-1 L179, NFR-2 L182);
  Event-Driven When/shall (M2-1 L135, M3-2 L167); State-Driven While/shall
  (M2-5 L156, M3-3 L171, NFR-3 L186); Unwanted If/then/shall (M1-3 L127, M2-3 L150,
  M3-1 L163, M3-4 L174). The bilingual EARS form is consistent project convention;
  keyword skeleton + subject intact on every entry. D1 deviations from iteration 1
  are resolved.

- [PASS] **MP-3 YAML frontmatter validity**: id "SPEC-TRADING-049" (L2),
  version "0.2.0" (L3), status "draft" (L4), created 2026-06-14 (L5), priority
  "high" (L8), labels array (L10). `created` is the project house field for
  `created_at` (accepted in prior audits). All present, correct types.

- [N/A] **MP-4 Section 22 language neutrality**: Single-project Python / KIS-API /
  Korean-equities trading SPEC (L102-104). No multi-language tooling, no
  language-server tool names. Auto-passes.

## Category Scores (0.0-1.0, rubric-anchored)

| Dimension | Score | Rubric Band | Evidence |
|-----------|-------|-------------|----------|
| Clarity | 0.90 | 1.0 | Single unambiguous interpretation per REQ. M2-1 still bundles 5 conditions (a)-(e) (L136-144) but each is now individually tested (S2-S6); M3-1 fuzzy trigger "위험이 있으면" (L163) remains the only minor soft condition. No pronoun ambiguity. |
| Completeness | 0.95 | 1.0 | HISTORY (L15), WHY (L34), BROWNFIELD/WHAT (L65), Environment (L99), Requirements (L116), Operator-confirm gates (L193), Exclusions (L205, 8 entries), Related (L224). AC in dedicated acceptance.md (project convention). Persistence location + mig-034 properly deferred to run with stated criteria (plan.md:L40-53). |
| Testability | 0.95 | 1.0 | Every numbered scenario binary-testable via mock/fake-clock/conftest. The central hard gate (M2-5) now has both directions (S13 allow, S14 block) plus FAIL-not-reinterpreted. No weasel words in normative text. |
| Traceability | 1.0 | 1.0 | Every REQ has coverage; every scenario tag references a valid REQ. Forward gaps from iteration 1 (M1-1, M1-4, M2-5) all closed by S11/S12/S13/S14. No orphaned ACs, no uncovered REQs. |

## Regression Check (prior defects D1-D6)

- **D4 (HIGH) — hard gate both directions**: RESOLVED. Scenario 13 (acceptance.md:L91-96):
  valid PASS record exists → 선행 검사 통과 → promotion ALLOWED, with And-clause
  confirming `live_unlocked` (REQ-MODE-02-6) semantics are unchanged (layered-above).
  Scenario 14 (L98-103): no valid PASS record — explicitly enumerating BOTH the
  never-run state AND prior-FAIL state ("스모크 미실행 또는 직전 verdict가 FAIL") →
  promotion BLOCKED with "스모크 PASS 기록 없음" reason, And "FAIL 기록은 결코 PASS로
  해석되지 않는다". Both tagged [REQ-049-M2-5]. Both concrete and binary-testable.
  The no-record-vs-FAIL distinction the iteration-1 report demanded is explicit.

- **D2 (HIGH) — M1-1 dedicated scenario**: RESOLVED. Scenario 11 (acceptance.md:L76-82),
  tagged [REQ-049-M1-1]: `cli.main(["smoke-gate","--max-qty","1"])` →
  `cmd == "smoke-gate"` branch matches → `_cmd_smoke_gate(rest)` called exactly once
  with `rest = ["--max-qty","1"]`; And the no-typo-mismatch guard
  (`cli.main(["smoke-gat"])` must NOT call the handler → help/error path);
  And handler return exit code propagates to `main()`. Covers dispatch, mismatch
  protection, and exit-code propagation — exactly as required.

- **D3 (MED) — honesty disclosure tag**: RESOLVED. Scenario 12 (acceptance.md:L84-89),
  tagged [REQ-049-M1-4, REQ-045-C4]: disclosure "실행 경로 검증이며 전략 수익성 검증이
  아님" MUST be included in CLI output/report And shown regardless of PASS/FAIL verdict.
  Explicit tag, string-presence binary test.

- **D1 (MED) — EARS subject + HOW relocation**: RESOLVED. M2-2 (L145-149) now leads
  "시스템은 … 결정론적으로 산출해야 한다(shall) … 동일 입력은 항상 동일 판정을 산출해야
  한다(shall)"; the "주입형 순수 함수" HOW is moved into a "> Notes(HOW, 비요구)" block
  (L148-149) outside the requirement. NFR-1 (L179) and NFR-2 (L182-184) likewise lead
  with "시스템은/시스템의 … 해야 한다(shall)"; TDD HOW moved to Notes (L185, "구현 방식은
  plan.md 참조"). Normative text is now WHAT/WHY only.

- **D5 (LOW) — persistence location**: RESOLVED. M2-4 (L153-155) phrases the testable
  contract against behavior (durable evidence snapshot + timestamp; "FAIL은 결코
  PASS로 덮어쓰지 않는다") and explicitly defers the storage backend to run.
  plan.md:L40-53 states the decision criteria (conftest fixture compatibility +
  FAIL-not-overwrite) and "이 두 결정 전까지는 어떤 마이그레이션 파일도 작성하지 않는다."
  S14 enforces the FAIL-not-PASS invariant at AC level.

- **D6 (LOW) — NFR-3 label**: RESOLVED. L186 relabeled Ubiquitous → State-Driven with
  conditional trigger "While 본 SPEC이 DB 스키마 변경을 필요로 하는 동안, the system shall
  … 034로 추가하고 … 스키마 변경이 불필요한 경우 마이그레이션을 추가하지 않는다." Label now
  matches the conditional text.

No defect appears across all iterations unchanged → no stagnation / blocking defect.

## Fact-Check of Brownfield Claims (re-verified against codebase this iteration)

- `broker_truth.confirm_fills` L506, `_inquire_daily_ccld` L234 (TR_ID TTTC8001R/CTSC9115R
  at L259-264), `_apply_live_fills` L315, `BrokerFillInquiryNotImplemented` L75,
  `clamp_sell_to_confirmed` L113, `intraday_reconcile` L167 — all confirmed.
- `order_resolver.resolve_stuck_orders` L107, `SUBMITTED_RESOLVE_WINDOW_SECONDS=900.0` L61 — confirmed.
- `order.py` `_check_live_gate` L32 reads `system_state.live_unlocked`, `submit_order` L224 — confirmed.
- CLI `main()` L84, `cmd, rest = args[0], args[1:]` L95, manual `if cmd ==` dispatch — confirmed.
- Latest migration = 033 (033_edge_hardening.sql); a new one would be 034 — confirmed.
- All [EXISTING] table citations (spec.md:L67-85) accurate. Brownfield delta markers correct.

## New Defects Found

None of major or minor severity.

- (LOW / cosmetic, not counted) S13 (acceptance.md:L95) phrases the positive path as
  "전면 승격이 **허용**된다", which in isolation could be misread as auto-promotion; the
  immediately following And-clause ("기존 live_unlocked 게이트 의미는 변경되지 않고 그
  상위 선행 검사로만 동작한다") disambiguates correctly. No action required; flagged for awareness only.

## Structural Re-checks

- Modules ≤ 5: 4 modules (M1/M2/M3/NFR). PASS.
- Scope creep: Exclusions #1-8 (L205-222) unchanged; new scenarios S11-S14 are
  coverage-only, adding no behavior beyond existing REQs. No new strategy/signal. None.
- Reverse traceability: S1-S14 every tag references an existing REQ; no orphans.

## Chain-of-Verification Pass

Second-look findings (re-read Requirements L116-189 end-to-end, all 14 scenarios +
edge cases, all [EXISTING] rows against source):
- Re-checked REQ numbering end-to-end across all 4 modules — no gap/dupe (not spot-check).
- Re-verified traceability for ALL 16 REQs (not a sample): M2-2/NFR-1/NFR-2/NFR-3 are
  covered via Quality-Gate + DoD sections (acceptance.md:L114-130) rather than numbered
  GWT scenarios — acceptable and consistent with iteration-1 ruling; all others have
  explicit tagged scenarios.
- Re-read Exclusions: 8 specific, non-vague entries; #4/#5 correctly fence the reuse
  boundary; no contradiction with M2-5 (layered-above) or any included REQ.
- Cross-requirement contradiction scan: none.
- Re-examined the two new HIGH-priority additions (S11, S13/S14) line by line: both are
  concrete, binary-testable, correctly tagged, and address the exact iteration-1 gaps.
First pass missed nothing material; no new defect surfaced on second look.

## Recommendation

PASS. All four must-pass criteria pass with cited evidence (MP-1 L122-186; MP-2 per-REQ
pattern verification; MP-3 L2-L10; MP-4 N/A). All six iteration-1 defects (D1-D6) are
independently confirmed RESOLVED in the actual document text, not merely claimed.
Traceability is now complete (1.0): the SPEC's central hard gate (REQ-049-M2-5) has both
directions tested with the no-record/FAIL-verdict distinction made explicit (S13/S14), the
CLI subcommand dispatch is directly asserted with mismatch + exit-code propagation (S11),
and the honesty disclosure is explicitly tagged (S12). Normative EARS text is WHAT/WHY only
with HOW relocated to Notes/plan. Brownfield claims remain 100% code-accurate. No new
defects of major or minor severity were introduced; no scope creep; modules = 4 (≤5).

Verdict: PASS
