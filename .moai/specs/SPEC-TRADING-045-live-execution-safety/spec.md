---
id: SPEC-TRADING-045
version: 0.1.0
status: draft
created: 2026-06-14
updated: 2026-06-14
author: oni
priority: high
issue_number: null
---

# SPEC-TRADING-045 — LIVE 실행 경로 안전화 (live fill-inquiry seam 구현 + 6/8 회귀 재현 + 소액 라이브 스모크 게이트)

## HISTORY

- 2026-06-14 v0.1.0 (draft): 최초 작성. 라이브 전환 임박 상태에서 실행(execution) 경로가 **미검증·부분 미구현**임을 심층 감사로 확인하여 작성. SPEC-042(broker-truth ledger)가 남긴 `confirm_fills()` live seam(`BrokerFillInquiryNotImplemented`)을 실제 KIS 체결조회로 구현하고, 2026-06-08 "매도 마비" 실패모드를 재현 테스트로 고정하며, 소액 라이브 스모크 게이트를 정의한다.

## 개요 (Overview)

라이브 전환 직전, 주문 **실행/체결 확인** 경로의 두 가지 핵심 공백을 닫는다:

1. **live fill-inquiry seam 미구현** — `src/trading/kis/broker_truth.py:confirm_fills()`의 live 분기는
   현재 `BrokerFillInquiryNotImplemented`를 raise하는 **가드된 seam(stub)**이다(SPEC-042가 의도적으로
   미배선 상태로 남김, REQ-042-A3/A5). 즉 라이브에서 주문은 실제 KIS 체결로 **확인되지 않고**,
   `order_resolver`가 15분 윈도우 후 `expired`로만 수렴시킨다. 라이브 매매가 **진짜 체결 확인 없이**
   돌아가는 상태다.
2. **live 주문 제출 경로 무테스트** — `order.submit_order()`의 live 경로(live_unlocked 게이트 통과 후
   실제 KIS POST)는 테스트로 한 번도 실행된 적이 없음을 감사로 확인.

본 SPEC은 (a) live 체결조회를 실제 KIS 엔드포인트로 구현, (b) 6/8 실패모드(체결확인 실패 →
submitted 영구 정체 → 매도 소실)를 재현 테스트로 고정, (c) 실행 경로만 검증하는 소액 라이브 스모크
게이트를 정의한다. SPEC-043의 전역 TPS 페이서를 반드시 존중한다.

## 배경 및 근본원인 (Why this SPEC exists)

2026-06-08 폭락장(KOSPI −5.5%)에서 시스템은 손절을 **올바르게 결정**했으나 **체결/원장 계층이
실패**했다. 손절 매도 3건이 전부 실패하고 SELL 5건이 `submitted` 상태에 영구히 갇혔다. 근본은
체결 확인 중 `ORDER_SYNTHETIC_ERROR`가 주문을 미해소 상태로 남긴 것이었다(SPEC-039/042 분석).

SPEC-042는 `order_resolver`(15분 후 stuck 주문 만료) + `sell_lock`(중복매도 락) + broker-truth
단일원장(phantom 매도 사전 clamp)으로 **원장(ledger) 수렴**을 구현했다. 그러나 SPEC-042는
live 체결조회를 의도적으로 **미배선 seam**으로 남겼다(`confirm_fills()` live 분기 = raise).
그 결과 라이브에서는:

- 실제 체결을 확인할 broker 데이터 경로가 없어, 정상 체결된 주문도 `confirm_fills`로 확인 불가 →
  15분 후 일괄 `expired`. 즉 라이브에서 **모든** live 주문은 "체결되었어도 만료"된다(원장 부정확).
- 매도가 실제로 체결되었는지 시스템이 알 수 없으므로, round-trip 실현손익·자산 정합이
  broker 진실원에 근거하지 못한다.

운영자는 **수일 내** paper → live 전환을 계획하고 있다. live 체결 확인 없이 전환하면 6/8 같은
"손절 결정은 맞았으나 실행이 마비"되는 상황을 실거래에서 재현할 위험이 있다.

