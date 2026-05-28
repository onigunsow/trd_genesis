---
id: SPEC-TRADING-033
version: 0.1.0
status: draft
created: 2026-05-28
updated: 2026-05-28
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "자동 손절/익절 포지션 워치독 — ATR 동적 임계 기반 장중 자동 청산"
related_specs:
  - SPEC-TRADING-012   # DYNTH — get_dynamic_thresholds(effective_stop/effective_take/trailing) 소유 SPEC
  - SPEC-TRADING-016   # 리스크 한도/회로차단 기원 + RSI stop-take 사전 규칙 기원, 자본 보전 원칙
  - SPEC-TRADING-024   # REQ-024-7 position watchdog (Stage 2 deferred) — 본 SPEC 이 실현, watcher/scheduler home, TickerThrottle
  - SPEC-TRADING-029   # positions / KIS balance mirror — balance() 의 pnl_pct source
---

# SPEC-TRADING-033 — 자동 손절/익절 포지션 워치독

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-28 | 0.1.0 | Initial draft. SPEC-024 REQ-024-7(Stage 2 deferred position watchdog)을 실현한다. 현재 매도는 **decision LLM 이 사이클 중 sell 을 선택할 때만** 발생하여, 급락 포지션이 자동 청산되지 않는 **자본 보전 안전 공백**이 존재한다. 본 SPEC 은 장중(09-15 KST) `*/5` cron 워치독을 추가하여 ATR 동적 임계(`get_dynamic_thresholds`, SPEC-012)로 보유 종목을 평가, `pnl_pct <= effective_stop` 이면 **전량 손절**, `pnl_pct >= effective_take` 이면 **반량 익절**(per-ticker 1회/일 가드)한다. 손절·익절 청산은 **halt_state 와 일일 주문건수 한도를 우회**(매수 게이트가 리스크 축소 청산을 막아선 안 됨)하되, 실제 시장 거부(하한가 등 market_safety)는 허용/로깅. 모든 청산은 텔레그램 + audit_log. 사용자 리스크 정책 결정 반영 — 2026-05-28 | onigunsow |
| 2026-05-28 | 0.1.1 | 승인(approved) — run 진입. 열린질문 해소: Q-1 익절 가드 in-memory per-ticker 일자 마커(마이그레이션 불필요), Q-2 익절도 halt·한도 우회=YES, Q-3 트레일링 스탑 defer(향후 SPEC), Q-4 double-sell은 매도 직전 잔여 qty 재확인으로 완화. 사용자 결정 2026-05-28 | onigunsow |

---

## Scope Summary

본 SPEC 은 **하락 포지션이 자동으로 청산되지 않는 자본 보전 안전 공백**을 막는다.
오늘날 매도는 decision persona 가 사이클 중 sell signal 을 낼 때만 실행되며, 종목이 급락해도
다음 LLM 사이클 전까지 자동 손절이 일어나지 않는다. 본 SPEC 은 장 시간(09-15 KST, 평일)
`*/5` 워치독을 도입하여, 보유 종목별 실시간 평가손익률(`pnl_pct`)을 ATR 동적 임계와 비교해
자동으로 손절(전량)·익절(반량)한다. 이는 SPEC-024 REQ-024-7 의 *position watchdog* 를 실현한다.

### 문제 (검증된 안전 공백)

- 매도 실행 경로는 `src/trading/personas/orchestrator.py` 의 `_execute_signal(client, sig,
  decision_id)`(라인 746) → `kis_sell`(import 라인 34: `from trading.kis.order import sell as
  kis_sell`)뿐이며, 이 함수는 decision persona 가 만든 sell signal 로만 호출된다(orchestrator
  라인 1056/1225/1491). 즉 **자동 다운사이드 보호가 없다** — LLM 이 팔기로 결정하지 않으면 급락
  포지션이 그대로 방치된다.
- 보유·실시간 손익은 `src/trading/kis/account.py` 의 `balance(client)`(라인 10)이 반환한다.
  per-holding dict 은 `qty`(라인 46), `avg_cost`(라인 47), `current_price`(라인 48),
  `eval_amount`(라인 49), `pnl_amount`(라인 50), `pnl_pct`(라인 51, KIS 제공 평가손익률
  `evlu_pfls_rt`)를 포함한다. **`pnl_pct` 는 KIS 가 직접 계산한 평가손익률이므로 워치독은 이를
  그대로 읽고 재계산하지 않는다.**
