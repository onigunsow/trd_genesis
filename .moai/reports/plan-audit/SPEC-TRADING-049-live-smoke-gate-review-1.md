# SPEC Review Report: SPEC-TRADING-049
Iteration: 1/3
Verdict: FAIL
Overall Score: 0.80

> Reasoning context ignored per M1 Context Isolation. The spawn prompt contained
> author-side framing and hints about expected defects; all findings below were
> derived solely from spec.md / plan.md / acceptance.md and verified against the
> live codebase. Brownfield claims were independently fact-checked.

## Must-Pass Results

- [PASS] **MP-1 REQ number consistency**: Modular hierarchical scheme, sequential
  with no gaps/dupes within each module. M1: spec.md:L115,L118,L120,L123 (1-4);
  M2: L128,L138,L141,L144,L147 (1-5); M3: L154,L158,L162,L165 (1-4);
  NFR: L170,L172,L175 (1-3). Consistent with the project's established modular
  convention (matches SPEC-048). No lost-requirement signal.

- [PASS] **MP-2 EARS format compliance**: All 16 requirements carry an EARS
  keyword and a declared pattern. Verified leading "the system **shall**" on
  M1-1 (L115), M1-2 (L118), M1-4 (L123), M2-1 (L129), M2-3 (L142), M2-4 (L144),
  M2-5 (L148); If/then/shall on M1-3 (L120-121), M2-3 (L141-142), M3-1 (L154-155),
  M3-4 (L165); When/shall on M2-1 (L128), M3-2 (L158); While/shall on M2-5 (L147),
  M3-3 (L162). Deviations noted as defects (D1) but keyword skeleton intact → PASS.

- [PASS] **MP-3 YAML frontmatter validity**: id "SPEC-TRADING-049" (L2),
  version "0.1.0" (L3), status "draft" (L4), priority "high" (L8),
  labels array (L10). Date present as `created: 2026-06-14` (L5) + `updated` (L6)
  — the project's house field name substituting `created_at` (same convention
  accepted in SPEC-048 audits). All required fields present, types correct.

- [N/A] **MP-4 Section 22 language neutrality**: N/A — single-project SPEC scoped
  to a Python / KIS-API / Korean-equities trading system (spec.md:L94-96). Not a
  multi-language tooling SPEC. No language-server tool names appear. Auto-passes.

## Category Scores (0.0-1.0, rubric-anchored)

| Dimension | Score | Rubric Band | Evidence |
|-----------|-------|-------------|----------|
| Clarity | 0.75 | 0.75 | Minor ambiguity: M3-1 trigger "phantom/이중주문을 만들 위험이 있으면" (L154) is a fuzzy condition; M2-1's nested (a)-(e) bundles 5 testable conditions into one REQ (L129-137). Otherwise unambiguous. |
| Completeness | 0.80 | 0.75 | HISTORY (L15), WHY (L27), BROWNFIELD/WHAT (L58), Environment (L92), Requirements (L109), Exclusions (L193), Related (L212) all present; AC in dedicated acceptance.md (project convention). Open: evidence-persistence location (L190-191) and migration-034 (L175-177) deferred to run. |
| Testability | 0.80 | 0.75 | Most ACs binary-testable via mock/fake-clock (acceptance.md S1-S10). Gap: the SPEC's core hard gate (M2-5) lacks a positive/no-record test (D4). Partial-fill given a default (acceptance.md:L80). No weasel words in normative text. |
| Traceability | 0.78 | 0.75 | Reverse traceability clean (every scenario tag references a valid REQ). Forward gaps: REQ-049-M1-1 untagged (D2), M1-4 only implicit (D3), M2-5 indirectly covered (D4). |

## Defects Found

D1. spec.md:L138 — **EARS subject deviation (minor).** REQ-049-M2-2 uses a
non-"system" subject with the keyword embedded mid-verb: "증거 판정 로직은 …
구현 **shall**한다". EARS Ubiquitous form is "The system shall …". Same pattern
on NFR-1 (L170 "…유지 **shall**한다") and NFR-2 (L172 "…개발 **shall**하며").
Additionally M2-2 prescribes implementation form ("주입형 순수 함수") inside a
requirement — borderline HOW-in-WHAT (RQ-3/RQ-4). Severity: minor.

D2. acceptance.md (whole) vs spec.md:L115 — **REQ-049-M1-1 has no dedicated
acceptance scenario (major).** The CLI-subcommand-exists requirement is only
exercised implicitly (Scenario 1 invokes `trading smoke-gate` but is tagged
[M2-1, M2-4]). No scenario tags M1-1. Uncovered REQ → AC-5 violation. Severity: major.

