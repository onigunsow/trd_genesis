# SPEC-TRADING-040 — Research (출구 정책 개편)

> 이 문서는 구현 전 조사·근거 정리 단계 산출물입니다. 코드는 작성하지 않습니다.
> 모든 file:line 인용은 2026-06-03 기준 현재 코드에서 재검증한 값입니다.

## 1. 문제 정의 (root cause)

SPEC-039 는 출구 **메커니즘**(페이퍼 합성 체결 + `daily_pnl_pct` 실현손익 교정)을
복원했다. 그러나 출구 **정책**(언제 파는가)은 여전히 *극단치 전용*이라,
정상장에서는 포트폴리오가 누적만 되고 round-trip 이 완성되지 않는다 → 수익성 검증 불가.

### 1.1 운영 사실 (operator-verified 2026-06-03, live DB + prompt)

- `persona_decisions` 최근 7일: **hold 362 / buy 103 / sell 3**.
  결정 페르소나는 사실상 매도하지 않는다.
- 현재 보유 6종목 손익 범위 **−2.37% ~ +2.26%**, 전부 RSI < 85 → 어떤 출구 룰에도 걸리지 않음.
- 086790 은 누적 매수로 **10주**까지 증가(집중), 단조 누적.
- 086790/055550 은 6/1 손절 시도가 일일한도(daily_count)로 막힌 뒤,
  6/2 에 같은 종목을 **7회 추가 매수**(물타기 = 가치 트랩).
- 드물게 나온 매도 시그널은 전부 차단: 6/1 daily_loss 거짓 halt(SPEC-039 에서 수정),
  5/26 & 5/28 daily_count.

> 주의(정직성): 위 7일 카운트·보유 손익·매수 횟수는 운영자가 라이브 DB·프롬프트에서
> 확인한 값이다(이 세션은 DB 자격증명 미보유로 직접 재조회하지 않음). 본 SPEC 의
> 코드 인용(아래)은 현재 코드에서 직접 재검증했다.

### 1.2 코드 근거 (file:line, 재검증 완료)

#### (A) 결정 페르소나 매도 룰이 극단치 전용
`src/trading/personas/prompts/decision.jinja`
- L13: 익절 룰 = **RSI > 85 시 50% / RSI > 90 시 70%**. (정상 구간 익절 없음)
- L14: 손절 룰 = 동적 `effective_stop` (ATR, SPEC-037 −10% 플로어 적용 후) /
  `fixed_fallback` 시 −7%. (정상 구간 트림·로테이션 없음)
- L40-43: 비용 보정 — "평가익 +1.0% 이상(수수료 차감 후 +0.5% 순익) AND RSI>85".
- L16: "동시 보유 3~7종목 권장, 8종목 이상 비권장" — **권고일 뿐, 코드 강제 없음**.
- L170-174: dynamic threshold 룰도 `effective_stop`/`effective_take`(극단치) 사용.
- 결론: 정상 구간(예: +2.26% / RSI<85)에서 **매도를 유발하는 룰 자체가 없음**.

#### (B) position_watchdog 도 동일 극단 임계
`src/trading/watchers/position_watchdog.py`
- L116-139 `classify_holding`: `pnl_pct <= eff_stop` → stop(전량),
  `pnl_pct >= eff_take` 그리고 당일 미익절 → take(절반), 그 외 skip.
- `eff_stop`/`eff_take` 는 `get_dynamic_thresholds`(L198) 의 ATR 극단치.
- 현재 6종목(−2.37%~+2.26%)은 전부 skip → stop_exits 0 / take_exits 0.
- L229-235: `kis_sell` 직접 호출 → 오케스트레이터 halt 게이트·daily_count pre-check **우회**
  (위험 축소 출구는 매수 게이트에 막히면 안 된다는 capital-preservation 하드룰).

#### (C) daily_count 가 매수로 소진
`src/trading/risk/limits.py`
- L43-52 `daily_order_count_today`: 오늘 `submitted|filled|partial` 주문 수.
- L116-118 `check_pre_order`: `cnt + 1 > RISK_DAILY_ORDER_COUNT_MAX(=10)` 이면 breach.
- `src/trading/config.py:43` `RISK_DAILY_ORDER_COUNT_MAX = 10`.
- buy/sell 구분 없이 동일 카운터 → ~15분마다 매수가 한도를 소진 → 11:48 halt →
  매도가 발사되기 전에 막힘.