- ATR 동적 임계는 `src/trading/strategy/volatility/thresholds.py` 의
  `get_dynamic_thresholds(ticker) -> dict`(라인 33)이 제공한다(SPEC-012, REQ-DYNTH-05-2~5 소유).
  반환 dict 은 `effective_stop`(음수 %, 예 -8.5, `= max(-2×ATR%, -15%)`, 라인 80),
  `effective_take`(양수 %, 예 +12, `= min(+3×ATR%, +30%)`, 라인 81),
  `trailing_stop_pct`(라인 90), `source`("dynamic" 또는 "fixed_fallback", 라인 93/62)를 담는다.
  ATR 미가용 시 `source="fixed_fallback"` 로 안전 폴백(라인 58~67)된다. **이것이 워치독의 임계
  source 이다.**
- SPEC-024 REQ-024-7(라인 130~132)은 정확히 이 기능을 *Stage 2 position watchdog* 으로 예약했고,
  파일명 `src/trading/watchers/position_watchdog.py` 를 예약했다(SPEC-024 라인 80, 191). 본 SPEC 이
  그 deferred 요구사항을 실현한다.

### In scope

- 신규 워치독 `*/5` (09-15 KST, mon-fri) cron `position_watchdog` — 기존 watcher 들과 동일하게
  runner.py 에 `_wrap` 경유로 등록(KRX 휴장일 자동 스킵).
- 매 폴마다 KIS `balance()` 보유 종목을 순회, 종목별 `get_dynamic_thresholds(ticker)` 로 임계 취득.
- **손절(전량):** `pnl_pct <= effective_stop` → 보유 전량(`qty`) 매도.
- **익절(반량):** `pnl_pct >= effective_take` → 절반(`max(1, qty // 2)`) 매도, **per-ticker 1회/일**
  재발 가드(전량 손절은 포지션이 사라지므로 가드 불필요).
- **halt_state 및 일일 주문건수 한도 우회:** 손절·익절 청산은 `halt_state=true` 이거나 일일 주문
  건수 cap 에 도달했어도 **반드시 실행**된다 — 매수 지향 게이트가 리스크 축소 청산을 막아선 안 됨.
- 모든 자동 청산은 `system_briefing`("자동 손절"/"자동 익절") + `audit_log`
  (`POSITION_WATCHDOG_EXIT`) 기록.
- per-ticker 오류 격리(한 종목 실패가 전체 sweep 을 중단시키지 않음), ATR 미가용 폴백, 텔레그램
  실패 swallow — 워치독은 스케줄러를 죽이지 않는다.

### Non-goals (명시적 비목표)

- **decision persona 의 매수/매도 로직, orchestrator 사이클, 기존 watcher 무변경.** 본 SPEC 은
  *additive* 레이어이며 기존 거래 흐름을 수정하지 않는다(REQ-033-7).
- **회로차단 트립 조건/리스크 한도(limits.py) 로직 변경 없음.** 워치독은 한도 게이트를 *우회*할 뿐
  그 정의를 수정하지 않는다.
- **트레일링 스탑 미구현.** `get_dynamic_thresholds` 가 `trailing_stop_pct` 를 제공하지만 본 SPEC 은
  고정 stop/take 만 사용한다(향후 SPEC — Open Question Q-3).
- **새 DB 테이블/컬럼 강제 도입 없음.** 익절 가드는 기본적으로 in-memory(프로세스 상주)로 구현.
  (DB 영속 가드는 Open Question Q-1, 채택 시 마이그레이션 번호 024.)
- **실제 시장 거부를 정책 게이트로 취급하지 않음.** KIS 가 하한가/locked 상태에서 매도를 거부하는
  것은 현실 제약이므로 허용/로깅하며, 본 SPEC 이 차단하지 않는다.

---

## Environment

