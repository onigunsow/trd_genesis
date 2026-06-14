# SPEC Review Report: SPEC-TRADING-048
Iteration: 2/3
Verdict: FAIL
Overall Score: 0.78

> Reasoning context ignored per M1 Context Isolation. The orchestrator prompt
> contained framing about RD-1..RD-5 and the resolution narrative; this audit
> verifies each claim against the spec.md / plan.md / acceptance.md text and the
> codebase only, not against the author's narrative. The prior report
> (review-1.md) is used solely for regression tracking of D1-D13.

This is a substantially improved SPEC. The iteration-1 hard-FAIL cause (MP-3
missing `labels`) is cleared, and 10 of 13 prior defects are genuinely resolved
with codebase-verified evidence. The remaining FAIL is driven by **two new major
defects introduced by the D6 fix and an undefined M2 verdict mapping** — a
"polish" FAIL, not a structural one. One more iteration should clear it.

## Must-Pass Results

- [PASS] **MP-1 REQ number consistency**: Modular scheme, sequential with no gaps
  or duplicates within each module: CORE-1..3 (spec.md:L98-100), M1-1..8
  (spec.md:L104-111; M1-8 newly added, sequential), M2-1..5 (spec.md:L115-128),
  M3-1..6 (spec.md:L132-142), NFR-1..3 (spec.md:L146-148). PASS.

- [PASS] **MP-2 EARS format compliance**: Every normative requirement uses
  `shall`/`shall not` and maps to a valid EARS pattern; acceptance.md is correctly
  labeled "Given-When-Then 시나리오" (acceptance.md:L3), not mislabeled as EARS.
  No informal "should/may/적당히" in normative text. Two pattern-LABEL mismatches
  remain (D8, D9-M2-4) where the text conforms to a valid pattern under the wrong
  label — logged as minor, not MP-2-breaking (consistent with iteration-1
  treatment). PASS.

- [PASS] **MP-3 YAML frontmatter validity**: `labels` is now present
  (spec.md:L10: `labels: ["trading", "sizing", "validation", "self-improvement"]`).
  Date fields are `created`/`updated` (spec.md:L5-6) — this **matches the project
  convention**, verified against SPEC-TRADING-047 frontmatter which uses
  `created`/`updated` and no `created_at`. Per the explicit project-convention
  instruction, `created` is correct here, NOT a defect. (SPEC-048 additionally
  adds `labels`, which SPEC-047 lacks, so it also satisfies the generic
  contract.) **MP-3 now PASS** — the iteration-1 firewall breach is cleared.

- [N/A] **MP-4 Section 22 language neutrality**: N/A — single-language (Python)
  trading SPEC. "시장 중립(market-neutral)" here means *financial-market*
  portability (KRX→US), unrelated to the 16-programming-language LSP enumeration.
  Auto-pass.

## Category Scores (0.0-1.0, rubric-anchored)