D3. acceptance.md:L15 vs spec.md:L123 — **REQ-049-M1-4 (honesty disclosure)
covered only implicitly (minor).** Scenario 1's final "And 출력에 '실행 경로
검증…' 고지가 포함된다" demonstrates it, but the scenario is tagged [M2-1, M2-4],
not M1-4. No scenario explicitly traces to M1-4. Severity: minor.

D4. spec.md:L147-150 vs acceptance.md — **The SPEC's central hard gate (M2-5)
is under-tested (major).** M2-5 is State-Driven: "While no valid smoke-PASS
record exists, block live_unlocked promotion." Acceptance scenarios only show the
FAIL-verdict→blocked path (S2-S6). There is (i) no positive scenario showing
"valid PASS record exists → promotion proceeds", and (ii) no scenario for the
no-record-yet state (gate never run) → blocked, which is distinct from a FAIL
verdict. S2 (L22) conflates "FAIL blocks" with "no PASS record blocks" but is
tagged [M2-1(a), M2-3], not M2-5. Since this gate is the entire reason the SPEC
exists, the binary-testability of its normative behavior must be explicit.
Severity: major.

D5. spec.md:L145-146, L190-191 — **REQ-049-M2-4 persistence location deferred
(minor).** "기록 위치는 run 단계에서 확정" leaves the storage backend
(system_state / audit_log / new table mig-034) open. Behavior-level testability
(verdict is persisted and re-readable; FAIL never overwritten by PASS) is
preserved, so this is acceptable for a brownfield plan but should be flagged.
Severity: minor.

D6. spec.md:L175 — **NFR-3 pattern/label mismatch (minor).** Labeled Ubiquitous
but the normative text is conditional ("DB 스키마 변경이 필요하면 … 마이그레이션
034로 추가 shall하며"). A conditional belongs in an Optional/Event/Unwanted form,
or should be split into an unconditional invariant + a conditional clause.
Severity: minor.

## Fact-Check of Brownfield Claims (all VERIFIED against codebase)

1. **Live fill-inquiry seam already exists → REUSE not reimplement: CONFIRMED.**
   `broker_truth.confirm_fills()` source-branches paper=balance_reconcile /
   live=execution_inquiry (src/trading/kis/broker_truth.py:L506-545);
   `_inquire_daily_ccld` with TR_ID `TTTC8001R`/`CTSC9115R` (L234, L259-264);
   `_apply_live_fills` matching `ODNO`/`CCLD_QTY`/`CCLD_AVG_UNPR` (L315, L428-429);
   `BrokerFillInquiryNotImplemented` guard (L75); `order_resolver.resolve_stuck_orders`
   (order_resolver.py:L107) with `SUBMITTED_RESOLVE_WINDOW_SECONDS=900.0` (L61).
   Cited line numbers in spec.md:L64-71 are accurate. Exclusion #4 (no reimplement,
   L203-204) is correct and well-grounded.

2. **CLI uses manual `cmd == "..."` dispatch + `_cmd_*` handlers: CONFIRMED.**
   cli.py `main()` L84, `cmd, rest = args[0], args[1:]` (L95), 25+ `if cmd ==`
   branches incl. `resolve-orders` (L174) → `_cmd_resolve_orders` (L306),
   `aggregate-pnl` (L176) → `_cmd_aggregate_pnl` (L380). Not argparse subparsers.
   spec.md:L75 / plan.md:L20 accurate.

3. **Latest migration 033 → new one would be 034: CONFIRMED.**
   src/trading/db/migrations/ ends at 033_edge_hardening.sql. spec.md:L80, L175 accurate.

4. **`live_unlocked` gate exists in kis/order.py: CONFIRMED.**
   `_check_live_gate` (order.py:L32-47) reads `system_state.live_unlocked`,
   raises `LiveLockedError` when false; called by `submit_order` (L224). Live POST
   branch exists (client.post, tr_id TTTC0801U/TTTC0802U). spec.md:L72, L77 and
   Exclusion #5 (do not change gate semantics, layer above) accurate.

5. **REQ-045-C provenance: CONFIRMED.** SPEC-045 defines REQ-045-C1..C4 (module C,
   execution-only smoke gate) at SPEC-TRADING-045/spec.md:L107-119, referencing
   "SPEC-042 AC-5 보완". SPEC-049's restatement (spec.md:L38-42) is faithful.