- 기존 SPEC-001 ~ SPEC-032 인프라 (Docker compose, Postgres 16-alpine, Telegram trading bot).
- `src/trading/kis/account.py`:
  - `balance(client) -> dict`(라인 10): `holdings` 리스트(라인 42~55) 각 항목이 `ticker`, `name`,
    `qty`(46), `avg_cost`(47), `current_price`(48), `eval_amount`(49), `pnl_amount`(50),
    `pnl_pct`(51=`evlu_pfls_rt`, KIS 제공) 보유. `hldg_qty > 0` 인 종목만 포함(라인 54).
  - paper(VTTC8434R)/live(TTTC8434R) tr_id 자동 분기(라인 18). SPEC-029 검증: paper 는 balance
    엔드포인트가 신뢰 가능한 fill/잔고 source.
- `src/trading/strategy/volatility/thresholds.py`:
  - `get_dynamic_thresholds(ticker) -> dict`(라인 33): `effective_stop`/`effective_take`/
    `trailing_stop_pct`/`source` 반환. ATR 미가용 → `source="fixed_fallback"`(라인 58~67,
    SPEC-001 고정 규칙). 환경변수 `STOP_ATR_MULTIPLIER`(라인 24, 기본 2.0),
    `TAKE_ATR_MULTIPLIER`(라인 25, 기본 3.0), `MAX_STOP_LOSS_PCT`(라인 29, 기본 15.0),
    `MAX_TAKE_PROFIT_PCT`(라인 30, 기본 30.0)로 multiplier/guardrail 설정 가능.
  - **소유 SPEC = SPEC-TRADING-012**(REQ-DYNTH-05-1~7, 012 spec.md 라인 316~357). 011/024 에는
    정의 없음(grep 검증: REQ-DYNTH 출현 012=8, 011=0, 024=0).
- `src/trading/personas/orchestrator.py`:
  - `_execute_signal(client, sig, decision_id) -> int | None`(라인 746~771): side="hold"/qty<=0 →
    None; 그 외 `fn = kis_buy if side=="buy" else kis_sell`(라인 755) 호출. signal dict shape:
    `{"ticker","side","qty","order_type"?,"limit_price"?}`. **이 함수 자체에는 halt/한도 게이트가
    없다** — 게이트는 사이클 코드에 위치.
  - halt 게이트(`if state["halt_state"]:` 라인 891/1189/1347)와 pre-order 한도 체크
    (`from trading.risk.limits import check_pre_order` 라인 43, trip 사이트 라인 1051/1484)는
    **사이클에서 `_execute_signal` 호출 이전**에 적용된다. 워치독은 이 사이클 게이트를 통과하지
    **않는 경로**(직접 `kis_sell` 또는 `_execute_signal` 직접 호출)로 청산해야 한다.
- `src/trading/kis/order.py`:
  - `sell(client, *, ticker, qty, order_type="market", limit_price=None, persona_decision_id=None)
    -> dict`(라인 ~과 그 시그니처; 반환 dict 에 `order_id`=DB orders.id 포함, 라인 151/204).
    워치독은 `kis_sell` 을 직접 호출 가능(decision_id 불필요 — `persona_decision_id` 는 선택).
- `src/trading/watchers/price_threshold.py`:
  - `poll_price_threshold(...) -> dict`(라인 126): 보유 ∪ dynamic ∪ micro candidate 를 `*/5` 로
    폴링하며 metrics dict 반환. **본 SPEC 워치독의 템플릿.** per-source try/except 격리(라인 71~77),
    metrics dict 패턴(라인 130~136) 참조.
- `src/trading/watchers/throttle.py`:
  - `TickerThrottle(min_interval_sec=300, daily_cap=20)`(라인 31): per-ticker 쿨다운 + **전역**
    일일 cap. 주의: `daily_cap` 은 **전체 합산** cap 이지 per-ticker 가 아니다(`_daily_count` 단일
    카운터, 라인 50/69). 따라서 "익절 per-ticker 1회/일" 은 TickerThrottle 만으론 직접 표현되지
    않으며, per-ticker 일자 마커(dict[str, date])가 더 직접적(Specifications 참조).
