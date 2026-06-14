# SPEC Review Report: SPEC-TRADING-048
Iteration: 3/3 (final allowed iteration)
Verdict: PASS
Overall Score: 0.93

> Reasoning context ignored per M1 Context Isolation. The orchestrator prompt
> contained the author's resolution narrative (N1/N2/net-of-tax/D8/D9 framing);
> this audit verifies every claim against spec.md / plan.md / acceptance.md text
> and the codebase only, not against the narrative. Prior reports (review-1.md,
> review-2.md) are used solely for regression tracking.

The two iteration-2 majors (N1, N2) are **genuinely resolved** with feedable
inputs and concrete, binary-testable cutoffs — verified line-by-line, not taken
on the author's word. D9 is resolved; D8 is reduced to an at-most-borderline
residual minor that does not breach MP-2. Net-of-tax is now pinned in M2-1 with a
run-phase verification note in plan. No new contradictions were introduced by the
revision. All four must-pass criteria PASS.

## Must-Pass Results

- [PASS] **MP-1 REQ number consistency**: Modular scheme, sequential, no gaps/dupes
  within each module: CORE-1..3 (spec.md:L99-101), M1-1..8 (L105-112), M2-1..5
  (L116-135), M3-1..6 (L139-151), NFR-1..3 (L155-157). PASS.

- [PASS] **MP-2 EARS format compliance**: Every normative requirement uses
  `shall`/`shall not` and maps to a valid EARS pattern; acceptance.md is correctly
  labeled "Given-When-Then 시나리오" (acceptance.md:L3), not mislabeled as EARS. No
  informal "should/may/적당히" in normative text. D8 (M1-5 "Unwanted" label) is a
  borderline label, but the body is now a clean prohibition `shall not`
  (spec.md:L109) that conforms to a valid EARS structure — not MP-2-breaking. PASS.

- [PASS] **MP-3 YAML frontmatter validity**: `id` (SPEC-TRADING-048), `version`
  (0.3.0), `status` (draft), `created`/`updated` (spec.md:L5-6, project convention
  verified against SPEC-047 in iteration 2 — `created` is correct here),
  `priority` (high), `labels` (`["trading","sizing","validation",
  "self-improvement"]`, spec.md:L10). All required fields present, correct types.
  PASS.

- [N/A] **MP-4 Section 22 language neutrality**: N/A — single-language (Python)
  trading SPEC. "시장 중립(market-neutral)" here means *financial-market*
  portability (KRX→US), unrelated to the 16-programming-language LSP enumeration.
  Auto-pass.

## Category Scores (0.0-1.0, rubric-anchored)

| Dimension | Score | Rubric Band | Evidence |
|-----------|-------|-------------|----------|
| Clarity | 0.90 | 0.75-1.0 | N1 resolved: MISSED now has a decision-cycle trigger + named source data (spec.md:L139,L144-145). N2 resolved: every dimension has a concrete scoring map and numeric verdict cutoffs (L122-131). TP/FP overlap is deterministically resolved by the priority rule (L146). Residual: only the D8 borderline label. |
| Completeness | 0.95 | 0.75-1.0 | All sections present; frontmatter complete; Exclusions specific (8 entries, L165-172); Resolved Decisions (L180-184). Added: 5-dimension point table + cutoffs (L122-131), positive PASS AC (acceptance.md:L67-70), explicit net-of-tax clause (spec.md:L121). |
| Testability | 0.90 | 0.75-1.0 | AC-M3-1 case C (미진입/HOLD, no roundtrip) is now feedable from persona_decisions/risk_reviews rows (acceptance.md:L105). AC-M2-1b constructs a genuine PASS (~92, arithmetic re-verified). AC-M2-1 yields REJECT via firewall+expectancy. All cutoffs are pinned numbers. |
| Traceability | 1.00 | 1.0 | Every REQ has >=1 AC; full forward map re-verified REQ-by-REQ (see CoV). No orphaned ACs. |

## Regression Check (iteration-2 defects)