## 환경 및 가정 (Environment & Assumptions)

- 언어/런타임: Python 3.13, PostgreSQL, KIS Open API(REST).
- 본 SPEC의 작업 범위는 **kis/ 실행 계층 한정**. config.py / backtest/ / edge/ / pyproject.toml /
  일일리포트 배선은 **수정 금지**(SPEC-044가 동시 구현 중).
- KIS 체결조회 엔드포인트(아래 ADR): `GET /uapi/domestic-stock/v1/trading/inquire-daily-ccld`.
- **가정 A-1 (확인 필요):** live TR_ID = `TTTC8001R`(3개월 이내), `CTSC9115R`(3개월 이전). paper = `VTTC8001R`.
  → 공개 소스(koreainvestment/open-trading-api `ORDERS_PAPER="VTTC8001R"`, Soju06/python-kis,
  jellybin52/samsung_auto_trader `TTTC8001R if not VIRTUAL`)로 **교차검증**했으나,
  **live `TTTC8001R` 동작은 본 프로젝트에서 한 번도 실측한 적이 없음** → 운영자 라이브 자격증명으로
  실측 확인 전까지 "확인 필요"로 표기(아래 [확인 필요] 항목).
- **가정 A-2 (확인 필요):** KIS paper(모의)에서 `inquire-daily-ccld`(VTTC8001R)는 당일 체결을
  **빈 응답**으로 반환함이 라이브 확정됨(2026-05-28, msg_cd 70070000). 본 SPEC은 이 엔드포인트를
  **live 전용**으로 사용한다. paper 체결 확인은 기존 SPEC-029 balance-reconcile 경로를 **그대로 유지**한다.
- 가정 A-3: live 주문 제출(`submit_order` live 분기)은 `live_unlocked=true`일 때만 KIS로 POST된다
  (REQ-MODE-02-6, 기존 불변). 본 SPEC은 이 게이트를 변경하지 않는다.
- 가정 A-4: position_watchdog는 `*/5`(5분) 주기로 돌고, persona orchestrator도 같은 손절을 평가한다.
  두 경로는 SPEC-042 `sell_lock.guard_sell`로 중복 제어된다(기존 불변).

## 요구사항 (Requirements — EARS)

### 모듈 A — live 체결조회 seam 구현 (live fill confirmation)

- **REQ-045-A1 (Event-Driven):** **When** `confirm_fills(client)`가 live 모드에서 호출되면,
  the system **shall** KIS `inquire-daily-ccld`(TR_ID 가정 A-1) 체결조회를 수행하여 당일 주문의
  실제 체결 상태(체결수량·체결가)를 조회하고, 기존 paper balance-reconcile 대신 **실제 broker 체결**로
  주문 상태를 확정한다.
- **REQ-045-A2 (Unwanted Behavior):** **If** live 체결조회 응답이 비어 있거나 오류(rt_cd≠0)이거나
  체결을 확정할 수 없으면, **then** the system **shall** 해당 주문을 `filled`로 **위조하지 않으며**
  (no fabricated fill), 미확인 상태를 audit하고 `order_resolver`의 윈도우 기반 `expired` 수렴에 맡긴다.
- **REQ-045-A3 (State-Driven):** **While** live 체결조회를 수행하는 동안, the system **shall**
  SPEC-043 전역 TPS 페이서(`client.get()` → `_RateGate`)를 경유하여, 통제되지 않은 추가 KIS 호출을
  발생시키지 않는다(REQ-043-B1 존중).
- **REQ-045-A4 (Ubiquitous):** the system **shall** live 체결조회 결과를 SPEC-042의 **단일 체결확인
  코드 경로**(`confirm_fills`) 안에서만 처리하여, `order_resolver`/`sell_lock`/positions 미러가
  동일한 진실원(broker 체결)을 공유하도록 한다. 병렬 체결확인 경로를 신설하지 않는다.