- `src/trading/risk/limits.py`:
  - `check_pre_order(...) -> LimitCheck`(라인 83): daily_count 는 `daily_order_count_today()`
    (라인 42~52)가 `orders` 테이블 today rows(status submitted/filled/partial)를 COUNT.
    `RISK_DAILY_ORDER_COUNT_MAX`(config import 라인 16). **워치독 청산 주문도 orders 에 기록되어
    카운트에 포함되지만, check_pre_order 게이트를 거치지 않으므로 카운트에 의해 *차단*되지 않는다.**
- `src/trading/risk/market_safety.py`:
  - `check_pre_order_safety(client, *, ticker, side, qty, notional) -> SafetyResult`(라인 40):
    sell 시 `near_lower_limit`(현재가 vs 하한가 1% 이내) → blocker(라인 82~85), 비정상 stat_cls →
    매매 차단(라인 76). **이는 정책 게이트가 아니라 현실 시장 제약** — 워치독이 임의로 우회하지
    않으며, KIS 가 하한가에서 매도를 거부하면 허용/로깅한다(REQ-033-4).
- `src/trading/scheduler/runner.py`:
  - `KST = pytz.timezone("Asia/Seoul")`(라인 34), `CronTrigger`(라인 13) import.
  - **watcher 등록 블록(라인 295~323):** 3개 watcher 모두
    `sched.add_job(lambda: _wrap("watcher_xxx", _watcher_xxx.poll_xxx),
    CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5", timezone=KST),
    id="watcher_xxx", name="watcher_xxx */5 (09-15 KST)")` 패턴. 본 SPEC 신규 잡은 이 블록에 추가.
  - `_wrap(name, fn)`(라인 91~102): `is_trading_day()`(KRX 휴장일 제외) 가드 + start/ok/failed
    로깅. watcher import 패턴(라인 29~31): `from trading.watchers import xxx as _watcher_xxx`.
- `src/trading/alerts/telegram.py`: `system_briefing(category: str, message: str)`(라인 70, 위치
  인자 2개). 테스트는 mock 으로 send 횟수/카테고리 검증.
- `src/trading/db/session.py`: `audit(event_type, actor, details)`(라인 47), `connection()`(라인 32),
  `get_system_state()`(라인 61). **신규 DB 레이어 불필요.**
- 마이그레이션 디렉터리 현재 최고 번호 = `023_halt_notify_cooldown.sql`(SPEC-031). → 본 SPEC 이
  DB 영속 가드를 채택한다면 신규 번호는 **024**. (단, 기본은 in-memory 가드 — 마이그레이션 불필요.)
- `.moai/config/sections/scheduler.yaml` 존재하나 **런타임 미로드**(SPEC-031/032 검증). schedule 값은
  코드 상수가 source of truth — `*/5` (09-15 KST) 는 runner.py `CronTrigger` 리터럴로 하드코딩.

---

## Assumptions

- A-1: KIS `balance()` 의 `pnl_pct`(`evlu_pfls_rt`)는 KIS 가 계산한 평가손익률(%)이며, 본 SPEC 은
  이를 재계산 없이 직접 임계 비교에 사용한다(account.py:51 검증).
- A-2: `get_dynamic_thresholds(ticker)` 는 항상 dict 를 반환하며(예외 시에도 `source="fixed_fallback"`
  로 안전 폴백, thresholds.py:58~67), `effective_stop`(음수 %)·`effective_take`(양수 %)를 포함한다.
  구현자는 키 존재/타입을 방어적으로 확인한다.
- A-3: 워치독은 단일 스케줄러 프로세스 내에서 실행되므로 in-memory per-ticker 익절 마커가 충분하다.
  컨테이너 재시작 시 마커가 초기화되나, 같은 날 재시작 직후 동일 종목이 다시 effective_take 이상이면
  익절이 한 번 더 발생할 수 있다 — same-day 가드의 허용 가능한 한계(SPEC-024 TickerThrottle 선례).
- A-4: 손절은 전량 청산이므로 다음 폴 때 해당 종목이 `balance()` holdings 에서 사라진다(`hldg_qty>0`
  필터, account.py:54) → 손절 재발 가드 불필요.
- A-5: `qty == 1` 인 종목의 익절은 `max(1, 1 // 2) = max(1, 0) = 1` 이므로 1주 매도 = **전량 청산**
  이 된다(반량 청산 불가능한 엣지 — 의도된 동작, 문서화 대상).
