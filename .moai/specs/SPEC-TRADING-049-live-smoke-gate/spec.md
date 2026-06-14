---
id: SPEC-TRADING-049
version: 0.3.0
status: implemented
created: 2026-06-14
updated: 2026-06-14
author: oni
priority: high
issue_number: null
labels: ["trading", "live-readiness", "validation", "execution-safety"]
---

# SPEC-TRADING-049: 라이브 스모크 게이트 (REQ-045-C 구현)

## HISTORY

- 0.3.0 (2026-06-14): TDD 구현 완료. 신규 `src/trading/kis/smoke_gate.py`(순수 판정 `evaluate_smoke_evidence`, audit_log 영구기록 `record_smoke_verdict`, 승격 선행검사 `check_smoke_gate_precondition`) + `cli.py` `smoke-gate` 서브커맨드. 마이그레이션 불요(audit_log 재사용). 신규 60 테스트 GREEN, 전체 1626 passed / 회귀 0(pre-existing 6 제외). plan-auditor 2차 감사 PASS 0.95. 기존 라이브 seam(confirm_fills/resolve_stuck_orders/sell_lock) 재사용, 재구현 없음. CI 실거래 미발주(전부 mock).
- 0.2.0 (2026-06-14): plan-auditor 1차 감사(FAIL 0.80, must-pass 전부 PASS, 사실검증 일치) 반영
  iteration 2. D1(EARS 정합): REQ-049-M2-2/NFR-1/NFR-2의 주어를 "시스템"으로·서술을 "...해야
  한다(shall)"로 정정, M2-2/NFR-2 정상요구문에서 HOW(순수함수·TDD)를 Notes/plan으로 이동;
  NFR-3 라벨을 Ubiquitous→State-Driven(조건부)으로 정정. D2(REQ-049-M1-1 전용 인수 시나리오
  추가)·D3(REQ-049-M1-4 정직고지 명시 태그)·D4(REQ-049-M2-5 하드게이트 양방향 인수 2건: PASS
  기록→승격 허용 / 기록 없음→승격 차단)는 acceptance.md에 반영. D5/D6(증거 기록 위치·mig 034
  필요여부 "run 단계 확정" 명문화)는 plan.md에 반영.
- 0.1.0 (2026-06-14): 최초 초안. 전체 프로젝트 평가(2026-06-14)에서 SPEC-045가 명시한
  REQ-045-C(bounded live round-trip 검증 게이트)가 **미구현(ACCIDENTAL-MISSING)** 으로 확인됨.
  라이브 체결조회 seam(`broker_truth.confirm_fills` live 분기, `_inquire_daily_ccld`
  TTTC8001R/CTSC9115R, `_apply_live_fills`, `order_resolver`)은 **이미 코드로 존재**(SPEC-045
  M1/M2 구현분, commit 8161ff2)하나, 실거래 전환 직전 "1회 소액 round-trip으로 실 체결·원장
  일치를 측정 증거로 확인하고 통과 못 하면 live 승격을 차단"하는 **운영 게이트/런북/CLI가 없다**.
  본 SPEC이 그 게이트를 구현한다. development_mode=tdd, brownfield delta.

---

## 배경 (WHY)

운영자는 수일 내 paper → live 전환을 계획하고 있다. SPEC-045는 라이브 실행 경로 안전화의
3개 축을 정의했다:

- 모듈 A(live 체결조회 seam 구현) — **구현됨** (`confirm_fills` live 분기, `_inquire_daily_ccld`).
- 모듈 B(6/8 실패모드 재현 회귀) — **구현됨** (fake clock 기반, `order_resolver` 윈도우 만료).
- **모듈 C(소액 라이브 스모크 게이트, REQ-045-C1..C4)** — **미구현(ACCIDENTAL-MISSING)**.

REQ-045-C는 다음을 요구했으나 코드/CLI/런북으로 실현되지 않았다:

- REQ-045-C1: 최소 수량·최소 종목의 bounded 라이브 실행 스모크 절차(실행 경로만 검증, 전략 검증 아님).
- REQ-045-C2: 소액 BUY→SELL round-trip 1건 완료 시 **관측 가능한 증거** 요구
  ((1) 실제 체결 확인된 live fill, (2) 원장-broker 진실원 정합, (3) submitted 영구 정체 0건).
- REQ-045-C3: 어느 한 증거라도 미충족 시 전면 라이브 승급 **차단**(SPEC-042 AC-5 보완 하드 게이트).
- REQ-045-C4: 본 게이트가 실행 경로 검증이며 전략 수익성 검증이 아님을 리포트에 명시.

이 SPEC은 **새 매매 전략·신호를 추가하지 않으며**, 기존 broker-truth·order_resolver·sell_lock
자산을 재사용하여 운영 게이트(CLI + 판정 + 영구 기록 + live 승격 차단)만 구축한다.

### 정직성 고지 (Honesty)

- 본 게이트는 **실행 경로(execution path)만** 검증한다. 전략이 수익을 내는지(기대값/Sortino/알파)는
  **검증하지 않으며**, 그것은 SPEC-044(측정 인프라) + SPEC-046/048(사이징·검증 게이트) 소관이다.
- 실제 라이브 주문 실행 자체는 운영자가 live 자격증명으로 수행하는 런북 흐름이다. 자동화 테스트는
  live POST/inquiry 응답을 **mock** 하여 게이트 로직·증거 판정·차단을 검증한다. CI에서 실거래를 발주하지 않는다.
- live TR_ID/필드명(`TTTC8001R`/`CTSC9115R`, `ODNO`/`CCLD_QTY`/`CCLD_AVG_UNPR`)의 최종 실검증은
  운영자 1회 라이브 실행으로 확정되며(SPEC-045 [확인 필요-1/2]), 본 게이트 절차가 그 검증을 **포함**한다.

---

## 기존 시스템 컨텍스트 (BROWNFIELD)

### [EXISTING] 그대로 재사용하는 자산 (코드로 검증된 실제 위치)

| 영역 | 위치 | 역할 |
|------|------|------|
| live 체결조회 단일경로 | `src/trading/kis/broker_truth.py` `confirm_fills()` (L506) | source=execution_inquiry(live)/balance_reconcile(paper) 분기 |
| live 일별주문체결조회 | `src/trading/kis/broker_truth.py` `_inquire_daily_ccld()` (L234) | TR_ID `TTTC8001R`/`CTSC9115R`, `client.get()` → SPEC-043 페이서 경유. [확인 필요-1/2] 마커 L249~ |
| live fill 매칭(위조 금지) | `src/trading/kis/broker_truth.py` `_apply_live_fills()` (L315) | `ODNO` 매칭, `CCLD_QTY`/`CCLD_AVG_UNPR` 추출(L428), no fabrication |
| 미확인 seam 가드 | `src/trading/kis/broker_truth.py` `BrokerFillInquiryNotImplemented` (L75) | TR_ID 미검증 시 raise(절대 fabricate 안 함) |
| phantom 매도 clamp | `src/trading/kis/broker_truth.py` `clamp_sell_to_confirmed()` (L113) | broker 확정수량으로 매도 수량 제한 |
| 인트라데이 정합 | `src/trading/kis/broker_truth.py` `intraday_reconcile()` (L167) | broker 잔고 vs 로컬 원장 정합 |
| stuck 해소 | `src/trading/kis/order_resolver.py` `resolve_stuck_orders()` (L107) | 15분 윈도(`SUBMITTED_RESOLVE_WINDOW_SECONDS`), `BrokerFillInquiryNotImplemented` 처리 |
| in-flight 매도 락 | `src/trading/kis/sell_lock.py` `guard_sell()` (L197), `set_sell_inflight()` (L140), `clear_sell_inflight()` (L170) | submitted leg + cooldown leg, 이중매도 방지 |
| live 주문 게이트 | `src/trading/kis/order.py` `submit_order()` (L224), `_check_live_gate()` (L33) | `live_unlocked` 게이트(REQ-MODE-02-6), live POST 분기(현재 테스트 미커버) |
| 실현손익 집계 | `src/trading/edge/realized_pnl.py` `aggregate_realized_pnl_cum()` (L98) | 누적 realized_pnl_cum |
| 라운드트립 | `src/trading/edge/roundtrips.py` `build_roundtrips()` (L127) | FIFO round-trip 생성 |
| CLI 디스패치 | `src/trading/cli.py` `main()` (L84), `cmd == "..."` 수동 분기 + `_cmd_*` 핸들러(예: `_cmd_resolve_orders` L306, `_cmd_aggregate_pnl` L380) | argparse subparser 아님 — `cmd, rest = args[0], args[1:]` 패턴 |
| 모드/설정 | `src/trading/config.py` `TradingMode.LIVE/PAPER` (L24), `get_settings()` | live/paper 분기 |
| 시스템 상태 | `system_state.live_unlocked` (order.py `_check_live_gate`가 읽음) | live 승격 게이트 플래그 |
| 테스트 픽스처 | `tests/conftest.py` `fake_cursor`/`fake_conn`/`patch_db_connection` | DB mock |