- **REQ-045-A5 (Unwanted Behavior):** **If** 현재 모드가 PAPER이면, **then** the system **shall**
  본 모듈을 호출하지 않고 기존 SPEC-029 balance-reconcile 경로를 그대로 사용한다
  (paper에서 `inquire-daily-ccld`는 빈 응답이므로 채택 금지, 가정 A-2).

### 모듈 B — 6/8 실패모드 재현 회귀 테스트 (reproduction-first) [HARD]

- **REQ-045-B1 (Event-Driven) [HARD — 프로젝트 규칙]:** **When** 체결 확인 단계에서 합성/조회
  실패(예: `ORDER_SYNTHETIC_ERROR` 또는 체결조회 예외)가 주입되면, the system **shall** 해당 SELL
  주문이 `submitted`에 **영구히 갇히지 않고** 유한 시간 내에 터미널 상태(`filled` 또는 사유 동반
  `expired`)로 수렴하며, 매도 의도가 **조용히 소실되지 않음**(다음 사이클에서 KIS 진실원으로 재평가)을
  보장한다. 이 재현 테스트는 어떤 수정보다 먼저 **실패를 보여야** 한다(고친 뒤 통과).
- **REQ-045-B2 (Ubiquitous):** the system **shall** 6/8 실패모드 재현 테스트를 fake clock(주입형
  시계)로 결정론적으로 구동하여, 라이브 broker나 wall-clock 없이 `order_resolver` 윈도우 만료를
  검증한다.
- **REQ-045-B3 (State-Driven):** **While** live 체결조회가 미확인을 반환하는 동안, the system **shall**
  미해소 주문을 `filled`로 위조하지 않고 `expired`로 수렴시키되, 매도 의도 재평가가 가능하도록
  positions/락 상태를 일관되게 유지한다(REQ-045-A2/A4와 정합).

### 모듈 C — 소액 라이브 스모크 게이트 (execution-only validation)

- **REQ-045-C1 (Ubiquitous):** the system **shall** 라이브 전환 검증 절차로 **최소 수량(예: 1주)·
  최소 종목**의 bounded 라이브 실행 스모크를 정의하며, 이 절차는 **실행 경로만** 검증하고
  **전략/알파 검증이 아님**을 명시한다.
- **REQ-045-C2 (Event-Driven):** **When** 소액 라이브 매수→매도 round-trip이 한 건 완료되면,
  the system **shall** 다음을 **관측 가능한 증거**로 요구한다: (1) `inquire-daily-ccld`로 **실제
  체결 확인된** live fill 1건, (2) 원장(orders 상태/positions/realized_pnl_cum)이 broker 진실원과
  정합, (3) submitted 영구 정체 0건.
- **REQ-045-C3 (Unwanted Behavior):** **If** 소액 라이브 스모크에서 어느 한 증거라도 미충족(미확인
  체결, 원장 불일치, stuck submitted)이면, **then** the system **shall** 전면 라이브 승급을
  **차단**하고 사유와 함께 운영자에게 보고한다(SPEC-042 AC-5를 보완하는 하드 게이트).
- **REQ-045-C4 (Ubiquitous):** the system **shall** 스모크 리포트에 "본 게이트는 실행 경로
  검증이며 전략 수익성 검증이 아니다(전략 측정은 SPEC-044 + 향후 hybrid 재설계 소관)"를 명시한다.

### 모듈 D — 라이브 조건 멱등성 / 이중매도 안전 (idempotency)

- **REQ-045-D1 (State-Driven):** **While** position_watchdog(`*/5`)와 Decision 사이클이 같은
  종목 매도를 동시에 평가하는 동안, the system **shall** SPEC-042 `sell_lock.guard_sell`(submitted leg +
  cooldown leg)로 라이브에서 정확히 한 건의 SELL만 발사되도록 보장한다(중복 KIS 매도 0).