- A-6: 워치독은 `kis_sell`(또는 `_execute_signal`)을 **직접** 호출하며 orchestrator 의 사이클 halt
  게이트(orchestrator:891/1189/1347)·`check_pre_order`(limits.py)를 통과하지 않는다. 따라서 청산은
  `halt_state=true`/일일 cap 도달 상태에서도 실행된다. 단 청산 주문도 `orders` 테이블에 기록되어
  `daily_order_count_today()` 카운트는 *증가*시킨다 — 차단되지 않을 뿐.
- A-7: 한 종목의 quote/threshold/order 오류는 try/except 로 격리되어 나머지 보유 종목 평가를
  중단시키지 않는다(price_threshold.py:71~77 선례).
- A-8: SPEC-024 A-6 에 따라 paper 모드에서 stop-loss/take-profit 자동 집행이 허용된다. real 모드
  전환(SPEC-017 이후)에서의 notify-only 모드는 SPEC-024 Q-6 의 범위로, 본 SPEC 은 현행(paper)
  full-auto 를 가정한다.

---

## Requirements (EARS)

### REQ-033-1 (Event-driven) — 장중 `*/5` 보유 종목 평가

**WHEN** 평일(mon-fri) 09-15 KST 에 매 5분이 도래하면, **THEN** 포지션 워치독
(`poll_position_watchdog()`)은 KIS `balance()` 의 모든 보유 종목을 순회하여 각 종목의 `pnl_pct` 를
`get_dynamic_thresholds(ticker)` 의 `effective_stop`/`effective_take` 와 비교 평가해야 한다.
- (a) cron 은 runner.py 의 `CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5",
  timezone=KST)`, 잡 id `position_watchdog`, 기존 watcher 들과 동일한 `_wrap(...)` 래퍼로 등록한다
  (런타임 source = 코드 상수; KRX 휴장일 자동 스킵).
- (b) 폴 1회는 관측용 metrics dict(예: `{"checked","stop_exits","take_exits","skipped","errors"}`)를
  반환한다(기존 watcher metrics 패턴).

### REQ-033-2 (Event-driven) — 손절: 전량 청산

**WHEN** 보유 종목의 `pnl_pct <= effective_stop` 이면, **THEN** 시스템은 해당 종목의 **보유 전량**
(`qty`)에 대해 매도(손절)를 실행하고, "자동 손절" `system_briefing` + `audit_log` 항목을 남겨야 한다.
- (a) 매도 수량은 평가 직전 `balance()` 가 보고한 그 종목의 `qty`(전량)이다.
- (b) 전량 손절 후 해당 종목은 다음 폴의 holdings 에서 제외되므로 손절 재발 가드는 두지 않는다.

### REQ-033-3 (Event-driven) — 익절: 반량 청산 + 같은 날 재발 가드

**WHEN** 보유 종목의 `pnl_pct >= effective_take` **이고** 그 종목이 **당일 아직 익절되지 않았으면**,
**THEN** 시스템은 절반(`max(1, qty // 2)`)에 대해 매도(익절)를 실행하고, "자동 익절"
`system_briefing` + `audit_log` 항목을 남기며, 그 종목을 **당일 익절됨**으로 표시해야 한다.
- (a) `qty == 1` 인 경우 `max(1, 1 // 2) = 1` 이므로 1주(=전량)가 매도된다(엣지: 익절이 전량 청산이
  됨, 의도된 동작).
- (b) 같은 종목은 **KST 당일 1회만** 익절한다 — 익절 반량 후 잔여 포지션이 매 `*/5` 틱마다 반복
  매도되지 **않아야** 한다(재발 가드: in-memory per-ticker 일자 마커 또는 동등 수단).
- (c) 동일 종목이 손절 조건과 익절 조건을 동시에 만족할 수 없다(`effective_stop < 0 < effective_take`).
  평가 순서는 손절 먼저(`<= effective_stop`), 아니면 익절(`>= effective_take`), 둘 다 아니면 skip.

### REQ-033-4 (Unwanted / State-driven) — 청산은 매수 지향 게이트를 우회