[EXISTING] 마이그레이션 최신 = **033** (SPEC-048). 본 SPEC이 새 컬럼/테이블을 필요로 하면 **034**.

### 현재 격차 (이 SPEC이 메우는 것)

- 운영자가 1회 bounded live round-trip을 실행하고 증거를 수집·판정하는 **CLI 진입점이 없음**.
- REQ-045-C2 증거 체크리스트(BUY 확정 / SELL 확정 / 원장 정합 / submitted 0건 / live TR_ID·필드 실검증)를
  **결정론적으로 판정하는 순수 함수가 없음**.
- PASS/FAIL 판정을 **영구 기록**(증거 스냅샷)하고, FAIL 시 **live 승격을 차단**하는 게이트가 없음
  (현재 `live_unlocked`는 운영자 수동 전환만 존재, 스모크 PASS 선행 요구 없음).

---

## 환경 및 가정 (Environment & Assumptions)

- 언어/런타임: Python 3.13, PostgreSQL, KIS Open API(REST). development_mode = tdd.
- 본 SPEC의 작업 범위는 **kis/ 실행 계층 + cli + (필요 시) db 마이그레이션 한정**.
  SPEC-044 소유 파일(`config.py` 일부, `backtest/*`, `edge/*` 산식, `pyproject.toml`)은 **수정 금지**.
  단, `edge/realized_pnl.py`·`edge/roundtrips.py`는 **읽기(증거 수집)만** 하며 산식을 변경하지 않는다.
- 가정 A-1: live 체결조회 seam(`confirm_fills` execution_inquiry 분기)은 이미 구현되어 있고
  본 게이트는 그것을 **호출**한다(재구현하지 않음).
- 가정 A-2: 실제 live POST(`submit_order` live 분기)와 KIS 응답은 자동화 테스트에서 **mock** 한다.
  운영자의 실제 라이브 발주는 런북(plan.md)의 수동 절차로 분리한다.
- 가정 A-3: 게이트는 `live_unlocked` 승격 **전** 단계에서 동작한다. 게이트 PASS 기록이 없으면
  본 SPEC이 도입하는 검사가 live 전면 승격을 차단한다.
- 가정 A-4: SPEC-048 M2 검증 게이트(전략 양의 엣지 입증)와 본 게이트는 **별개의 독립 게이트**다.
  실거래 확대는 두 게이트(실행 정합 + 전략 엣지)를 **모두** 만족해야 한다(§관련 SPEC).

---

## 요구사항 (Requirements — EARS)

요구사항 모듈 4개: M1(스모크 실행 CLI) + M2(증거 판정·차단) + M3(멱등·안전) + NFR(비기능).

### 모듈 M1 — 소액 라이브 스모크 실행 CLI (bounded round-trip runner)