| Dimension | Score | Rubric Band | Evidence |
|-----------|-------|-------------|----------|
| Clarity | 0.75 | 0.75 | Heat (spec.md:L107), COOL_DOWN trigger (L141), 4-way classifier boundaries (L132-137) are now precisely defined. Residual ambiguity: M2-1 verdict cutoffs undefined (N2, L115-124); M3-1 MISSED has no triggering event (N1). One-or-two requirements need interpretation. |
| Completeness | 0.85 | 0.75-1.0 | All sections present; frontmatter complete; Exclusions specific (8 numbered entries incl. new #7/#8, spec.md:L156-163); Resolved Decisions section added (L167-175). Gap: M2-1 dimension point-allocation table and PASS/REVISE/REJECT cutoffs absent. |
| Testability | 0.70 | 0.50-0.75 | Most ACs now binary-testable with pinned values (tolerance 1e-6 acceptance.md:L113; heat formula L39; COOL_DOWN 3회/-5% L117). BUT AC-M3-1 case C ("미진입/hold", acceptance.md:L100) cannot be fed to a roundtrip-close-triggered `classify_roundtrip()` (N1); M2-1 general PASS verdict has no testable cutoff (N2). |
| Traceability | 1.00 | 1.0 | Every REQ now has >=1 AC. The three iteration-1 orphans are covered: M1-7→AC-M1-6 (acceptance.md:L48), M2-3→AC-M2-5 (L82), M3-6→AC-M3-5 (L120). Full forward map verified REQ-by-REQ, no orphaned ACs. |

## Codebase Fact-Check (RD-1..RD-5 grounding)

- **RD-5 / D3 (M2 feasibility)** — VERIFIED. `BacktestResult` (engine.py:L28-34)
  provides exactly cagr/mdd/sharpe/trades/final_equity/equity_curve/daily_returns
  and **no** trade-unit stats (avg_win/avg_loss/profit_factor/win_rate). The
  injected-pure-function redesign (spec.md:L116-119, Exclusion #8) is correct and
  necessary, not speculative.
- **RD-2 (W/R from roundtrips)** — VERIFIED. `RoundTrip.net_pnl = gross_pnl - fees`
  (roundtrips.py:L60-62); `build_roundtrips()` FIFO exists (L127). net_pnl is
  genuinely net-of-fees. (Tax inclusion flagged below as advisory.)
- **RD-4 (heat)** — Implementable; positions carry entry/stop data via watchdog.
- **RD-1 (COOL_DOWN manual /resume only)** — VERIFIED implementable. `auto_resume.py`
  `classify_halt()` (L40-86) already excludes `daily_loss` from SPEC-032
  auto-resume; COOL_DOWN can hook the identical seam. circuit_breaker
  is_halted/trip/reset exist (L26/L73/L87).
- **Migration 033** — VERIFIED. Latest on disk is `032_dashboard_readonly_role.sql`;
  033 is the correct next number (spec.md:L80, L148, plan.md:L75).
- **persona_decisions.confidence NUMERIC(4,2)** — VERIFIED (004_personas.sql:L37);
  `prob_*` columns absent (correctly to be added in 033).
- **SIZING_MODE default `llm_direct`** — VERIFIED (config.py:L214).

Brownfield delta-markers ([EXISTING]/[NEW]/[MODIFY]) and all migration/asset
citations are factually correct.

## Regression Check (D1-D13 from iteration 1)

- **D1** (critical, missing `labels`) — **RESOLVED**: spec.md:L10.
- **D2** (minor, `created` vs `created_at`) — **RESOLVED-by-convention**: project
  uses `created`/`updated` (SPEC-047 verified). Not a defect under project rules.
- **D3** (critical, M2 feasibility) — **RESOLVED**: BacktestResult fields verified;
  scorer is injected pure function (spec.md:L116-119). Engine read confirmed.
- **D4** (major, M3-4 scope contradiction) — **RESOLVED**: M3-4 scoped to
  "DB 스키마(nullable)+저장 경로만"; prompt deferred to Exclusion #7 (spec.md:L140,L162).
- **D5** (major, COOL_DOWN undefined) — **RESOLVED**: trigger 3회/-5%, manual
  /resume only, SPEC-032 excluded (spec.md:L141).
- **D6** (major, classifier no boundaries) — **RESOLVED (boundaries added) but
  introduced N1** (see below): explicit 4-way rules + priority (spec.md:L132-137).
- **D7** (major, heat undefined) — **RESOLVED**: stop-distance risk amount + notional
  fallback + branch (a)(b) (spec.md:L107).
- **D8** (minor, M1-5 mislabel) — **UNRESOLVED**: M1-5 still labeled "(Unwanted)"
  (spec.md:L108) but is an unconditional Ubiquitous `shall not` (no IF/undesired
  trigger).
- **D9** (minor, M2-4 & M1-1 mislabels) — **PARTIALLY RESOLVED**: M1-1 now correctly
  "(Event-Driven)" with WHEN (spec.md:L104). M2-4 still "(Ubiquitous)" while
  "지표 계산 시" reads as Event-Driven WHEN (spec.md:L126).
- **D10** (major, M1 runtime activation) — **RESOLVED**: REQ-048-M1-8 forces
  kelly=0 until M2 PASS; AC-M1-7 covers it (spec.md:L111, acceptance.md:L53-56).
- **D11** (minor, NFR-3 "033 이후" wording) — **RESOLVED**: now "033으로 추가
  (현재 최신 032)" (spec.md:L148).
- **D12** (major, untraced M1-7/M2-3/M3-6) — **RESOLVED**: all three now have ACs.
- **D13** (minor, AC-M3-3 tolerance) — **RESOLVED**: `|sum-1| <= 1e-6` quantified
  (spec.md:L140, acceptance.md:L113).

Resolved: 10/13 fully + D2 by-convention. Unresolved: D8 (minor), D9-M2-4 (minor).
No defect appears unchanged across all candidate iterations → no stagnation flag.

## Defects Found (remaining + new)

D8 (carryover). spec.md:L108 — M1-5 labeled "(Unwanted)" but text is a Ubiquitous
`shall not` (no undesired-condition trigger). Pattern-label mismatch. — Severity: **minor**

D9b (carryover-partial). spec.md:L126 — M2-4 labeled "(Ubiquitous)" but
"지표 계산 시" is a WHEN trigger (Event-Driven). — Severity: **minor**

N1 (NEW — introduced by the D6 fix). spec.md:L132 (M3-1 trigger
"라운드트립(FIFO)이 종료되면") vs L135 (MISSED = "진입하지 않았거나 hold였는데
relative_20d > 0") and acceptance.md:L100 (AC-M3-1 case C "미진입/hold").
A FIFO roundtrip exists only for an entry+exit pair; a non-entered/hold signal
produces **no roundtrip**, so a classifier triggered on roundtrip-close can never
emit MISSED, and AC-M3-1 case C cannot be fed to `classify_roundtrip()` as written.
M3-1 defines an output label (MISSED) with no triggering event and no source data
structure. Internal contradiction (CN-1) + untestable AC. — Severity: **major**

N2 (NEW — newly isolable). spec.md:L115-124 (M2-1) — the 0-100 score →
PASS/REVISE/REJECT verdict mapping has **no defined cutoffs**, and per-dimension
point maxes for expectancy and profit_factor are unspecified (only 표본수 0/15/20,
MDD>=50%→0, robustness 5yr→0, param>7→penalty are given). M2-1's verdict is the
gating output for M1-8 (kelly=0 until PASS) and M2-5 (block A/B), yet no AC can
construct a genuine PASS because no PASS threshold exists. AC-M2-1's REJECT is
testable only because the degenerate input trivially fails (sample<30=0, negative
expectancy). The general verdict boundary is underspecified. — Severity: **major**

Advisory (non-blocking). plan.md:L89 — expectancy uses roundtrips `net_pnl`, which
is `gross - fees` only. Korean 거래세 (0.18% sell tax) must be confirmed to be
inside the order `fee` field, else expectancy overstates edge. The plan already
flags this as a run-phase check; acceptable to defer, but worth pinning the
net-of-tax assumption in M2-1 to avoid silent edge inflation. — Severity: advisory

## Chain-of-Verification Pass

Second-look findings (re-read: frontmatter L1-10, every REQ L98-148, every AC,
Exclusions L156-163, Resolved Decisions L167-175, and the codebase seams):

- Re-verified frontmatter line-by-line and against SPEC-047 — `created`/`updated`
  is the genuine project convention; `labels` present. No skim artifact.
- Re-checked traceability for **every** REQ (not a sample) — full coverage
  confirmed; the three iteration-1 orphans are closed. No new orphaned ACs.
- Re-read M3-1 against `build_roundtrips()` semantics — surfaced N1 (MISSED is
  unproducible under the roundtrip-close trigger); this was masked in iteration 1
  because D6 had no boundaries at all, so the fix exposed the gap.
- Re-read M2-1 scoring against AC-M2-1/AC-M1-7 — confirmed N2: no AC forces a real
  PASS verdict (gate state is mockable in AC-M1-7), so the missing cutoffs slip
  past per-AC testing but leave the requirement's core output undefined.
- Confirmed RD-3/Exclusion #7 (confidence prompt deferred) and RD-5/Exclusion #8
  (engine not rewritten) are internally consistent with M3-4 and M2-1 — no new
  contradiction there.

## Recommendation (FAIL — fixes for manager-spec, prioritized)

1. **(Major, N1)** Resolve the MISSED contradiction. Either (a) re-scope M3-1's
   MISSED to a separate requirement with its own trigger and data source (e.g.
   "결정 시점에 진입하지 않은 신호를 평가할 때(WHEN), …") drawing from
   persona_decisions/system_state rather than roundtrips, OR (b) drop MISSED from
   the roundtrip-close classifier and the M3-1 boundary set this SPEC, deferring
   it to a follow-up. Update AC-M3-1 case C to match whichever path is chosen so
   it is executable against a named function.

2. **(Major, N2)** Pin M2-1's verdict mapping: give each of the 5 dimensions a
   point budget summing to 100 (expectancy and profit_factor maxes are currently
   missing) and define the numeric PASS/REVISE/REJECT cutoffs on the 0-100 total.
   Add one AC that constructs a genuine PASS (not just the degenerate REJECT) so
   the verdict boundary is binary-testable.

3. **(Advisory)** State in M2-1 whether `net_pnl` is net-of-tax; if tax is not in
   the `fee` field, require the scorer to subtract 거래세 via an injected adapter
   (consistent with the market-neutral CORE constraint).

4. **(Minor, D8/D9b)** Relabel M1-5 as Ubiquitous (`shall not`) and M2-4 as
   Event-Driven (or rephrase "지표 계산 시" to a ubiquitous invariant) so the EARS
   labels match the requirement text.

**On RD-1..RD-5 (focus area):** All five resolved decisions are now internally
consistent and grounded in verified code (RD-5 engine fields, RD-1 auto_resume
seam, RD-2 net_pnl, migration 033). The two remaining majors are NOT in the RD
set — they are (N1) a side-effect of fleshing out D6's classifier and (N2) an
M2 scoring detail that was always thin and is now the last undefined threshold.
This SPEC is one focused iteration away from PASS.

Verdict: FAIL