- **N1** (major — MISSED classification contradiction) — **RESOLVED**.
  - Trigger re-scoped from "WHEN roundtrip closes" to "결정 결과 평가 사이클(일일
    postmortem 배치)이 실행되면(WHEN)" (spec.md:L139); plan.md:L69 explicitly
    states "라운드트립 종료 hook 아님". Trigger label now consistent.
  - Classification unit changed 라운드트립→결정(decision); function renamed
    `classify_decision_outcome()` (spec.md:L139, plan.md:L65). VERIFIED.
  - Dual path: 경로1 entered+closed (roundtrip exists) → TRUE_POSITIVE/
    FALSE_POSITIVE/REGIME_MISMATCH from roundtrip P&L + relative returns
    (L140-143); 경로2 non-entered (hold / risk_reviews REJECT|HOLD, no roundtrip)
    → MISSED from relative_20d>0 (L144). Source = persona_decisions(all) +
    risk_reviews(verdict) + roundtrips(LEFT JOIN) (L145). MISSED is now genuinely
    producible without a roundtrip.
  - AC-M3-1 (acceptance.md:L101-108) covers all 4 labels (A=TP, B=FP, C=MISSED via
    HOLD row, D=REGIME_MISMATCH) with feedable inputs; case C explicitly notes
    "roundtrip이 없어도 persona_decisions/risk_reviews 행으로 입력 산출 가능".

- **N2** (major — M2-1 verdict mapping) — **RESOLVED**.
  - 5 dimensions × 20 = 100 (spec.md:L116). Concrete scoring maps for all five:
    expectancy (L123), profit_factor (L124, formula `40*(PF-1.0)` checked:
    PF=1.0→0, PF=1.5→20), sample (L125), MDD-risk (L126), robustness (L127).
  - Cutoffs (L128-131): PASS = total>=70 AND no zero-dimension firewall AND
    expectancy>0; REVISE = 50-69; REJECT = <50 OR any zero-dim OR expectancy<=0.
  - AC-M2-1 (acceptance.md:L62-65) yields **REJECT** for the current negative edge
    (sample 8 → 표본수 0 firewall, expectancy<=0). AC-M2-1b (L67-70) demonstrates a
    **genuine PASS** (~92): expectancy 20 + PF 20 + sample 20 + MDD 12
    (=20·(1−0.2/0.5)) + robustness 20. Arithmetic independently re-verified = 92.

- **Net-of-tax** — **RESOLVED**. M2-1 [거래세 명시] (spec.md:L121) requires
  expectancy/profit_factor/avg_win/avg_loss to use P&L net of fees **and** the
  0.18% sell tax; "그로스 손익으로 채점해서는 안 된다". AC-M2-1 echoes it
  (acceptance.md:L63). plan.md risk table (L95) has the run-phase verification note
  with a net-correction fallback; edge case acceptance.md:L153 mirrors it.
  Codebase fact-check: `RoundTrip.net_pnl = gross_pnl − fees`, fees derived from
  the order `fee` field (roundtrips.py:L51-62,142,177-178) — net-of-fees only.
  Whether 0.18% tax is inside `fee` is correctly a data-population question; the
  SPEC does not falsely assert it is already included — it *requires* net-of-tax
  and provides the run-phase guard. Correct handling at SPEC stage.

- **D8** (minor — M1-5 EARS label) — **SUBSTANTIALLY RESOLVED (residual minor)**.
  M1-5 (spec.md:L109) keeps the "Unwanted" label but rewrites the body as a clean
  prohibition (`confidence ... 증가시켜서는 안 된다 / shall not`). A prohibition of
  unwanted size-amplification is a defensible Unwanted-prevention requirement, and
  the text conforms to a valid EARS structure. Strict reading would prefer
  Ubiquitous, so a residual minor remains — non-blocking.

- **D9** (minor — M2-4 EARS label) — **RESOLVED**. M2-4 reworded to "시스템의 모든
  지표 계산은 항상 ... 회피해야 한다" (spec.md:L134) — a genuine Ubiquitous
  invariant. Label now matches the statement.