- SPEC-037(orchestrator L288-410)이 **count-halt 시 위험 축소 SELL bypass** 를 추가했으나,
  이는 *사후*(이미 halt 트립된 뒤 매도 통과)일 뿐 **예방적 예산 분리가 아님**.
  buy 가 카운터를 다 쓰기 전에 sell 예산을 남겨두는 메커니즘은 없다.

#### (D) per-ticker 한도는 매수 측 cap 뿐
`src/trading/risk/limits.py:125-134` `per_ticker` = 매수 시 예상 보유가
`RISK_PER_TICKER_MAX_POSITION(=0.20)` 초과면 매수 거부.
- 이는 **신규 매수 진입 cap** 이지, 이미 누적된 보유를 **줄이는(트림) 매도 측 제어가 아님**.
- 086790 집중은 이 cap 으로 막히지 않았다(개별 매수는 각각 한도 내였을 가능성).
- 트림은 **신규 sell-side 제어**가 필요(기존 자산 없음).

#### (E) 단기과열 반복 매수 억제는 약한 감점뿐
`src/trading/screener/daily_screen.py`
- L67-70: `OVERHEAT_PENALTY_NORMAL=2.5`(개별) / `OVERHEAT_PENALTY_MARKETWIDE`(장세, 더 약함).
- L365-384: 단기과열(stat_cls=55) 종목을 제외하지 않고 **score 감점**만 적용.
- `decision.jinja` L96-102: 단기과열 종목은 "비중 축소·지정가" 권고일 뿐, **반복 매수 차단 없음**.
- L30: "같은 종목 같은 방향 반복 시 1일 1회 제한(중복 hold)" — 프롬프트 권고, 코드 강제 없음.
- 결과: 086790 같은 단기과열 종목을 6/2 에 7회 물타기 가능했다.

## 2. 기존 자산 — 재사용 대상 (재발명 금지)

| 자산 | 위치 | 역할 |
|---|---|---|
| 결정 페르소나 매도 룰 | decision.jinja L13-18, L40-43, L170-174 | 정상 구간 익절/트림 룰 추가 지점 |
| position_watchdog | watchers/position_watchdog.py | */5 자동 출구, `classify_holding` 확장 후보 |
| 동적 임계 | strategy/volatility/thresholds.py (`get_dynamic_thresholds`→dict, `effective_stop`/`effective_take`) | 익절/손절 기준값 |
| late-cycle 방어 | risk/late_cycle.py + regime_branch.py | 트림과 방향 일치(시너지 설계) |
| 위험 한도 | risk/limits.py (`check_pre_order`, `RISK_DAILY_ORDER_COUNT_MAX`, `RISK_PER_TICKER_MAX_POSITION`) | daily_count 예산 분리·트림 트리거 기준 |
| count-halt SELL bypass | orchestrator.py L288-410 | 예방적 예산 분리와 합쳐질 사후 안전망 |
| 단기과열 처리 | screener/daily_screen.py L67-70, L365-384 | 반복 매수 억제 강화 지점 |
| round-trip 측정 | edge/roundtrips.py (`build_roundtrips`, FIFO `net_pnl`) | 첫 완성 round-trip 검증·daily_pnl_pct 정합 |
| edge scorecard | edge/scorecard.py (`limitations_footer`) | 정직성 톤(엔트리 엣지 백테스트 불가 명시) |
| 출구 백테스트 | backtest/exit_sweep.py + `trading exit-backtest` | 임계 보정(추측 금지)의 단일 진실원 |

## 3. 백테스트 근거 (임계는 추측하지 말 것)

`src/trading/backtest/exit_sweep.py`:
- L8-18 SCOPE LIMIT: **출구 룰만** 검증. LLM 엔트리 엣지는 look-ahead 라 검증 불가(C-1).
- `mechanical_entries`(L91-101)는 통제 변수(N번째 봉마다 매수)일 뿐 LLM 엔트리 모델 아님.
- `recommend`(L269-322): 단일 피크가 아니라 **그리드 이웃 평균을 섞은 robust 선택**.
- SPEC-037 결과(thresholds.py L32-38 인용): "wide take-profit(3×ATR) + wide stop(−10%)
  가 기대값 최대; narrow take-profit(1.5×ATR)은 승률 높지만 **기대값 음수 = 트랩**".
