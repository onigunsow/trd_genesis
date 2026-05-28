# SPEC-TRADING-033 — Implementation Plan

자동 손절/익절 포지션 워치독. SPEC-024 REQ-024-7 의 deferred position watchdog 를 실현하는 작고
additive 한 안전 레이어 — KIS balance 의 실시간 `pnl_pct` 를 ATR 동적 임계와 비교해 자동 청산하며,
기존 거래 흐름·watcher·리스크 로직은 무회귀.

## Technical Approach

신규 모듈 `src/trading/watchers/position_watchdog.py`(SPEC-024 예약 파일명)가 (1) KIS
`balance()` 보유 종목을 순회하고, (2) 종목별 `get_dynamic_thresholds(ticker)`(SPEC-012)로
`effective_stop`/`effective_take` 를 취득하고, (3) KIS 제공 `pnl_pct`(재계산 금지)를 비교하여,
(4) `pnl_pct <= effective_stop` 이면 **전량 손절**, `pnl_pct >= effective_take` 이고 당일 미익절이면
**반량 익절**(per-ticker 1회/일 가드)을 `kis_sell` **직접 호출**(orchestrator 사이클 halt 게이트/
`check_pre_order` 우회)로 실행하고, (5) "자동 손절"/"자동 익절" 텔레그램 + `audit_log`
(`POSITION_WATCHDOG_EXIT`)을 남긴다. runner.py 의 watcher 블록(라인 295~323)에 `*/5` 09-15 KST
평일 cron 을 추가한다. 기존 `balance()`/`get_dynamic_thresholds`/`kis_sell` 을 호출만 하고 그
정의는 수정하지 않는다.

### 핵심 결정 (사용자 승인)

- 임계 source = **ATR 동적**(`get_dynamic_thresholds` → `effective_stop`/`effective_take`).
  ATR 미가용 폴백은 이미 그 함수 내부(`source="fixed_fallback"`).
- **손절 = 전량 청산**(`qty` 전부). 전량 청산이라 손절 재발 가드 불필요.
- **익절 = 반량 청산**(`max(1, qty // 2)`), per-ticker **1회/일** 가드. `qty==1` 이면 1주=전량.
- **손절·익절 모두 halt_state·일일 주문건수 한도 우회**(자본 보전 hard rule) — 사이클 게이트를
  거치지 않는 직접 `kis_sell` 경로. 단 실제 시장 거부(하한가/market_safety)는 허용/로깅.
- **cadence = `*/5` 09-15 KST mon-fri** cron `position_watchdog`, runner.py 코드 상수 하드코딩,
  `_wrap` 경유(KRX 휴장일 자동 스킵).
- 모든 청산은 텔레그램 + audit_log 기록.
- 익절 가드 = 기본 **in-memory** per-ticker 일자 마커(YAGNI, SPEC-024 throttle 선례).

## Milestones (우선순위 기반, 시간 추정 없음)

### Primary Goal (P-High) — 판정 헬퍼 + 워치독 엔트리 (position_watchdog.py)

- 순수 판정 헬퍼 `classify_holding(pnl_pct, eff_stop, eff_take, took_profit_today) ->
  ("stop"|"take"|"skip", qty)`:
  - `pnl_pct <= eff_stop` → ("stop", 전량 qty).
  - `elif pnl_pct >= eff_take and not took_profit_today` → ("take", `max(1, qty // 2)`).
  - else → ("skip", 0).
- 엔트리 `poll_position_watchdog()`: `balance()` 보유 순회 → 종목별 `get_dynamic_thresholds` →
  classify → stop/take 시 우회 매도 + 텔레그램 + audit. metrics dict 반환
  (`checked/stop_exits/take_exits/skipped/errors`).
- 대상 REQ: REQ-033-1, REQ-033-2, REQ-033-3, REQ-033-5 / 검증: AC-1, AC-2, AC-6, AC-8

### Secondary Goal (P-High) — 우회 매도 경로 + 익절 가드

- 청산: `kis_sell(client, ticker=, qty=, order_type="market", persona_decision_id=None)` 직접 호출
  — orchestrator 사이클의 halt 게이트/`check_pre_order` 를 거치지 않아 halt·daily-count 자연 우회.
  매도 직전 `balance()` qty 재확인(double-sell race 완화, Q-4).
- 익절 same-day 가드: in-memory `dict[str, date]`(ticker → KST date) + 날짜 변경 시 리셋. 손절은
  전량 청산이라 가드 불필요.
- 실거부 허용: KIS 하한가/locked 거부·market_safety sell 차단은 per-ticker try/except 로 흡수·로깅.
- 대상 REQ: REQ-033-3b, REQ-033-4 / 검증: AC-3, AC-4, AC-5

### Tertiary Goal (P-High) — 스케줄러 잡 등록 (runner.py)

- watcher 블록(라인 295~323)에 `sched.add_job(lambda: _wrap("watcher_position_watchdog",
  _watcher_position_watchdog.poll_position_watchdog), CronTrigger(day_of_week="mon-fri",
  hour="9-15", minute="*/5", timezone=KST), id="position_watchdog",
  name="position_watchdog */5 (09-15 KST)")` 추가.
- `from trading.watchers import position_watchdog as _watcher_position_watchdog` import(라인 29~31
  패턴). `_wrap` 으로 KRX 휴장일 자동 스킵.