- **REQ-049-M1-1 (Ubiquitous):** the system **shall** 운영자가 live 자격증명으로 1회 bounded
  round-trip(소액 BUY→SELL)을 실행할 수 있는 CLI 서브커맨드(예: `trading smoke-gate`)를 제공한다.
  이 서브커맨드는 기존 `cli.main()`의 `cmd == "..."` 디스패치 패턴과 `_cmd_*` 핸들러 규약을 따른다.
- **REQ-049-M1-2 (Ubiquitous):** the system **shall** 스모크 실행에 **수량 상한과 금액 상한**을
  주입 파라미터(예: `--max-qty`, `--max-notional`)로 강제하여, 상한을 초과하는 주문을 발주하지 않는다.
- **REQ-049-M1-3 (Unwanted Behavior):** **If** 현재 모드가 PAPER이거나 live 자격증명이 없으면,
  **then** the system **shall** 실거래 발주를 **수행하지 않고** 명확한 사유와 함께 종료한다
  (실거래는 live 모드 + 운영자 자격증명에서만).
- **REQ-049-M1-4 (Ubiquitous):** the system **shall** 스모크 절차가 **실행 경로 검증이며
  전략 수익성 검증이 아님**을 CLI 출력/리포트에 명시한다(REQ-045-C4 충족).

### 모듈 M2 — 증거 판정 + live 승격 차단 (evidence verdict & hard gate)

- **REQ-049-M2-1 (Event-Driven):** **When** 소액 라이브 BUY→SELL round-trip이 한 건 완료되면,
  the system **shall** 다음을 **관측 가능한 증거**로 수집·판정한다(REQ-045-C2):
  - (a) **BUY 확정 체결** — `confirm_fills(execution_inquiry)`로 조회한 live fill에서 `ODNO`가
    매수 주문번호와 매칭되고 `CCLD_QTY > 0`(체결수량), `CCLD_AVG_UNPR > 0`(체결평균가)인 1건.
  - (b) **SELL 확정 체결** — 동일하게 매도 주문번호와 매칭된 live fill 1건.
  - (c) **원장 일치** — broker 진실원(`intraday_reconcile` 기준 broker 잔고)과 로컬 원장
    (orders 상태/positions)이 정합하고, `realized_pnl_cum`이 round-trip 실현손익을 반영.
  - (d) **stuck 'submitted' 0건** — `order_resolver` 통과 후 `submitted`에 영구 정체된 주문 0건.
  - (e) **live TR_ID/필드 실검증** — `_inquire_daily_ccld`의 live TR_ID·output 필드명이 실응답과
    호환됨이 확인됨([확인 필요-1/2] 해소; 운영자 1회 실행으로 충족).
- **REQ-049-M2-2 (Ubiquitous):** 시스템은 수집된 증거(fill 레코드/주문 상태/원장 스냅샷)만으로
  PASS/FAIL 판정과 각 증거 항목별 충족/미충족 결과를 결정론적으로 산출해야 한다(shall).
  동일 입력은 항상 동일 판정을 산출해야 한다(shall).
  > Notes(HOW, 비요구): 이 판정 로직은 I/O·전역 상태·시각·DB 접근이 없는 **주입형 순수 함수**로
  > 구현하여 단위 테스트와 CI mock 검증을 가능하게 한다(구현 방식은 plan.md 참조).
- **REQ-049-M2-3 (Unwanted Behavior):** **If** REQ-049-M2-1의 (a)~(e) 중 어느 하나라도 미충족이면,
  **then** the system **shall** 전면 라이브 승급을 **차단**하고(verdict=FAIL) 어느 증거가 왜
  미충족인지 사유와 함께 운영자에게 보고한다(SPEC-042 AC-5를 보완하는 하드 게이트, REQ-045-C3).
- **REQ-049-M2-4 (Ubiquitous):** the system **shall** 스모크 판정 결과(PASS/FAIL + 각 증거 항목
  상태 + 수집된 fill/원장 스냅샷 + 타임스탬프)를 **영구 기록**한다(증거 스냅샷). 기록 위치는
  run 단계에서 확정하되, FAIL은 결코 PASS로 덮어쓰지 않는다(audit 추적성).
