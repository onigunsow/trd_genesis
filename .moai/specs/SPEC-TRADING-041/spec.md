---
id: SPEC-TRADING-041
version: 0.1.0
status: draft
created: 2026-06-04
updated: 2026-06-04
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "텔레그램 트레이딩 UX — 모든 알림 종목명 표기 + 매도 체결 실현손익 부호 + /holdings 명령 + /pnl 수수료 차감"
related_specs:
  - SPEC-TRADING-039   # 페이퍼 합성 체결 — 매도 체결이 본 SPEC의 매도 P&L 알림을 트리거(side=sell)
  - SPEC-TRADING-040   # 출구 정책(트림·익절) — 트림/한도차단 알림이 본 SPEC의 종목명 표기 대상
  - SPEC-TRADING-029   # KIS inquire-balance reconcile — holdings[].avg_cost(pchs_avg_pric) 원천
  - SPEC-TRADING-030   # 일일 리포트 holdings 렌더 — /holdings 의 데이터 원천·클라이언트 배선 패턴 재사용
  - SPEC-TRADING-033   # position_watchdog — 트림/손절 매도 경로(매도 P&L 알림 적용 대상)
---

# SPEC-TRADING-041 — 텔레그램 트레이딩 UX 개선

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-06-04 | 0.1.0 | Initial draft. 운영자(텔레그램 단독 제어)가 보고한 3대 라이브 UX 결함을 표시 계층(presentation-layer)에서 해소. **핵심 전제: 필요한 데이터·함수가 전부 이미 존재** — 거의 전적으로 표시 계층 변경, DB 마이그레이션 불필요 예상. 탐색 결과(2026-06-04, 라이브 코드 검증): (1) "한도 위반 차단"/"단기과열 비중 축소" 두 알림은 `orchestrator.py` 4곳(pre_market trim ~L1163, breach ~L1188, intraday trim ~L1619, breach ~L1642)에서 `tg.system_briefing()`로 종목코드만 표기 — 반면 `tg.trade_briefing()`은 이미 `name=ticker_name(...)` 표기. `ticker_name`은 orchestrator.py:28에 import 완료. (2) `trade_briefing()`(telegram.py L140-165)은 수량/금액만 표기, 매도 실현손익 미표기. 매도 체결 직전 `bal_after = balance(client)`가 이미 호출되어 `holdings[].avg_cost`(KIS `pchs_avg_pric`, account.py L47)가 **이미 스코프 내**. (3) 운영자가 `/holdings` 명령 부재로 보유 현황을 즉시 못 봄. `/pnl`(`_pnl_summary` emergency.py L276-293)은 당일 gross(매도금액−매수금액)만 계산하고 **수수료 무시**. **개방 질문(run 단계 해소):** telegram_bot.py L75는 `emergency.handle()`만 호출하고 KIS 클라이언트를 만들지 않음 — `/holdings`는 KisClient 필요. 단, daily_report.py `_collect_portfolio()`(L146-171)의 `KisClient(get_settings().trading_mode)`→`balance(client)`→try/except 안전 placeholder 패턴을 그대로 재사용 가능함을 확인. 사용자 결정: 표시 계층만 변경, 기존 알림/명령 포맷 보존(가산적 변경만), 4모듈 EARS(종목명·매도P&L·/holdings·/pnl) + 1 Unwanted(graceful fallback). paper-first, SPEC-039 합성 체결/KIS paper inquire-balance(VTTC8434R) 불파괴. 마이그레이션 불필요(확정은 run). 신규 순수 함수(메시지 조립·P&L 계산)는 TDD-leaning. — 2026-06-04 | onigunsow |

---

## 개요 (Environment & Assumptions)

### Environment
- 페이퍼(모의) 자동매매 운영 중, 운영자는 텔레그램(@sehoon_trd_bot)으로 단독 제어.
- 알림 경로 둘: `tg.system_briefing()`(시스템 이벤트, 종목코드만), `tg.trade_briefing()`(매매 체결, 종목명 표기).
- 명령 디스패처: `src/trading/risk/emergency.py` `handle()` (telegram_bot.py L75에서 호출).
- SPEC-039 로 매도 합성 체결·`daily_pnl_pct` 실현손익 교정 완료. SPEC-040 으로 트림/한도차단 알림 발생.

