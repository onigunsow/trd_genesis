# SPEC-TRADING-049 인수 기준 (acceptance.md)

라이브 스모크 게이트 (REQ-045-C 구현). 모든 시나리오는 Given-When-Then. live POST/inquiry는 mock,
fake clock, conftest 픽스처(`fake_cursor`/`fake_conn`/`patch_db_connection`) 기반 결정론적 검증.

---

## 시나리오 1 — 완전 PASS 경로 (정상 round-trip) [REQ-049-M2-1, M2-4]

- **Given** live 모드 + 운영자 자격증명(mock client), 상한 `--max-qty 1`,
  그리고 mock `inquire-daily-ccld`가 BUY/SELL 각 1건을 `ODNO` 매칭 + `CCLD_QTY>0` + `CCLD_AVG_UNPR>0`로 반환,
- **When** `trading smoke-gate --max-qty 1` 가 1회 BUY→SELL round-trip을 실행하고 증거를 판정하면,
- **Then** verdict = **PASS** 이고 증거 항목 (a)BUY확정 / (b)SELL확정 / (c)원장정합 / (d)stuck 0 / (e)TR_ID·필드 호환이 **전부 충족**으로 표시되며,
- **And** 판정 결과(PASS + 증거 스냅샷 + 타임스탬프)가 영구 기록되고,
- **And** 출력에 "실행 경로 검증이며 전략 수익성 검증이 아님" 고지가 포함된다.

## 시나리오 2 — BUY 미확정 → FAIL·차단 [REQ-049-M2-1(a), M2-3]

- **Given** mock `inquire-daily-ccld`가 BUY 주문번호와 매칭되는 fill을 반환하지 않음(`CCLD_QTY=0` 또는 ODNO 불일치),
- **When** 스모크 게이트가 증거를 판정하면,
- **Then** verdict = **FAIL** 이고 항목 (a)BUY확정 = 미충족, 사유에 "BUY fill 미확인"이 기록되며,
- **And** 전면 라이브 승급이 **차단**되고(live_unlocked 선행 검사 실패), 운영자에게 사유가 보고된다.
- **And** 미확정 BUY는 `filled`로 **위조되지 않는다**.

## 시나리오 3 — SELL 미확정 → FAIL·자동정리 [REQ-049-M2-1(b), M3-2]

- **Given** BUY는 확정되었으나 mock이 SELL fill을 반환하지 않고, fake clock이 order_resolver 윈도(15분)를 경과시킴,
- **When** 스모크 게이트가 SELL 체결을 확인·판정하면,
- **Then** verdict = **FAIL** 이고 항목 (b)SELL확정 = 미충족,
- **And** 미체결 SELL leg는 `order_resolver`에 의해 `expired`로 수렴(위조 없음)하여 게이트가 stuck 주문을 남기지 않으며,
- **And** live 승급이 차단된다.

## 시나리오 4 — 원장 불일치 → FAIL [REQ-049-M2-1(c), M2-3]

- **Given** BUY/SELL 모두 체결 확인되었으나, `intraday_reconcile` 기준 broker 잔고와 로컬 positions가 불일치하거나 `realized_pnl_cum`이 round-trip 실현손익을 반영하지 않음,
- **When** 게이트가 원장 정합을 판정하면,
- **Then** verdict = **FAIL** 이고 항목 (c)원장정합 = 미충족, 사유에 불일치 내역이 기록되며 live 승급이 차단된다.

## 시나리오 5 — stuck 'submitted' 잔존 → FAIL [REQ-049-M2-1(d)]

- **Given** `resolve_stuck_orders` 통과 후에도 `submitted` 상태에 정체된 주문이 1건 이상 남음,
- **When** 게이트가 stuck 개수를 판정하면,
- **Then** verdict = **FAIL** 이고 항목 (d)stuck 0건 = 미충족, live 승급이 차단된다.

## 시나리오 6 — live TR_ID/필드 미호환 → FAIL (seam 가드) [REQ-049-M2-1(e)]

- **Given** mock `_inquire_daily_ccld`가 `BrokerFillInquiryNotImplemented`를 raise(TR_ID 미검증)하거나 output 필드명이 `_parse`와 비호환,
- **When** 게이트가 실행되면,
- **Then** verdict = **FAIL** 이고 항목 (e)TR_ID·필드 호환 = 미충족,
- **And** 시스템은 미확정 주문을 `filled`로 위조하지 않고(seam 가드 존중) live 승급을 차단한다.

## 시나리오 7 — PAPER 모드/무자격증명 거부 [REQ-049-M1-3]

- **Given** 현재 모드가 PAPER 이거나 live 자격증명이 없음,
- **When** `trading smoke-gate` 가 호출되면,
- **Then** 시스템은 실거래를 **발주하지 않고** 명확한 사유와 함께 종료(비-0 종료코드)한다.

## 시나리오 8 — 상한 초과 발주 차단 [REQ-049-M1-2]

- **Given** `--max-qty 1 --max-notional <소액>` 상한 주입,
- **When** 게이트가 주문 수량/금액을 산정하면,
- **Then** 상한을 초과하는 BUY/SELL은 **발주되지 않으며**, 상한 내 수량으로만 실행되거나 사유와 함께 중단된다.

## 시나리오 9 — 이중 주문 방지 (멱등) [REQ-049-M3-1, M3-4]

- **Given** position_watchdog/Decision 사이클이 동시에 같은 종목을 평가하는 환경,
- **When** 스모크 SELL이 `sell_lock.guard_sell`을 경유해 발사되고 판정/기록이 반복 호출되면,
- **Then** 정확히 1건의 SELL만 발사되고(중복 KIS 매도 0), 이미 기록된 터미널 verdict는 재전이되지 않는다.

