# SPEC-TRADING-049 구현 계획 (plan.md)

라이브 스모크 게이트 (REQ-045-C 구현). development_mode = **tdd** (RED-GREEN-REFACTOR, brownfield).

## 기술 접근 (Technical Approach)

핵심 원칙: **재사용 우선, 신설 최소화.** live 체결조회 seam·order_resolver·sell_lock·broker-truth는
이미 존재한다. 본 게이트는 (1) 그것들을 호출하는 **CLI 러너**, (2) 수집된 증거를 PASS/FAIL로 판정하는
**주입형 순수 함수**, (3) 판정의 **영구 기록 + live 승격 선행 검사** 세 조각만 추가한다.

### 구성 요소 (proposed)

1. **증거 판정 순수 함수** (예: `src/trading/kis/smoke_gate.py` `evaluate_smoke_evidence(...)`)
   - 입력: 수집된 BUY/SELL fill 레코드(ODNO/CCLD_QTY/CCLD_AVG_UNPR), 주문 상태 목록,
     원장 스냅샷(broker 잔고 vs 로컬 positions, realized_pnl_cum delta), stuck submitted 개수,
     live TR_ID/필드 호환 플래그.
   - 출력: `SmokeVerdict`(PASS/FAIL + 각 증거 항목 (a)~(e) 충족/미충족 + 사유 목록).
   - I/O·시각·DB 접근 없음 → 단위 테스트로 모든 분기 검증(REQ-049-M2-2/NFR-2).

2. **CLI 러너** (`src/trading/cli.py` `cmd == "smoke-gate"` → `_cmd_smoke_gate(rest)`)
   - 기존 디스패치 패턴(`main()` L84, `_cmd_resolve_orders`/`_cmd_aggregate_pnl` 규약) 준수.
   - 플래그: `--max-qty`, `--max-notional`, `--ticker`(선택), `--dry-run`.
   - 흐름: live 모드+자격증명 확인(REQ-049-M1-3) → bounded BUY 발주(`submit_order` live, 상한 강제)
     → `confirm_fills(execution_inquiry)`로 BUY 체결 확인 → bounded SELL 발주(`guard_sell` 경유)
     → `confirm_fills`로 SELL 체결 확인 → `intraday_reconcile`로 원장 정합 수집
     → `resolve_stuck_orders`로 stuck 0건 확인 → `evaluate_smoke_evidence` 판정 → 영구 기록 → 출력.
   - 출력 머리말에 "실행 경로 검증, 전략 검증 아님"(REQ-049-M1-4/REQ-045-C4) 명시.

3. **영구 기록 + live 승격 선행 검사**
   - 기록 위치는 [확인 필요-3]에서 확정(system_state 컬럼 / audit_log / 신규 테이블 mig 034).
   - `live_unlocked` 전면 승격 경로에 "유효한 스모크 PASS 기록 존재" 선행 검사 추가(REQ-049-M2-5).
     기존 `_check_live_gate`(order.py L33) 의미는 변경하지 않고 **상위 검사**로만 둔다.

### TDD 사이클 (RED → GREEN → REFACTOR, brownfield)

- 모든 신규 모듈은 테스트 우선. 증거 판정 순수 함수는 (a)~(e) 각 항목 FAIL 분기를 개별 RED로 고정.
- live POST/inquiry는 mock(fake client)으로 주입. fake clock으로 order_resolver 윈도 만료 검증.
- 기존 broker_truth/order_resolver/sell_lock 호출부는 ANALYZE로 동작 파악 후 호출만 추가(PRESERVE).

## 마이그레이션 및 미해결질문 확정 (run 단계 결정 사항) [D5/D6]

다음 2건은 **run 단계에서 확정**한다(spec.md REQ-049-NFR-3 및 [확인 필요-3]과 정합):

1. **증거 영구 기록 위치 (OQ-3 / [확인 필요-3]):** 스모크 판정 결과(PASS/FAIL + 증거 스냅샷 +
   타임스탬프)를 어디에 영속화할지 — 후보: (a) `system_state` 신규 컬럼, (b) `audit_log` 항목,
   (c) 신규 테이블(마이그레이션 034). **결정 기준**: `conftest.py`의 fake_cursor/fake_conn/
   patch_db_connection 픽스처와의 호환성 + FAIL→PASS 미덮어쓰기(REQ-049-M2-4) 보장 용이성.
   → run 단계 M0/M3에서 ANALYZE 후 확정.
2. **마이그레이션 034 필요 여부:** 위 (c)를 택하거나 기존 테이블 스키마 확장이 필요한 경우에만
   신규 마이그레이션 034를 추가한다(현재 최신 033). (a)/(b)로 충분하면 마이그레이션을 추가하지
   않는다(REQ-049-NFR-3의 조건부 State-Driven 라벨과 정합). → run 단계 M0에서 확정.