- **REQ-045-D2 (Event-Driven):** **When** live 체결조회가 한 주문의 체결을 확인하면, the system
  **shall** `sell_lock`의 submitted leg가 자동 해제되어, 체결 완료 후 새로운 정당한 매도 신호가
  부당하게 영구 차단되지 않도록 한다(capital-preservation, REQ-042-C2 정합).
- **REQ-045-D3 (Unwanted Behavior):** **If** 동일 주문에 대해 체결조회가 반복 호출되면, **then**
  the system **shall** 이미 터미널/advanced 상태인 주문을 재전이하지 않는다(멱등, REQ-042-B3 정합).

## [확인 필요] 운영자 확인 게이트 (Operator-confirm gates)

정직성 규칙(거짓 금지·검증 후 단언)에 따라, 아래는 **운영자 라이브 자격증명으로 실측 확인 전까지
가정**으로 표기한다:

- **[확인 필요-1] live TR_ID 실측:** `TTTC8001R`(3개월 이내)/`CTSC9115R`(3개월 이전)가 live
  `inquire-daily-ccld`에서 당일 체결을 실제로 반환하는지. (공개 소스 교차검증 완료, 본 프로젝트
  live 실측 미완.) → run 단계에서 운영자 자격증명으로 1건 실측 확인을 **선행 조건**으로 한다.
- **[확인 필요-2] live 응답 스키마:** 체결조회 output 필드명(체결수량/체결가/주문번호 매핑)이
  본 프로젝트 `KisResponse._parse`(output/output1)와 호환되는지 실측 확인.
- **[확인 필요-3] websocket 체결통보 가용성(ADR 대안):** KIS 라이브 체결통보 websocket이 운영자
  계정에서 가용한지, 별도 승인이 필요한지. (ADR에서 polling을 1차 권고하므로 차단요소 아님.)

## 제외 사항 (Exclusions — What NOT to Build) [HARD]

- **전략/알파 검증 제외:** 본 SPEC은 **실행 경로**만 검증한다. 전략이 수익을 내는지(기대값/Sortino
  등)는 **검증하지 않으며**, 그것은 SPEC-044(측정 인프라) + 향후 hybrid 재설계 소관이다.
- **SPEC-044 소유 파일 수정 제외:** `src/trading/config.py`, `src/trading/backtest/*`,
  `src/trading/edge/*`, `pyproject.toml`, 일일리포트 배선 **수정 금지**.
- **paper 체결확인 경로 변경 제외:** 기존 SPEC-029 balance-reconcile(paper)는 **그대로 유지**.
  본 SPEC은 live 분기만 구현한다.
- **`live_unlocked` 게이트 변경 제외:** live 주문 차단 게이트(REQ-MODE-02-6)는 미변경.
- **자격증명 로테이션 제외:** KIS 키 발급/회전은 운영자 수동(Non-Goal).
- **websocket 체결통보 구현 제외(이번 SPEC):** ADR에서 polling을 권고. websocket은 후속 SPEC
  여지로만 남긴다(가용성은 [확인 필요-3]).
- **신규 마이그레이션 불요(잠정):** orders 상태 enum(`expired` 포함)은 mig 031에 이미 존재.
  본 SPEC은 새 컬럼/상태가 불필요할 것으로 보이며, run 단계에서 최종 확정한다.

## 관련 SPEC (Related)

- SPEC-TRADING-042 (broker-truth ledger): 본 SPEC이 채우는 live seam(`confirm_fills`)을 남김.
  AC-5 live-readiness 게이트를 본 SPEC의 모듈 C가 **보완**한다.
- SPEC-TRADING-029 (fill sync): paper balance-reconcile 경로. 본 SPEC은 이를 **건드리지 않는다**.
- SPEC-TRADING-043 (KIS TPS governance): 전역 페이서. 모듈 A는 이를 **반드시 존중**한다.
- SPEC-TRADING-039 (paper 합성 체결): paper-only 합성 fill. live 무관(미변경).
- SPEC-TRADING-044 (measurement infra): 전략 측정 소관. 본 SPEC과 **경계 분리**(파일 미접촉).
