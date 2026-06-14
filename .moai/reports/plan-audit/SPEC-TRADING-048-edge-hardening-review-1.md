# SPEC Review Report: SPEC-TRADING-048
Iteration: 1/3
Verdict: FAIL
Overall Score: 0.52

> Reasoning context ignored per M1 Context Isolation. The orchestrator prompt
> contained substantial framing about the SPEC's intent; this audit verifies
> claims against the spec.md / plan.md / acceptance.md text and the codebase
> only, not against the author's narrative.

## Must-Pass Results

- [PASS] **MP-1 REQ number consistency**: The SPEC uses a hierarchical/modular
  REQ scheme (CORE, M1, M2, M3, NFR) rather than flat `REQ-001..N`. Within every
  module the numbering is sequential with no gaps and no duplicates:
  CORE-1..3 (spec.md:L96-98), M1-1..7 (spec.md:L102-108), M2-1..5 (spec.md:L112-121),
  M3-1..6 (spec.md:L125-130), NFR-1..3 (spec.md:L134-136). PASS on
  uniqueness/sequencing. Note: the scheme intentionally deviates from the
  zero-padded flat convention; acceptable for a modular brownfield SPEC.

- [PASS] **MP-2 EARS format compliance**: All normative requirements live in
  spec.md "요구사항 (EARS)" and every one maps to a valid EARS pattern with
  `shall`/`shall not` (e.g., M1-2 Unwanted "만약 kelly_pct <= 0 이면(IF) ...
  금지해야 한다(then shall)" spec.md:L103; M2-1 Event-Driven spec.md:L112).
  acceptance.md is correctly and explicitly labeled "Given-When-Then 시나리오"
  (acceptance.md:L3) — not mislabeled as EARS. No "should/may/적당히" in normative
  text. PASS. Two label/pattern mismatches logged as minor defects (D8, D9) —
  the text conforms to a valid pattern, just under the wrong label.

- [FAIL] **MP-3 YAML frontmatter validity**: Required field **`labels` is absent**
  from the frontmatter (spec.md:L1-10 contains: id, version, status, created,
  updated, author, priority, issue_number — no `labels`). Additionally the
  date field is named **`created`** (spec.md:L5), not the required `created_at`
  (the ISO value 2026-06-14 is present, but the field name diverges from the
  MP-3 contract). Per the M5 firewall, any missing required field = FAIL, and
  this is not compensable by other dimensions. **MP-3 FAIL.**

- [N/A] **MP-4 Section 22 language neutrality**: N/A — this is a single-language
  (Python) trading-system SPEC, not template-bound/universal LSP tooling content.
  "시장 중립(market-neutral)" in this SPEC refers to *financial-market*
  portability (KRX→US equities, spec.md:L42-52), which is unrelated to the
  16-programming-language LSP enumeration that MP-4 governs. Auto-pass.

## Category Scores (0.0-1.0, rubric-anchored)

| Dimension | Score | Rubric Band | Evidence |
|-----------|-------|-------------|----------|
| Clarity | 0.50 | 0.50 | Multiple requirements need interpretation: M1-4 disjunctive outcome "축소하거나 0으로" (spec.md:L105); M3-1 classifier has no decision-boundary rule table (spec.md:L125); M3-5 "증거태그 기준" undefined (spec.md:L129); heat "리스크 노출" undefined (spec.md:L105, OQ-4 L158). A reasonable engineer could implement these differently than intended. |
| Completeness | 0.50 | 0.50 | All prose sections present and Exclusions are specific (6 numbered entries, spec.md:L144-149). BUT frontmatter is missing a required field (`labels`) — the 0.50 anchor ("frontmatter missing one or two fields") applies. M3-1 classification thresholds and M3-5 COOL_DOWN thresholds are substantively undefined. |
| Testability | 0.50 | 0.50 | Several ACs hinge on undefined inputs/thresholds: AC-M3-4 assumes "증거태그 임계를 충족한 상태" (acceptance.md:L92) with no defined threshold; AC-M3-1 cannot deterministically assert the resulting label without classifier boundaries (acceptance.md:L78); AC-M1-4 expects "축소되거나 0" — disjunctive, no single pinned value (acceptance.md:L41); AC-M3-3 "합이 1.0(허용 오차 내)" with tolerance unquantified (acceptance.md:L89). |
| Traceability | 0.50 | 0.50 | Multiple REQs lack a dedicated AC: REQ-048-M1-7 (tick/lot/rounding application, spec.md:L108) — no AC; REQ-048-M2-3 (overfit pre-checklist, spec.md:L119) — no AC; REQ-048-M3-6 (dashboard read-only view, spec.md:L130) — no AC. Also M1-1's half-Kelly 0.5 factor (spec.md:L102) is only indirectly exercised. |

## Defects Found

D1. spec.md:L1-10 — **MP-3 firewall breach**: required frontmatter field `labels`
is missing entirely. — Severity: **critical**

D2. spec.md:L5 — Frontmatter uses `created` instead of the required `created_at`
field name (value is a valid ISO date). — Severity: minor

D3. spec.md:L159 (OQ-5) + plan.md:L48,L83 — **M2 feasibility risk is unresolved.**
The scorer (M2-1, spec.md:L112) requires `avg_win, avg_loss, MDD, active 기간,
파라미터 수` but the SPEC admits it has NOT verified `backtest/engine.py` emits
these. The fallback ("채점기 내 재계산", plan.md:L83) silently assumes the engine
exports raw per-trade/equity-curve data; if it only emits summary metrics, the
scorer cannot recompute and "엔진 재작성 금지" blocks the fix. This is the single
most load-bearing M2 dependency and it is unverified at draft. A one-time Read of
engine.py's return type would settle it and belongs in research/plan BEFORE
approval. — Severity: **critical (blocking for M2)**

D4. spec.md:L128 (M3-4) vs spec.md:L147 (Exclusion #4) + L157 (OQ-3) —
**Internal scope inconsistency.** M3-4 mandates a confidence→bull/base/bear
scenario-probability schema "to secure calibration raw material." But populating
those probabilities requires persona LLM prompt output, which OQ-3 itself flags
as colliding with Exclusion #4 ("새 매매 신호/전략 추가 금지") and defers. As
written, M3-4 delivers an empty schema with no in-scope producer — a hollow
requirement. The "no new signals / proposals-only" boundary is internally
consistent for the *weights* half (M3-3↔Exclusion #5, verified consistent) but
NOT for the *confidence-scenario* half (M3-4). M3-4 must either be explicitly
scoped to "schema/storage only, no population this SPEC" or pull the prompt
change in-scope. — Severity: **major**

D5. spec.md:L129 (M3-5) + L155 (OQ-1) + acceptance.md:L92 (AC-M3-4) —
COOL_DOWN trigger "증거태그 기준/임계" is undefined (counts, drawdown %), and
auto-release vs SPEC-032 interaction is unresolved (OQ-1). The requirement and
its AC are untestable as written — a tester cannot make the trigger fire
deterministically. — Severity: **major (blocking for M3-5)**

D6. spec.md:L125 (M3-1) + acceptance.md:L78 (AC-M3-1) — The 4-way classifier
(TRUE_POSITIVE/FALSE_POSITIVE/MISSED/REGIME_MISMATCH) has **no decision-boundary
rule table**. The AC asserts "분류된다" but cannot assert *which* label without
defined thresholds vs 5d/20d KOSPI relative return. — Severity: **major**

D7. spec.md:L105 (M1-4) + L158 (OQ-4) + acceptance.md:L41 — heat "리스크 노출" is
undefined (notional vs stop-distance risk), stop-less position handling deferred
to "run 단계 확정", and the outcome is disjunctive ("축소하거나 0"). M1-4 is
underspecified and AC-M1-4 dodges by passing heat% as a given. — Severity: major

D8. spec.md:L106 (M1-5) — Labeled "(Unwanted)" but the text is a Ubiquitous
`shall not` (no IF/THEN undesired-condition structure). Pattern mislabel. —
Severity: minor

D9. spec.md:L120 (M2-4) — Labeled "(Ubiquitous)" but "지표 계산 시" is a WHEN
trigger (Event-Driven). Also M1-1 (spec.md:L102) labeled State-Driven but
"주어졌을 때" reads as event/precondition. Pattern-label inaccuracies. —
Severity: minor

D10. spec.md:L156 (OQ-2) — M1's runtime activation is unresolved: whether to
force `kelly_pct=0` (block all trades) or implement-but-leave-inactive until the
scorer PASSes. This changes M1's production behavior and is not pinned by any
AC. — Severity: major (design decision left open)

D11. spec.md:L136 (NFR-3) — Wording "마이그레이션 033 **이후** 순번" reads as
"after 033" (034+), contradicting plan.md:L68 which states 033 IS this SPEC's
migration. Latest existing migration verified as 032 (db/migrations/
032_dashboard_readonly_role.sql), so 033 is correct as the next number; the
NFR-3 phrasing should read "033부터/033을 추가". — Severity: minor

D12. spec.md:L108 (M1-7), spec.md:L119 (M2-3), spec.md:L130 (M3-6) — Three REQs
have no corresponding acceptance criterion (traceability gaps). M3-6 is Optional,
but M1-7 (rounding/tick correctness) and M2-3 (overfit checklist) are normative
and untraced. — Severity: major

D13. acceptance.md:L89 (AC-M3-3) — "합이 1.0(허용 오차 내)" — tolerance is not
quantified; "허용 오차" is a soft term that should specify an epsilon. — Severity:
minor

## Chain-of-Verification Pass

Second-look findings (re-read sections: frontmatter L1-10, every REQ L96-136,
all ACs, Exclusions L144-149, OQ L155-159, plan.md risk table L80-85):

- Re-verified frontmatter line-by-line — confirmed `labels` truly absent and
  `created` vs `created_at` naming (D1, D2). Not a skim artifact.
- Re-checked traceability for **every** REQ, not a sample — surfaced M1-7, M2-3,
  M3-6 as uncovered (D12); confirmed M3-3↔AC-M3-2 and Exclusion #5 are mutually
  consistent (no defect there — the proposals-only boundary holds for weights).
- New defects found on second pass that the first pass had not isolated:
  D6 (M3-1 classifier has no threshold rule table), D11 (NFR-3 "033 이후"
  wording), D13 (unquantified probability tolerance).
- Verified against codebase: latest migration = 032; SIZING_MODE default =
  `llm_direct` (config.py:L214); SPEC-046 exists; engine.py/roundtrips.py/
  vol_target.py exist. The brownfield delta-markers and migration-033 claim are
  factually correct — no defect on those axes.

## Recommendation (FAIL — fixes for manager-spec)

1. **(Critical, D1)** Add a `labels` field to the frontmatter (array), e.g.
   `labels: [edge, sizing, validation, self-improvement, brownfield]`. Rename
   `created` → `created_at` to satisfy the MP-3 field contract (D2).
2. **(Critical, D3 / OQ-5)** Before approval, Read `backtest/engine.py run()`
   return type and state in the SPEC exactly which of `avg_win, avg_loss, MDD,
   active-period, param-count` the engine already emits and which the scorer must
   recompute (and from what raw fields). Resolve OQ-5; do not carry it into Run.
3. **(Major, D4 / OQ-3)** Resolve the M3-4 scope contradiction explicitly: either
   re-scope M3-4 to "schema + storage only, NO probability population this SPEC"
   (and say so in Exclusions), or bring the persona-prompt change in-scope and
   remove it from the no-new-signals exclusion. Update AC-M3-3 accordingly.
4. **(Major, D5/D6/D7 + OQ-1/OQ-4)** Define concrete thresholds now: COOL_DOWN
   trigger counts/drawdown % and auto-release rule (and its SPEC-032
   interaction); the M3-1 classifier decision-boundary table; and the heat
   "리스크 노출" definition with the stop-less fallback rule. These are normative
   requirement inputs, not Run-phase tuning — undefined triggers make M3-5,
   M3-1, M1-4 untestable.
5. **(Major, D10 / OQ-2)** Pin M1's runtime activation decision (force kelly=0
   vs implement-but-inactive-until-PASS) and add an AC for it.
6. **(Major, D12)** Add acceptance criteria for REQ-048-M1-7 (tick/lot/rounding
   application correctness) and REQ-048-M2-3 (overfit pre-checklist).
7. **(Minor, D8/D9/D11/D13)** Correct the EARS pattern labels on M1-5 and M2-4
   (and M1-1); fix NFR-3 wording to "033부터"; quantify the AC-M3-3 probability
   tolerance.

**On the 5 open questions (focus area 1):** All five are genuinely blocking and
should NOT be deferred to Run. OQ-5 and OQ-3 gate M2 feasibility and an M3 scope
contradiction respectively; OQ-1 and OQ-4 leave normative requirements (M3-5,
M1-4) with undefined triggers/metrics; OQ-2 leaves M1 runtime behavior open. The
ACs repeatedly use "Given <undefined-quantity>" to test only the resolvable half
while skipping the unresolved half — a sign the SPEC is not yet implementation-
ready.

Verdict: FAIL