시스템은 손절(REQ-033-2)·익절(REQ-033-3) 청산을 `halt_state=true` 이거나 일일 주문건수 한도
(`RISK_DAILY_ORDER_COUNT_MAX`)에 도달한 상태에서도 **차단하지 않고 실행해야** 한다 — 리스크 축소
청산은 매수 지향 게이트(회로차단 halt 게이트 / `check_pre_order` daily-count)에 의해 막혀선 안 된다
(자본 보전 hard rule).
- (a) 워치독은 orchestrator 의 사이클 halt 게이트 / `limits.py` daily-count 검사를 **통과하지 않는**
  경로(직접 `kis_sell` 또는 `_execute_signal` 직접 호출)로 청산한다.
- (b) 단, **실제 시장 거부는 정책 게이트가 아니다.** KIS 가 하한가/locked(상하한가 도달 등) 상태에서
  매도를 거부하거나 `market_safety` 의 sell 안전 검사(near_lower_limit 등)에 걸리는 것은 현실 제약
  이므로 **허용/로깅**하며 워치독은 이를 인위적으로 우회하지 않는다(거부는 오류 격리로 흡수).

### REQ-033-5 (Ubiquitous) — 모든 자동 청산의 알림 + 감사 기록

시스템은 모든 자동 청산(손절/익절)에 대해 **텔레그램 브리핑 1회**와 **`audit_log` 항목 1건**을 남겨야
한다.
- (a) 텔레그램은 손절 시 "자동 손절", 익절 시 "자동 익절" 카테고리로 `system_briefing(category,
  message)` 를 발송하며, message 에 ticker·`pnl_pct`·임계(`effective_stop`/`effective_take`)·매도
  qty 를 포함한다.
- (b) 감사 항목은 `audit("POSITION_WATCHDOG_EXIT", actor="position_watchdog",
  details={"kind": "stop"|"take", "ticker", "pnl_pct", "threshold", "qty"})` 형태로 기록한다.

### REQ-033-6 (Ubiquitous) — 장애 격리 + ATR 폴백 + 스케줄러 무중단

시스템은 한 종목의 quote/threshold/order 오류가 다른 보유 종목 평가를 중단시키지 않도록 **per-ticker
오류를 격리**해야 하며, ATR 미가용 시 `get_dynamic_thresholds` 의 고정 폴백(`source=
"fixed_fallback"`) 임계를 사용하고, 텔레그램 실패는 swallow 하여 **워치독이 스케줄러를 죽이지 않아야**
한다.
- (a) per-ticker 오류는 로깅 후 skip(metrics `errors` 증가), sweep 은 계속된다.
- (b) ATR 미가용 종목도 폴백 임계로 정상 평가된다(별도 크래시 없음).

### REQ-033-7 (Unwanted) — 기존 거래 흐름 무회귀

시스템은 본 워치독 도입으로 인해 decision persona 기반 매수/매도, orchestrator 사이클(pre_market/
intraday 등), 기존 watcher(price_threshold/volume_anomaly/blocked_release), `_execute_signal`/
`kis_sell`/limits.py 의 **동작·반환·횟수를 변경하지 않아야** 한다. 본 SPEC 은 *additive* 레이어로,
이들을 *호출/조회*만 한다.

### REQ-033-8 (Optional) — ATR multiplier/guardrail 설정성 상속

**Where** 운영자가 손절/익절 민감도를 조정하려는 경우, 시스템은 `get_dynamic_thresholds` 가 이미
노출하는 환경변수(`STOP_ATR_MULTIPLIER`/`TAKE_ATR_MULTIPLIER`/`MAX_STOP_LOSS_PCT`/
`MAX_TAKE_PROFIT_PCT`)를 통해 임계를 조정할 수 있어야 한다.
- (a) 워치독은 임계를 자체 계산하지 않고 `get_dynamic_thresholds` 를 통해 취득하므로 기존 설정을
  자동 상속한다 — 본 SPEC 은 신규 설정 키를 추가하지 않는다.

---

## Specifications

### 권장 메커니즘 (구현 가이드 — 구현자 재량 여지 있음)

대상 REQ: REQ-033-1 ~ REQ-033-8