## 시나리오 10 — TPS 페이서 경유 [REQ-049-M3-3]

- **Given** 스모크 실행이 진행 중,
- **When** 체결조회/주문이 KIS를 호출하면,
- **Then** 모든 호출이 `client.get()` → SPEC-043 `_RateGate`(전역 페이서)를 경유하며, 통제되지 않은 추가 호출이 발생하지 않는다.

## 시나리오 11 — `smoke-gate` 서브커맨드 디스패치 [REQ-049-M1-1]

- **Given** `cli.main()`이 `cmd, rest = args[0], args[1:]` 디스패치 패턴으로 동작하는 환경,
- **When** `cli.main(["smoke-gate", "--max-qty", "1"])` 가 호출되면,
- **Then** `cmd == "smoke-gate"` 분기가 매칭되어 전용 핸들러(`_cmd_smoke_gate(rest)`)가 정확히 1회 호출되고 `rest = ["--max-qty", "1"]`가 전달되며,
- **And** 알 수 없는 서브커맨드(예: `cli.main(["smoke-gat"])`)는 이 핸들러를 호출하지 않고 도움말/오류 경로로 빠진다(오매칭 없음).
- **And** 핸들러의 반환 종료코드가 `main()`의 종료코드로 그대로 전파된다.

## 시나리오 12 — 정직 고지 명시 ("실행 검증, 전략 검증 아님") [REQ-049-M1-4, REQ-045-C4]

- **Given** 임의의 스모크 실행(PASS 또는 FAIL 무관),
- **When** 게이트가 CLI 출력/리포트를 산출하면,
- **Then** 출력/리포트에 "본 게이트는 **실행 경로 검증**이며 **전략 수익성 검증이 아님**(전략 측정은 SPEC-044 + SPEC-046/048 소관)" 고지 문구가 **반드시 포함**된다,
- **And** 이 고지는 verdict 결과(PASS/FAIL)와 무관하게 항상 표시된다(전략 게이트 SPEC-048 M2와 혼동 방지).

## 시나리오 13 — 하드게이트: 유효 PASS 기록 존재 → 전면 승격 허용 [REQ-049-M2-5]

- **Given** 유효한 스모크 PASS 기록이 영구 저장소에 존재하는 상태,
- **When** 운영자가 `live_unlocked` 전면 승격을 시도하면,
- **Then** 본 SPEC이 도입한 선행 검사가 **통과**하여 전면 승격이 **허용**된다,
- **And** 기존 `live_unlocked` 게이트(REQ-MODE-02-6) 의미는 변경되지 않고 그 상위 선행 검사로만 동작한다.

## 시나리오 14 — 하드게이트: PASS 기록 부재 → 전면 승격 차단 [REQ-049-M2-5]

- **Given** 유효한 스모크 PASS 기록이 아직 존재하지 않는 상태(스모크 미실행 또는 직전 verdict가 FAIL),
- **When** 운영자가 `live_unlocked` 전면 승격을 시도하면,
- **Then** 본 SPEC이 도입한 선행 검사가 **차단**하여 전면 승격을 거부하고, "스모크 PASS 기록 없음" 사유를 운영자에게 보고한다,
- **And** FAIL 기록은 결코 PASS로 해석되지 않는다(REQ-049-M2-4 영구기록 정합).

---

## 엣지 케이스 (Edge Cases)

- BUY 확정 + SELL 부분체결(`CCLD_QTY` < 주문수량): (b)SELL확정 판정 기준을 run에서 명확화(부분=미충족 처리 기본).
- round-trip 실현손익이 음수(소액 손실): 손익 부호는 PASS/FAIL 판정에 무관(실행 정합만 검증) — 시나리오 1 보강.
- 동일 ODNO가 BUY/SELL 양쪽에 중복 등장: 매칭 시 side 구분으로 오매칭 방지.
- mock 응답이 빈 output1(rt_cd≠0): (a)/(b) 모두 미충족 → FAIL(REQ-045-A2 정합).

## 품질 게이트 (Quality Gate Criteria)

- 단위: `evaluate_smoke_evidence`의 (a)~(e) FAIL 분기 + PASS 경로 전부 GREEN.
- 통합: fake client로 BUY→confirm→SELL→confirm→reconcile→resolve→판정 전 흐름 + 차단 경로 mock.
- 회귀: 전체 스위트 회귀 0(REQ-049-NFR-1, pre-existing 6 제외). 페이퍼 balance-reconcile 경로 불변.
- 보안: 출력/기록에 자격증명·토큰 등 비밀값 미노출.

## Definition of Done

- [ ] REQ-049-M1-1..M1-4 (CLI 러너·디스패치·상한·PAPER 거부·고지) 구현 + 테스트 GREEN. (시나리오 7·8·11·12)
- [ ] REQ-049-M2-1..M2-5 (증거 5항목 판정·결정론적 산출·FAIL 차단·영구기록·승격 선행검사 양방향) 구현 + 테스트 GREEN. (시나리오 1~6·13·14)
- [ ] REQ-049-M3-1..M3-4 (단일 주문·자동정리·TPS·멱등) 구현 + 테스트 GREEN. (시나리오 3·9·10)
- [ ] REQ-049-NFR-1..NFR-3 (회귀 0·TDD·마이그레이션 034 호환 또는 불요 확정) 충족.
- [ ] 시나리오 1~14 + 엣지 케이스 인수 통과.
- [ ] [확인 필요-1/2/3] run 단계에서 해소 또는 운영자 M5 절차로 위임 명시.
- [ ] 페이퍼 동작 불변 확인. CI 실거래 미발주 확인.
- [ ] (운영) M5: 운영자 1회 라이브 round-trip 실행 PASS = 최종 게이트.