### Assumptions
- `ticker_name()`(trading.data.ticker_names)은 orchestrator.py:28 에 이미 import 되어 있다.
- `balance()`(account.py)의 `holdings[]`는 `ticker/name/qty/avg_cost/current_price/pnl_amount/pnl_pct`를 이미 제공한다.
- 매도 체결 직전 `bal_after = balance(client)`로 holdings/avg_cost 가 이미 스코프에 있다(여분 API 호출 불요 가능성 높음).
- `/holdings` 의 KIS 클라이언트 배선은 daily_report.py `_collect_portfolio()` 패턴을 재사용할 수 있다.
- 본 변경은 DB 스키마를 건드리지 않는다(표시 계층). 마이그레이션 불필요(run 단계 확정).

---

## 요구사항 (EARS Requirements) — 4기능 → 5모듈

### 모듈 1 (REQ-041-1) — 모든 알림에 종목명 표기 [Ubiquitous]

- **REQ-041-1a (Ubiquitous):**
  시스템은 "한도 위반 차단"(limit-breach)과 "단기과열 비중 축소"(overheat trim) 텔레그램 알림에
  **항상** 종목코드와 함께 한국 종목명을 표기해야 한다(예: `"064350 현대로템"`),
  매매 체결 알림(`trade_briefing`)과 일관되게.
  (4 발생 지점: pre_market trim/breach, intraday trim/breach — 모두 `tg.system_briefing()`.)

- **REQ-041-1b (Ubiquitous, 순수성 보존):**
  종목명 해석은 **표시 계층(orchestrator)**에서 수행하고, 리스크 로직(`limits.py`의 breach 문자열 생성)은
  순수하게 유지하는 것을 권장한다. limits.py 의 breach 텍스트(repeat_buy/avg_down, ~L180-190)도
  종목코드를 포함하므로, 표시 계층에서 메시지 조립 시 종목명을 부가한다.

> 분리 원칙: 종목명 부가는 메시지 조립 시점(orchestrator)에서. 리스크 로직은 코드만 다룬다.

### 모듈 2 (REQ-041-2) — 매도 체결 알림에 실현손익(부호 포함) [Event-driven]

- **REQ-041-2a (Event-driven):**
  WHEN 매도 주문이 체결(side=sell)되면
  THEN 시스템은 **평균단가 기준**(average-cost basis) 실현손익을 부호·퍼센트와 함께
  매매 알림(`trade_briefing`)에 표기해야 한다.
  계산식: `(fill_price − avg_purchase_price) × qty − fee`,
  표기 예: `"실현손익 +12,340원 (+3.2%)"`.

- **REQ-041-2b (Ubiquitous, 데이터 재사용):**
  평균단가(`avg_purchase_price`)는 KIS balance 필드 `pchs_avg_pric`(account.py `balance()`의
  `holdings[].avg_cost`)에서 가져온다. 매도 체결 직전 이미 호출된 `balance()` 결과를
  **재사용**(여분 API 지연 0)하는 것을 우선한다. run 단계는 매도 발생 지점에서 holdings/avg_cost 가
  이미 가용한지 확인하고, 아니면 최소 fetch 한다.

- **REQ-041-2c (Unwanted):**
  매수(side=buy) 알림은 변경하지 않아야 한다. 매도 P&L 표기는 side=sell 에만 적용한다.

### 모듈 3 (REQ-041-3) — /holdings 명령 [Event-driven]

- **REQ-041-3a (Event-driven, 신규 명령):**
  WHEN 운영자가 텔레그램에서 `/holdings` 를 입력하면
  THEN 시스템은 `emergency.py handle()` 디스패처에서 이를 처리해, 각 보유 종목의
  종목명, 수량, 평균단가, 현재가, 평가손익(원, 부호 포함), 손익률(%)을 표기하고,
  **총 평가손익(TOTAL)** 요약 라인을 포함해야 한다.
  (데이터 원천: account.py `balance()` — 16:00 일일 리포트 holdings 와 동일, daily_report.py L147-169.)