이 두 결정 전까지는 어떤 마이그레이션 파일도 작성하지 않는다(불필요한 스키마 변경 방지).

## 마일스톤 (Milestones — 우선순위 기반, 시간 추정 없음)

### M0 — 재현/베이스라인 [Priority: High, 선행]
- [확인 필요-1/2/3] 정리: live TR_ID·필드명 호환은 운영자 1회 실행으로 해소함을 게이트 절차에 명시.
- 영구 기록 위치 결정(conftest 픽스처 호환 기준). 마이그레이션 034 필요 여부 확정.

### M1 — 증거 판정 순수 함수 [Priority: High]
- `evaluate_smoke_evidence` + `SmokeVerdict` 자료형 (REQ-049-M2-1/M2-2).
- RED: (a)BUY확정 / (b)SELL확정 / (c)원장정합 / (d)stuck 0 / (e)TR_ID·필드 호환 각각의 FAIL 케이스.
- GREEN: 최소 구현. REFACTOR: 사유 메시지 정리.

### M2 — CLI 스모크 러너 [Priority: High]
- `_cmd_smoke_gate` + `cmd == "smoke-gate"` 디스패치 (REQ-049-M1-1..M1-4).
- 상한 강제(--max-qty/--max-notional), PAPER/무자격증명 거부(REQ-049-M1-3).
- broker-truth·sell_lock·order_resolver 재사용 배선, TPS 페이서 경유(REQ-049-M3-3).

### M3 — 영구 기록 + live 승격 차단 [Priority: High]
- 판정 영구 기록(증거 스냅샷, FAIL은 PASS로 미덮어쓰기) (REQ-049-M2-4).
- `live_unlocked` 전면 승격 선행 검사(스모크 PASS 요구) (REQ-049-M2-5).
- (필요 시) 마이그레이션 034 + conftest 호환.

### M4 — 멱등·안전 + 회귀 [Priority: Medium]
- 단일 BUY/SELL 보장(sell_lock), 미체결 자동정리(order_resolver), 판정/기록 멱등(REQ-049-M3-1..M3-4).
- 전체 테스트 회귀 0 확인(REQ-049-NFR-1). 페이퍼 동작 불변 회귀.

### M5 — 운영자 라이브 1회 실행 (런북) [Priority: 운영 게이트]
- 운영자가 live 자격증명으로 `trading smoke-gate --max-qty 1` 1회 실행 → [확인 필요-1/2] 실측 해소.
- PASS 시 live 전면 승격 허용, FAIL 시 사유 보고·차단. (이 단계는 코드가 아닌 운영 절차)

## 운영자 런북 (Runbook — CLI 초심자 대상, 단계별)

> 아래는 M5에서 운영자가 수행하는 흐름의 초안이다. 실제 명령/플래그는 run 단계에서 확정한다.

1. live 자격증명·`TRADING_MODE=live` 확인. (예: `trading status`로 현재 상태 확인)
2. 소액 스모크 실행: `trading smoke-gate --max-qty 1 --max-notional <소액>` — BUY→SELL 1 round-trip.
3. 출력의 증거 체크리스트 (a)~(e) 전부 PASS인지 확인. FAIL이면 사유 확인 후 차단 유지.
4. PASS 기록 확인 후에야 `live_unlocked` 전면 승격 진행(별도 절차).
5. 미체결이 남으면 `trading resolve-orders --cleanup`로 정리(게이트가 자동 위임하지만 수동 확인).

## 리스크 (Risks)

- **R1 — live TR_ID/필드 미실측:** [확인 필요-1/2]. → 게이트 자체가 검증 절차(M5). 미해소면 FAIL.
- **R2 — 실거래 발주 사고:** 상한 강제(M1-2) + live 모드/자격증명 게이트(M1-3) + CI mock(Exclusions #1).
- **R3 — stuck 주문 잔존:** order_resolver 윈도 위임(M3-2) + 런북 수동 정리(런북 5).
- **R4 — 두 게이트 혼동:** 본 게이트(실행 정합) ≠ SPEC-048 M2(전략 엣지). 출력/리포트에 명시(M1-4).
- **R5 — 기록 위치 미정:** [확인 필요-3]. conftest 픽스처 호환 기준으로 run에서 확정.

## 검증 게이트 (Validation Gates)

- 단위: `evaluate_smoke_evidence`의 (a)~(e) 각 FAIL 분기 + PASS 경로 테스트 GREEN.
- 통합: fake client로 BUY→SELL→confirm→reconcile→resolve 전 흐름 + 차단 경로 mock 검증.
- 회귀: 전체 스위트 회귀 0(REQ-049-NFR-1), 페이퍼 경로 불변.
- 운영(M5): 운영자 1회 라이브 round-trip PASS = 최종 게이트.