6. **Distinction from SPEC-048 M2: CONFIRMED.** SPEC-048 M2 is a strategy/edge
   validation gate (backtest PASS/REVISE/REJECT, ≥70 firewall) — a DIFFERENT gate
   from SPEC-049's execution-path gate. The "two independent gates" claim
   (spec.md:L104-105, L220-221) is accurate.

## Assessment of the 4 Open Questions (NOT blocking — design strength)

- **OQ-1 live TR_ID / OQ-2 field-name compat**: NOT blocking. The SPEC does not
  *assume* these are true; it makes them a FAIL condition via REQ-049-M2-1(e)
  (spec.md:L136-137) backed by the `BrokerFillInquiryNotImplemented` seam guard
  (acceptance.md S6). Fail-safe by construction. Sound.
- **OQ-3 evidence persistence location**: deferred to run (D5) — minor.
- **Partial-fill judgment**: a default IS specified ("부분=미충족 처리 기본",
  acceptance.md:L80). Not fully open — minor.

## CI-Safety Consistency (PASS)
"No real live orders in tests" is specified consistently across all documents:
Honesty notice (spec.md:L52), Assumption A-2 (L100-101), REQ-049-NFR-2 (L173-174),
Exclusion #1 (L197-198), acceptance Quality Gate (acceptance.md:L89-90) and DoD
(L100). No inconsistency found.

## Chain-of-Verification Pass

Second-look findings (re-read Requirements L109-178, acceptance S1-S10 + edge
cases, all [EXISTING] rows against source):
- Re-checked every REQ-049 number end-to-end: no gap/dupe confirmed (not spot-check).
- Re-verified traceability for ALL 16 REQs (not a sample): newly surfaced that
  M1-1 (D2) and M2-5 (D4) are the only requirements whose primary normative
  behavior lacks an explicitly-tagged scenario; M2-2/NFR-1/NFR-2/NFR-3 are covered
  via the Quality-Gate/DoD sections rather than numbered GWT scenarios (acceptable).
- Re-read Exclusions (L193-211): 8 specific, non-vague entries; #4 and #5
  correctly fence the reuse boundary. No contradiction with included requirements.
- Cross-requirement contradiction scan: none. M2-5 (layer above gate) is
  consistent with Exclusion #5 (do not change gate semantics).
- All [EXISTING] table line citations independently confirmed against the actual
  files (see Fact-Check). No fabricated locations.
First pass missed nothing material beyond what is recorded; D4's "no-record vs
FAIL-verdict" distinction was sharpened on second look.

## Recommendation (FAIL — fixable in iteration 2)

The SPEC is technically excellent: brownfield claims are 100% code-accurate,
reuse boundary is correctly fenced, CI-safety is airtight, and the open questions
are handled fail-safe rather than assumed. It fails iteration 1 on traceability/
testability of two requirements — including the gate that is the SPEC's reason to
exist. Fix the following, then re-audit:

1. **(D4, priority HIGH)** Add acceptance scenarios for REQ-049-M2-5 covering BOTH
   missing states: (i) a *valid PASS record exists* → live_unlocked promotion
   proceeds; (ii) *no smoke record at all* (gate never run) → promotion blocked,
   shown as distinct from a FAIL-verdict block. Tag them [REQ-049-M2-5].

2. **(D2, priority HIGH)** Add a scenario tagged [REQ-049-M1-1] asserting the
   `trading smoke-gate` subcommand is registered and dispatches via the
   `cmd == "smoke-gate"` → `_cmd_smoke_gate` path (mirrors `_cmd_resolve_orders`).

3. **(D3, priority MEDIUM)** Either add the [REQ-049-M1-4] tag to Scenario 1's
   honesty-disclosure assertion (acceptance.md:L15) or add a dedicated scenario.

4. **(D1, priority MEDIUM)** Rephrase M2-2/NFR-1/NFR-2 to lead with "The system
   shall …" (or relabel NFR-1/NFR-2 as process constraints outside EARS), and move
   the "pure function" implementation choice out of the normative requirement text
   into the plan/approach.

5. **(D6, priority LOW)** Relabel NFR-3 from Ubiquitous to a conditional EARS
   pattern, or split the unconditional ("new schema → migration 034") from the
   conditional ("if schema change is needed").

6. **(D5, priority LOW)** Acknowledge in REQ-049-M2-4 that the *behavior*
   (durable, re-readable, FAIL-not-overwritten) is the testable contract and the
   backend is a run-phase decision — phrase the AC against behavior, not location.

Verdict: FAIL