- **신규 모듈:** `src/trading/watchers/position_watchdog.py`(SPEC-024 예약 파일명).
  - 엔트리 함수 `poll_position_watchdog(...) -> dict`(metrics dict, 기존 watcher 패턴):
    1. KIS `balance(client)` 보유 종목 순회.
    2. 종목별 `th = get_dynamic_thresholds(ticker)`; `eff_stop = th["effective_stop"]`,
       `eff_take = th["effective_take"]`.
    3. `pnl = holding["pnl_pct"]`(KIS 제공, 재계산 금지).
    4. `if pnl <= eff_stop:` → **손절 전량**: qty = `holding["qty"]`, kind="stop".
       `elif pnl >= eff_take and not _took_profit_today(ticker):` → **익절 반량**:
       qty = `max(1, holding["qty"] // 2)`, kind="take", 청산 후 `_mark_took_profit(ticker)`.
       `else:` skip.
    5. 청산: 아래 "우회 매도 경로" 로 `kis_sell` 호출.
    6. 성공 시 `system_briefing("자동 손절"|"자동 익절", <message>)` +
       `audit("POSITION_WATCHDOG_EXIT", actor="position_watchdog",
       details={"kind","ticker","pnl_pct","threshold","qty"})`.
    7. per-ticker try/except 격리(price_threshold.py:71~77 선례); 텔레그램 실패 swallow.
  - 순수 판정 헬퍼 `classify_holding(pnl_pct, eff_stop, eff_take, took_profit_today) ->
    ("stop"|"take"|"skip", qty_fraction)` 로 분리하면 단위 테스트 용이.

- **우회 매도 경로(REQ-033-4):**
  - 권장: `kis_sell(client, ticker=ticker, qty=qty, order_type="market",
    persona_decision_id=None)` 를 **직접** 호출. orchestrator 사이클의 halt 게이트/`check_pre_order`
    를 거치지 않으므로 자연히 halt·daily-count 를 우회한다(`_execute_signal` 자체엔 게이트 없음 —
    이를 직접 호출하는 대안도 가능하나 decision_id 가 필요하므로 `kis_sell` 직접 호출이 더 단순).
  - 매도 직전 **잔여 qty 재확인**(double-sell race 완화, Q-4): 청산 직전 `balance()` 의 그 종목 qty
    가 0 이면 skip(이미 decision persona 가 같은 창에서 팔았을 수 있음). 격리된 try/except 가 KIS
    거부(잔고 부족/하한가)도 흡수한다.
  - market_safety/하한가 거부는 정상 흐름 — 거부 시 로깅 후 skip(REQ-033-4b).

- **익절 same-day 가드(REQ-033-3b):** 기본 **in-memory** per-ticker 일자 마커.
  - 권장: 모듈/인스턴스 레벨 `dict[str, date]`(ticker → KST date) + 날짜 변경 시 리셋. 또는 전용
    구조. (TickerThrottle 은 `daily_cap` 이 전역 합산이라 per-ticker 1회/일을 직접 표현 못 함 —
    per-ticker 마커가 더 정확. 단 매우 긴 per-ticker 쿨다운으로 TickerThrottle 을 재활용하는 변형도
    구현자 재량.)
  - 손절은 전량 청산으로 포지션 소멸 → 가드 불필요.

- **스케줄러 등록:** runner.py 의 watcher 블록(라인 295~323)에 추가:
  ```
  from trading.watchers import position_watchdog as _watcher_position_watchdog  # 라인 29~31 패턴
  ...
  sched.add_job(
      lambda: _wrap(
          "watcher_position_watchdog",
          _watcher_position_watchdog.poll_position_watchdog,
      ),
      CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5", timezone=KST),
      id="position_watchdog",
      name="position_watchdog */5 (09-15 KST)",
  )
  ```
  `_wrap` 으로 KRX 휴장일 자동 스킵.

- **스키마:** 본 SPEC 은 **신규 컬럼이 필요 없다**(익절 가드 in-memory). 따라서 신규 마이그레이션
  **불필요**. (DB 영속 가드(Q-1) 채택 시 마이그레이션 번호 024 가 다음 번호.)

- **설정/상수:** `*/5`·09-15 KST 는 runner.py 코드 상수. 손절/익절 임계는 `get_dynamic_thresholds`
  가 노출하는 기존 env 변수(REQ-033-8)로 조정 — 신규 설정 키 없음.