- **REQ-049-M2-5 (State-Driven):** **While** 유효한 스모크 PASS 기록이 존재하지 않는 동안,
  the system **shall** 본 SPEC이 도입하는 검사를 통해 `live_unlocked` 전면 승격을 차단한다
  (스모크 PASS는 live 전면 승격의 **선행 조건**). 기존 `live_unlocked` 게이트(REQ-MODE-02-6)는
  변경하지 않고 그 **상위에** 선행 검사를 둔다.

### 모듈 M3 — 멱등성 / 안전 (idempotency & safety)

- **REQ-049-M3-1 (Unwanted Behavior):** **If** 스모크 실행이 phantom 포지션이나 이중 주문을
  만들 위험이 있으면, **then** the system **shall** 기존 broker-truth(`clamp_sell_to_confirmed`,
  `intraday_reconcile`)와 `sell_lock.guard_sell`을 재사용하여 정확히 1건의 BUY와 1건의 SELL만
  발사되도록 보장한다(새 매도/주문 경로를 신설하지 않는다).
- **REQ-049-M3-2 (Event-Driven):** **When** 스모크 BUY는 체결되었으나 SELL이 미체결로 남으면,
  the system **shall** 미체결 leg를 자동 정리(`order_resolver`의 윈도 기반 `expired` 수렴 + 다음
  사이클 재평가에 위임)하여 게이트 자체가 stuck 주문을 남기지 않게 한다. 미체결을 `filled`로
  **위조하지 않는다**(REQ-045-A2 정합).
- **REQ-049-M3-3 (State-Driven):** **While** 스모크 실행이 진행 중인 동안, the system **shall**
  SPEC-043 전역 TPS 페이서(`client.get()` → `_RateGate`)를 경유하여, 통제되지 않은 추가 KIS
  호출을 발생시키지 않는다(REQ-043-B1 존중).
- **REQ-049-M3-4 (Unwanted Behavior):** **If** 동일 스모크 실행에 대해 판정/기록이 반복 호출되면,
  **then** the system **shall** 이미 기록된 터미널 verdict를 재전이하지 않는다(멱등).

### 모듈 NFR — 비기능 요구사항

- **REQ-049-NFR-1 (Ubiquitous):** 시스템은 본 SPEC 구현 후 기존 테스트의 회귀를 **0** 으로 유지해야
  한다(shall)(pre-existing 6건 제외). 시스템은 페이퍼 동작을 불변으로 유지해야 한다(shall)(paper
  체결확인은 기존 SPEC-029 balance-reconcile 경로를 그대로 사용).
- **REQ-049-NFR-2 (Ubiquitous):** 시스템의 증거 판정 로직(REQ-049-M2-2)은 외부 의존 없이 단위
  테스트로 검증 가능해야 한다(shall). 시스템의 live POST/inquiry 경로는 자동화 테스트에서 mock으로
  게이트 로직·차단을 검증해야 한다(shall)(CI 실거래 발주 금지).
  > Notes(HOW, 비요구): 모든 신규 모듈은 TDD(RED-GREEN-REFACTOR)로 개발한다(개발 방식은 plan.md 참조).
- **REQ-049-NFR-3 (State-Driven):** **While** 본 SPEC이 DB 스키마 변경을 필요로 하는 동안, the
  system **shall** 그 변경을 신규 마이그레이션 **034** 로 추가하고(현재 최신 033), `conftest.py`의
  `fake_cursor`/`fake_conn`/`patch_db_connection` 픽스처와 호환되도록 한다. 스키마 변경이 불필요한
  경우 마이그레이션을 추가하지 않는다(필요 여부는 run 단계에서 확정 — plan.md §마이그레이션 참조).

---

## [확인 필요] 운영자 확인 게이트 (Operator-confirm gates)

정직성 규칙(거짓 금지·검증 후 단언)에 따라, 아래는 **운영자 라이브 자격증명으로 실측 확인 전까지
가정**으로 표기한다. **본 스모크 게이트의 1회 실행이 이 항목들을 해소하는 절차다**(REQ-049-M2-1(e)).