- **함의**: 어떤 '적정 익절'도 기대값을 낮추지 않음을 백테스트로 검증해야 한다.
  실제 임계값은 **run 단계**에서 이 백테스트로 결정(open question), SPEC 에서 추측 하드코딩 금지.

## 4. 핵심 설계 원칙 (반드시 인코딩)

1. **백테스트로 임계 보정 — 추측 금지.** '적정 익절'은 기대값 비감소를 백테스트로 입증.
2. **TRIM 과 PROFIT/LOSS 출구 분리.**
   - 트림(집중 상한·정체 로테이션) = 리스크/리밸런싱 동기 → 기대값 중립이라도 정당
     (집중 리스크를 줄이므로). **백테스트 기대값 제약을 적용하지 않음.**
   - 익절(profit-taking) = 기대값 동기 → **백테스트 기대값 제약을 반드시 적용.**
3. **정직성.** 백테스트는 출구 룰만 검증. LLM 엔트리 엣지는 look-ahead 라 검증 불가
   (edge/scorecard.py 톤과 일치). SPEC·결과에 명시.
4. **late-cycle 방어와 정합.** moderate 활성 중(margin 35.7조, 현금 바닥 30%, 신규 진입 제한).
   트림은 후기 사이클 방어와 방향 일치 → 충돌 아닌 시너지(예: 후기 사이클 시 트림 강화).

## 5. 제약 (mandatory constraints)

- Paper-first; live 경로 영향 최소화, `live_unlocked` 불변. money/risk 로직은
  run 단계에서 **reproduction-first TDD**(plan.md 명시).
- 기존 자산 재사용, 재발명 금지(2절 표).
- EARS 요구 모듈 ≤ 5 (4 방향 매핑, 한 모듈이 룰+가드를 묶을 수 있음).
- 마이그레이션 필요 시 **030** 예약(현재 최신 029 확인; 027 결번 존재, 028/029 사용 중).

## 6. 마이그레이션 판단

후보(run 단계 확정):
- (a) 트림 마커/집중 트림 멱등 가드 — `position_action_markers` 재사용 가능성 검토
  (이미 `action='take_profit'` 사용, `action='trim'` 추가로 컬럼 변경 없이 가능할 수 있음 → **마이그레이션 불필요 가능**).
- (b) daily_count 매도 예산 — 신규 컬럼/테이블 불필요(기존 `orders` 카운트 + side 분기로 구현 가능).
- (c) 단기과열 반복 매수 카운터 — 기존 `orders`(ticker/side/ts) 집계로 충분할 수 있음.
- 잠정 결론: **마이그레이션 불필요 가능성 높음**. 신규 컬럼이 꼭 필요하면 **030** 예약.
  (run 단계에서 `position_action_markers` 스키마 확인 후 최종 결정 — open question)

## 7. 미해결 질문 (run 단계로 이연)

- Q-1: 적정 익절 임계값(% / ATR 배수 / RSI 단계) — `exit-backtest` 로 결정, 기대값 비감소 검증.
- Q-2: 종목 집중 상한 N% — `RISK_PER_TICKER_MAX_POSITION(20%)` 와의 관계(트림은 20%보다
  낮은 트림 트리거? 예: 15% 초과 시 20%로 트림). late-cycle 시 강화 폭.
- Q-3: 정체 로테이션 정의 — 보유일수 임계 + 무수익 조건(예: N일 보유 & |손익|<X% & RSI 중립).
- Q-4: daily_count 매도 예산 메커니즘 — (a) sell 전용 예산 K건 별도 카운트 / (b) buy 를 한도−K 로 제한
  (sell 용 K건 항상 확보) / (c) sell-우선 정렬. 어느 쪽이 live 경로 영향 최소인지.
- Q-5: 트림이 watchdog(코드 강제) vs 페르소나 프롬프트(권고) 중 어디서 일어나야 하는가.
  집중 상한은 **코드 강제**(페르소나가 안 팔므로) 권장, 정적 로테이션은 프롬프트+가드 혼합.
- Q-6: 단기과열 반복 매수 차단 기준(같은 종목 당일 매수 N회 초과 차단? 단기과열(55)일 때만?).
- Q-7: 마이그레이션 030 필요 여부(6절).