### 영향 파일 (예정)

- `src/trading/watchers/position_watchdog.py` (신규 — 판정 헬퍼 + `poll_position_watchdog`)
- `src/trading/scheduler/runner.py` (신규 잡 등록 + import)
- `src/trading/kis/account.py`, `src/trading/strategy/volatility/thresholds.py`,
  `src/trading/kis/order.py`, `src/trading/personas/orchestrator.py`, `src/trading/risk/limits.py`,
  `src/trading/risk/market_safety.py` (변경 불필요 — 호출/조회/참조만)
- 테스트: `tests/watchers/test_position_watchdog.py`(권장)

---

## Open Questions (Flag Only)

- **Q-1 (in-memory vs DB-persisted 익절 가드)**: 기본 = in-memory per-ticker 일자 마커(YAGNI,
  SPEC-024 TickerThrottle 선례 — 단일 스케줄러 프로세스에 충분, 컨테이너 재시작 시 초기화되나
  same-day 가드의 허용 한계). DB 영속(`system_state` 또는 신규 테이블, 재시작 견고)을 선호하면
  마이그레이션 번호 **024**(현재 최고 023). **기본 in-memory 권장 — flag only.**
- **Q-2 (익절도 halt/한도 우회 여부)**: **RESOLVED = YES.** 사용자 결정 #4 가 "stop-loss AND
  take-profit 청산 모두 halt_state·일일 cap 우회" 로 명시했고, 익절 역시 리스크 축소(노출 감소·이익
  확정) 청산이므로 손절과 동일하게 매수 지향 게이트를 우회한다. (사용자의 *명시적* 우회 결정이
  손절을 중심으로 서술되었으나, 결정 #4 본문이 take-profit 을 함께 포함하므로 본 SPEC 은 둘 다
  우회로 확정. — flag only, REQ-033-4 에 반영됨.)
- **Q-3 (트레일링 스탑)**: `get_dynamic_thresholds` 가 `trailing_stop_pct`(thresholds.py:90)를
  제공하나 본 SPEC 은 고정 stop/take 만 사용. 트레일링 스탑(최고가 추적형)은 별도 상태(종목별 최고가
  기록)가 필요하므로 **향후 SPEC 으로 defer 권장. — flag only.**
- **Q-4 (manual decision persona 와의 double-sell race)**: 같은 `*/5` 창에서 decision persona 가
  동일 종목을 매도하고 워치독도 청산을 시도하면 이중 매도가 가능하다. **완화책:** 청산 직전 그 종목의
  `balance()` qty 를 재확인하고 0(또는 의도 수량 부족)이면 skip; KIS 잔고 부족 거부도 per-ticker
  오류 격리(REQ-033-6)로 흡수. **구현자 선택 — Specifications 참조.**

---

## Acceptance & Traceability

자세한 acceptance criteria 는 `acceptance.md` 참조.
구현 계획 및 milestone 은 `plan.md` 참조.

| REQ | 구현 대상(예정) | 검증(acceptance.md) |
| --- | --- | --- |
| REQ-033-1 | runner.py `*/5` 09-15 KST 잡 + balance 순회 평가 | AC-10, AC-1~AC-9 전제 |
| REQ-033-2 | `pnl_pct <= effective_stop` → 전량 손절 | AC-1 |
| REQ-033-3 | `pnl_pct >= effective_take` → 반량 익절 + 당일 가드 + qty==1 엣지 | AC-2, AC-3, AC-8 |
| REQ-033-4 | halt/daily-count 우회 청산 + 실거부 허용 | AC-4, AC-5 |
| REQ-033-5 | system_briefing("자동 손절"/"자동 익절") + audit(POSITION_WATCHDOG_EXIT) | AC-1, AC-2 |
| REQ-033-6 | per-ticker 격리 + ATR 폴백 + 무중단 | AC-7, AC-9 |
| REQ-033-7 | 기존 거래 흐름/watcher 무회귀(정적+동적) | AC-11 |
| REQ-033-8 | get_dynamic_thresholds 설정 상속(임계 source 단일화) | AC-7(폴백), 정적 검증 |
| (경계) | thresholds 내·외 → 무동작 | AC-6 |