- **[확인 필요-1] live TR_ID 실측:** `TTTC8001R`(3개월 이내)/`CTSC9115R`(3개월 이전)가 live
  `inquire-daily-ccld`에서 당일 체결을 실제로 반환하는지(SPEC-045 [확인 필요-1] 상속).
- **[확인 필요-2] live 응답 스키마:** 체결조회 output 필드명(`ODNO`/`CCLD_QTY`/`CCLD_AVG_UNPR`
  주문번호·체결수량·체결가 매핑)이 `KisResponse._parse`(output/output1)와 호환되는지 실측 확인.
- **[확인 필요-3] 영구 기록 위치:** 증거 스냅샷을 `system_state`/`audit_log`/신규 테이블(mig 034)
  중 어디에 둘지(run 단계에서 conftest 픽스처 호환 기준으로 확정).

## Exclusions (What NOT to Build) [HARD]

이 섹션은 [HARD] 필수이며 범위 폭주를 막는다.

1. **CI 실거래 발주 금지:** 자동화 테스트는 live POST·inquiry 응답을 **mock** 하여 게이트 로직·
   증거 판정·차단만 검증한다. CI나 paper 환경에서 실제 라이브 주문을 발주하지 않는다.
2. **전략/알파 검증 제외:** 본 게이트는 **실행 경로**만 검증한다. 전략 수익성(기대값/Sortino/알파)은
   검증하지 않으며 SPEC-044 + SPEC-046/048 소관이다.
3. **새 매매 전략/신호 추가 금지:** 페르소나/오케스트레이터/리스크/엣지 산식을 변경하지 않는다.
   페이퍼 동작은 불변.
4. **live 체결조회 seam 재구현 금지:** `confirm_fills`/`_inquire_daily_ccld`/`_apply_live_fills`는
   이미 SPEC-045에서 구현됨 — 본 게이트는 그것을 **호출**할 뿐 재구현·병렬경로 신설하지 않는다.
5. **`live_unlocked` 게이트 의미 변경 금지:** 기존 live 차단 게이트(REQ-MODE-02-6)는 변경하지 않고,
   그 **상위에** 스모크 PASS 선행 검사를 추가하는 방식으로만 동작한다.
6. **SPEC-044 소유 파일 수정 제외:** `config.py`(엣지 상수·세율)·`backtest/*`·`edge/*` 산식·
   `pyproject.toml` 수정 금지. `edge/realized_pnl.py`·`edge/roundtrips.py`는 읽기만.
7. **자격증명 로테이션 제외:** KIS 키 발급/회전은 운영자 수동(Non-Goal).
8. **websocket 체결통보 제외:** SPEC-045 ADR에 따라 polling 경로(`inquire-daily-ccld`)만 사용한다.

## 관련 SPEC (Related)

- SPEC-TRADING-045 (live-execution-safety): 본 SPEC이 구현하는 **REQ-045-C(모듈 C)** 의 출처.
  모듈 A/B(live seam + 6/8 재현)는 구현 완료, 모듈 C(스모크 게이트)가 ACCIDENTAL-MISSING이었다.
- SPEC-TRADING-042 (broker-truth ledger): broker 단일진실원·`order_resolver`·`sell_lock`·
  `intraday_reconcile` 제공. AC-5 live-readiness 게이트를 본 SPEC이 **보완**한다.
- SPEC-TRADING-043 (KIS TPS governance): 전역 페이서. 모듈 M3는 이를 **반드시 존중**한다.
- SPEC-TRADING-029 (fill sync): paper balance-reconcile 경로. 본 SPEC은 이를 **건드리지 않는다**.
- SPEC-TRADING-048 (edge-hardening): M2 검증 게이트(전략 양의 엣지 입증)는 본 게이트와 **별개**.
  실거래 확대는 두 게이트(실행 정합 + 전략 엣지)를 **모두** 만족해야 한다.
- SPEC-TRADING-044 (measurement infra): 전략 측정 소관. 본 SPEC과 **경계 분리**(산식 파일 미접촉).