## Re-confirmation (orchestrator checklist)

- **Acceptance coverage**: full forward map verified — CORE-1..3, M1-1..8,
  M2-1..5, M3-1..6, NFR-1..3 each have >=1 AC; no orphaned ACs. Traceability 1.00.
- **Requirement modules <= 5**: exactly 5 (CORE, M1, M2, M3, NFR; spec.md:L95).
- **Brownfield delta markers**: [EXISTING] table (L61-79), [NEW] M2/M3, [MODIFY]
  M1; migration latest 032 → next 033 (L81). VERIFIED on disk:
  `src/trading/db/migrations/032_dashboard_readonly_role.sql` is the latest; 033 is
  correct.
- **Frontmatter**: valid, `labels` present, `created`/`updated` per project
  convention (see MP-3).

## New-Contradiction Scan

- TP (L141) vs FP (L142) can co-match (realized>0, rel5d>0, rel20d<0,
  conf>=0.6); resolved deterministically by priority FALSE_POSITIVE >
  TRUE_POSITIVE (L146). Not a contradiction — handled.
- REGIME_MISMATCH highest priority (L146); MISSED mutually exclusive (path-2 only).
  Consistent.
- M1-8 (kelly=0 until M2 PASS, L112) ↔ M2-5 (block A/B until PASS, L135) explicitly
  linked; AC-M1-7 tests it. Consistent.
- Thresholds consistent across spec/plan/acceptance: heat 0.08, COOL_DOWN 3회/-5%
  manual-resume-only, EXP_FULL, sample 30/100/200, MDD 50% firewall.
- Scoring boundaries are continuous (no gap/discontinuity at 30, 100, 200, 50%).
- AC-M2-1b "약 92점" matches the dimension maths exactly — no inflated claim.
- No new contradictions found.

## Chain-of-Verification Pass

Second-look (re-read: frontmatter L1-11; every REQ L99-157; every AC; Exclusions
L165-172; Resolved Decisions L180-184; plan.md L40-100; and codebase seams
roundtrips.py, db/migrations):

- Re-read M3-1 end-to-end against build_roundtrips() semantics: confirmed MISSED is
  now producible from persona_decisions/risk_reviews without a roundtrip — the
  iteration-2 N1 contradiction is genuinely gone, not papered over.
- Recomputed AC-M2-1b independently (20+20+20+12+20=92>=70, all non-zero,
  expectancy>0 ⇒ PASS) and re-derived the profit_factor partial-credit formula
  (`40*(PF-1.0)`) — both correct. AC-M2-1 REJECT path holds via the firewall.
- Verified net_pnl against source (net-of-fees only) and confirmed the SPEC
  requires net-of-tax with a run-phase guard rather than mis-asserting current
  behavior — honest framing.
- Re-checked traceability for **every** REQ (not a sample) — no orphans, full
  coverage.
- One non-defect observation: a path-2 hold with relative_20d<=0 receives no label
  (an implicitly "correct hold"). This is acceptable for an error-attribution
  postmortem loop and is not asserted otherwise by any AC — not a defect.

## Recommendation (PASS)

This SPEC is implementation-ready. Rationale by must-pass criterion:
- **MP-1**: modular REQ numbering sequential, no gaps/dupes (spec.md:L99-157).
- **MP-2**: all normative statements `shall`/`shall not` mapping to valid EARS;
  acceptance.md correctly Given-When-Then (acceptance.md:L3).
- **MP-3**: frontmatter complete incl. `labels` (spec.md:L10); `created`/`updated`
  per verified project convention.
- **MP-4**: N/A (single-language Python).

Both iteration-2 majors (N1, N2) are resolved with feedable inputs and binary
cutoffs; net-of-tax is pinned with a run-phase guard; D9 resolved; D8 reduced to a
non-blocking residual minor. No new contradictions.

Optional (non-blocking, may be deferred to Run): relabel M1-5 from "Unwanted" to
"Ubiquitous" for strict EARS-label precision — the requirement text is already
correct.

Verdict: PASS