- **REQ-041-3b (Event-driven, /pnl 개선):**
  WHEN 운영자가 `/pnl` 을 입력하면
  THEN 시스템은 기존 gross(매도금액−매수금액, CURRENT_DATE) 계산에서 `orders.fee` 를 차감해
  **실현 NET**(수수료 차감)을 보고하고, 정밀 per-lot 실현손익으로 오인되지 않도록 라벨을
  명확히(예: "(추정)" 유지 + 당일 gross 현금흐름 net-of-fee 임을 명시)해야 한다.
  (단일 SQL, orders 테이블 경량 유지. FIFO 전환은 범위 외.)

- **REQ-041-3c (Ubiquitous, 명령 목록 등록):**
  시스템은 `/holdings` 를 `/help`(및 `/start`) 명령 목록(emergency.py `_help()`, ~L254-273)에 등록해야 한다.

### 모듈 4 (REQ-041-4) — 우아한 실패(graceful fallback) [Unwanted, 횡단]

- **REQ-041-4a (Unwanted, 종목명 None):**
  IF `ticker_name()` 이 None 을 반환하면
  THEN 시스템은 종목코드만으로 우아하게 fallback 해야 하며(빈 이름·크래시 금지).

- **REQ-041-4b (Unwanted, 평균단가 부재):**
  IF 해당 종목의 평균단가(avg_cost)가 가용하지 않으면
  THEN 시스템은 매도 P&L 라인을 **우아하게 생략**해야 하며, 잘못된/0 값을 진짜처럼 표기해서는 안 된다.

- **REQ-041-4c (Unwanted, KIS 클라이언트/API 실패):**
  IF `/holdings` 처리 중 KIS 클라이언트 구성/조회가 실패하거나(타임아웃 포함) 보유가 비어 있으면
  THEN 시스템은 안전한 메시지로 degrade 해야 하며(사이클·봇 크래시 금지),
  daily_report.py `_collect_portfolio()` 의 try/except 안전 placeholder 패턴을 따른다.

---

## 사양 (Specifications)

- 종목명 표기: orchestrator.py 4곳(`tg.system_briefing()`)에서 메시지 조립 전 `ticker_name(ticker)` 해석.
  None → 코드만. 매매 알림 포맷(`"코드 이름"`)과 일관.
- 매도 실현손익: `(fill_price − avg_cost) × qty − fee`, 부호+퍼센트. side=sell 한정. avg_cost 부재 → 생략.
  `balance()` 결과 재사용 우선(run 단계 가용성 확인).
- /holdings: KisClient(get_settings().trading_mode)→balance()→holdings 렌더 + TOTAL 평가손익.
  실패/빈 보유 → 안전 placeholder.
- /pnl: 기존 SQL 에 `orders.fee` 차감 추가, 라벨 명확화("(추정)" + net-of-fee gross 명시).
- 마이그레이션: **불필요** 예상(표시 계층, 신규 컬럼 없음). run 단계 명시적 확인·기록.

## Traceability

| REQ | 기능 | 대상 파일 | 검증(acceptance) |
|---|---|---|---|
| REQ-041-1a/1b | 종목명 표기 | orchestrator.py(L1163/1188/1619/1642), limits.py(L180-190), ticker_names | AC-1 |
| REQ-041-2a~2c | 매도 실현손익 | alerts/telegram.py `trade_briefing`(L140-165), kis/account.py `balance` | AC-2 |
| REQ-041-3a | /holdings | risk/emergency.py `handle`, kis/account.py, bot/telegram_bot.py | AC-3 |
| REQ-041-3b | /pnl net-of-fee | risk/emergency.py `_pnl_summary`(L276-293) | AC-4 |
| REQ-041-3c | /help 등록 | risk/emergency.py `_help`(L254-273) | AC-3 |
| REQ-041-4a~4c | graceful fallback | 위 전 영역(None/부재/API 실패) | AC-1~4 공통 |