- 대상 REQ: REQ-033-1 / 검증: AC-10

### Final Goal (P-High) — 장애 격리 + 무회귀 + 테스트

- per-ticker 오류 격리(price_threshold.py:71~77 선례) + ATR 폴백 + 텔레그램 swallow.
- 단위 테스트 `tests/watchers/test_position_watchdog.py`(권장): classify 분기 + 손절 전량/익절 반량/
  qty==1/당일 가드/폴백/오류 격리 + halt·daily-count 우회(직접 kis_sell 호출 검증) + 텔레그램
  카테고리·audit 검증. `kis_sell`/`system_briefing`/`balance`/`get_dynamic_thresholds` 는 mock.
- 무회귀: 신규 모듈이 balance/thresholds/order/limits 를 호출·조회만 함을 정적·동적 확인.
- 대상 REQ: REQ-033-6, REQ-033-7, REQ-033-8 / 검증: AC-7, AC-9, AC-11

### Optional Goal (P-Low) — DB 영속 가드 / 트레일링 스탑 (deferred)

- DB 영속 익절 가드(Q-1) 채택 시 마이그레이션 번호 024(현재 최고 023). 기본 in-memory 로 충분.
- 트레일링 스탑(Q-3, `trailing_stop_pct` 활용, 종목별 최고가 추적 상태 필요)은 별도 SPEC 으로 defer.

## Architecture / Design Direction

- **임계 single source of truth = `get_dynamic_thresholds`(SPEC-012).** 워치독은 임계를 자체
  계산하지 않아 ATR multiplier/guardrail env 설정(REQ-033-8)을 자동 상속하고, ATR 폴백도 무료 획득.
- **손익 single source = KIS `balance().pnl_pct`(evlu_pfls_rt).** 재계산 금지 — KIS 평가손익률을
  그대로 신뢰(account.py:51).
- **청산 경로는 사이클 게이트 바깥.** `kis_sell` 직접 호출로 halt·daily-count 를 우회하되 `kis_sell`
  내부의 정상 주문 기록(orders 테이블)·KIS 거부 처리는 그대로 재사용 → 청산 주문도 추적·체결 동기화
  (SPEC-029) 대상이 된다(차단만 안 됨, 기록은 됨).
- 판정 로직을 순수 함수(`classify_holding`)로 격리하여 테스트 용이성 확보. I/O(KIS·텔레그램·audit)는
  엔트리 함수에 모음 — price_threshold.py 의 metrics+격리 패턴 답습.
- **additive 원칙:** 기존 watcher 3개와 동일 cron 슬롯/래퍼/구조를 따르되 별개 잡으로 추가하여
  decision-persona 흐름·orchestrator 사이클과 독립.

## Risks and Mitigations

- R-1 (사이클 게이트 우회로 인한 과청산): 우회는 *청산(매도)* 에만 적용되며 매수에는 영향 없음.
  리스크 축소 방향이므로 자본 보전과 정합. 임계 안쪽이면 무동작(AC-6).
- R-2 (익절 반복 매도): in-memory per-ticker 일자 가드로 당일 1회 제한(AC-3). 손절은 전량 청산으로
  포지션 소멸 → 가드 불필요.
- R-3 (double-sell race: decision persona 와 워치독이 동시 매도): 청산 직전 balance qty 재확인 +
  per-ticker 오류 격리로 KIS 잔고부족 거부 흡수(Q-4).
- R-4 (한 종목 오류로 sweep 중단): per-ticker try/except 격리(price_threshold 선례), metrics
  errors 증가, 나머지 종목 계속(AC-9).
- R-5 (ATR 미가용): `get_dynamic_thresholds` 가 `source="fixed_fallback"` 로 안전 폴백(AC-7).
- R-6 (재시작 시 익절 가드 초기화): in-memory 마커는 컨테이너 재시작 시 리셋 — same-day 가드의
  허용 한계(A-3). DB 영속이 필요하면 Q-1(마이그레이션 024).
- R-7 (실제 시장 거부): 하한가/locked sell 거부는 정책이 아닌 현실 제약 — 허용·로깅, 워치독이
  인위적 우회하지 않음(REQ-033-4b, AC-5).
- R-8 (트립 사이트/라인 이동): 본 SPEC 은 orchestrator 트립 사이트·limits.py 를 *수정하지 않음*.
  citation 라인은 작성 시점 기준이며 구현자는 패턴으로 재확인.

## Dependencies

- 선행 SPEC 무차단(additive). SPEC-012(get_dynamic_thresholds 소유)/016(자본 보전·리스크 한도 기원)/
  024(REQ-024-7 watchdog 예약, watcher/scheduler home, TickerThrottle)/029(balance mirror·체결
  동기화) 와 호환.
- 신규 마이그레이션 없음(in-memory 익절 가드 기본). DB 영속 채택 시 번호 024.

## Out of Scope

- decision persona 매수/매도·orchestrator 사이클·기존 watcher 변경, `_execute_signal`/`kis_sell`/
  limits.py/market_safety 내부 동작 변경, 트레일링 스탑, DB 영속 익절 가드, scheduler.yaml 런타임
  로더, real 모드 notify-only 전환(SPEC-024 Q-6 범위).
