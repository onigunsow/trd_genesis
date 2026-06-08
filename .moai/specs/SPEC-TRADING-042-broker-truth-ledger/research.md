# SPEC-TRADING-042 — Research (broker-truth 단일 원장 + 인트라데이 체결 정합)

> 구현 전 조사·근거 정리 단계 산출물. 코드는 작성하지 않는다.
> 모든 file:line 인용은 2026-06-08 기준 현재 코드에서 재검증한 값이다.
> **임박한 LIVE 실거래 전환(수일 내)** 이 모든 설계 판단을 지배한다.

## 0. 한 줄 요약

SPEC-039/040/041 로 출구 "경로"·"정책"·"UX" 를 고쳤으나, 그 토대인 **포지션 원장의 진실원
(source of truth)** 이 여전히 깨져 있다. 로컬 `positions` 원장이 합성 매수로 부풀려지고
(KIS 잔고엔 없음), 매도는 실 KIS 주문으로 라우팅되어 "잔고내역이 없습니다"로 reject —
**phantom position**. 이것이 "매도가 실행 안 됨" 스레드의 진짜 잔여 뿌리다.
**라이브에서 phantom position = 실거래 손절 실패 = 치명적 자본 손실.**

## 1. 회고적 증거 (2026-06-08, bear/risk-off 폭락일, KOSPI −5.5%, 사이드카 트립)

포트폴리오 −2.9% (₩9,932,786 → ₩9,643,039). 시스템은 손절을 **올바르게 결정**했으나
**실행에 실패**했다(운영자 라이브 DB 확인). 결정 페르소나·watchdog 의 판단은 정상이었고,
실패는 전적으로 **체결/원장 계층**에서 발생했다.

| 종목 | 손익 | 손절 룰 | 실패 양상 |
|---|---|---|---|
| 000270 기아 | −10.8% | hard-stop −10% | submitted→rejected "40240000:모의투자 잔고내역이 없습니다", 0 체결 |
| 033780 KT&G | −7.3% | fixed-stop −7% | 5분간 4회 매도 결정(09:04/09:31/09:32/09:34); 09:32 합성 체결, 09:33/09:34 실주문 reject "no balance" |
| 071050 한국금융지주 | −8.1% | — | submitted 에 영구 정체, 체결/취소 둘 다 안 됨 |
| 086790/055550/064350/000270/071050 | — | — | 2026-06-01 이후 5건이 submitted 누수 |

## 2. 근본 원인 (코드 재검증 완료)

### RC-1 — 이중 원장 발산 / phantom position
`src/trading/kis/order.py`
- L79-116 `_synthetic_fill`: 합성 **매수** 시 로컬 `positions` 행을 *조작 생성*(가중평균 갱신).
  그러나 KIS 페이퍼 계좌엔 그 잔고가 없다.
- L387-394 `submit_order`: 합성 체결은 `kis_order_no is not None`(KIS 가 rt_cd=0 수락) 일 때만
  발화 → 매수는 합성, 매도는 *때때로* 실 KIS 주문 → **비대칭**. 매도가 실주문으로 가면
  "잔고내역이 없습니다" reject.
- 로컬 원장 ≠ KIS 원장. 유일 reconcile 은 SPEC-029 `reconcile_from_balance` 가 **하루 1회
  15:59** 에만 실행 → 발산이 장중 내내 지속.

`src/trading/kis/fills.py`
- L57-102 `_transition_orders_fifo`: 두 WHERE 절 모두 `side = 'buy'` 전용
  (`AND synthetic = false`). **매도는 'submitted' 를 벗어날 코드 경로가 reconcile 에도 없다**
  (SPEC-029 한계, SPEC-039 가 합성 매도로 우회 시도했으나 phantom 위에서 동작).

`src/trading/kis/account.py`
- L10-18 `balance()`: `inquire-balance`(VTTC8434R paper / TTTC8434R live). 미실현 평가손익만 제공,
  당일 매도 체결 미보고(SPEC-029 Layer 3 한계의 원천). 이것이 SPEC-039 합성 체결의 동기였다.

### RC-2 — 비결정적 매도 경로 / submitted 정체
- KIS 가 수락(rt_cd=0)했으나 합성 체결 단계가 throw 하면 주문이 `submitted` 로 남고
  **filled/cancelled 로 옮길 resolver/timeout 이 없다**. `order.py`/`fills.py` 에 cancel/resolve/
  timeout/expire 경로 부재(grep 0건). → 5건 누수.

### RC-3 — 매도 in-flight 락/쿨다운 없음
`src/trading/watchers/position_watchdog.py`
- L378-489 `poll_position_watchdog`: `*/5` 폴, L486-489 `kis_sell` 직접 호출(오케스트레이터
  halt 게이트·daily_count pre-check 우회 = capital-preservation 하드룰).
- watchdog 와 persona orchestrator 가 **둘 다** 같은 종목 손절을 발사 → 033780 5분 4회.
  같은 종목 매도가 in-flight 인 동안 재결정을 억제하는 락 없음. 낭비적 LLM + 중복 주문.

### RC-4 — 실현 P&L 미집계
`src/trading/db/migrations/026_edge_validation.sql`
- L13-19 `daily_equity_snapshot.realized_pnl_cum BIGINT`: "balance() 미제공 → 라운드트립이
  백필" 로 설계됐으나, round-trip 이 phantom 위에서 완성되지 못해 **전 행 NULL**.
  SPEC-039 가 daily_pnl_pct 실현손익 교정을 주장했으나 누적 컬럼은 비어 있다.

## 3. 기존 자산 — 재사용 대상 (재발명 금지)

| 자산 | 위치 | 역할 |
|---|---|---|
| 합성 체결 | order.py L79-222 `_synthetic_fill`/`submit_order` | paper fallback 으로 재구성 대상 |
| 잔고 조회 | account.py `balance()` (VTTC8434R/TTTC8434R) | 인트라데이 reconcile 의 진실원 |
| reconcile FIFO | fills.py `reconcile_from_balance`/`_transition_orders_fifo` | 인트라데이로 끌어올림(현재 15:59 1회) |
| over-sell clamp | order.py `_held_qty`, watchdog `_confirm_qty` | phantom 방지·매도 안전(SPEC-039/033 선례) |
| 멱등 마커 | position_action_markers (action='take_profit'/'trim') | in-flight 락/쿨다운 마커 재사용 |
| 직접 매도 | watchdog L486-489 `kis_sell` | 출구 경로(in-flight 락 삽입 지점) |
| round-trip | edge/roundtrips.py FIFO `net_pnl` | realized_pnl_cum 백필 소스 |
| equity 스냅샷 | daily_equity_snapshot (mig 026) | realized_pnl_cum 영속화 대상 |
| 합성 마커 | orders.synthetic (mig 029) | reconcile 이중계산 가드(유지) |

## 4. KIS 실거래 체결 조회 (LIVE 경로 설계 근거)

- 라이브 실 체결은 KIS **체결내역**(주식일별주문체결조회, inquire-daily-ccld 계열)에서 온다.
  SPEC-029 메모: 페이퍼는 inquire-daily-ccld 가 빈 응답 → fill tracking 은 inquire-balance 만
  신뢰 가능. **라이브는 체결조회 폴링이 가능** → paper/live 가 *동일 코드 경로* 로 체결 확인하되
  소스만 분기(live=체결조회, paper=balance reconcile + 좁은 합성 fallback).

## 5. 핵심 설계 원칙 (반드시 인코딩)

1. **broker-truth 단일 원장.** KIS 계좌가 권위 있는 포지션 소스. 로컬 `positions` 는 캐시이며
   장중 reconcile 로 KIS 에 재수렴. 어떤 매도 시도도 KIS 잔고로 확인된 보유에만 한다.
2. **인트라데이 정합.** 최소한 (a) 매 매도 결정 사이클 *직전* 과 (b) 매 주문 *직후* reconcile
   → phantom 이 매도 시도를 구동할 수 없게.
3. **paper/live 패리티.** 체결 확인은 동일 코드 경로. 합성 체결은 paper 전용 *좁은 fallback*
   (KIS 페이퍼가 당일 매도 체결을 정말로 보고 못 할 때만), 즉시 로컬 원장 갱신 후 다음
   reconcile 과 정합 유지(drift 0). 합성 제거(paper fill-simulation 으로 대체) 가능성도 검토.
4. **결정성.** submitted 는 bounded window 내에 반드시 해소(폴→filled, 또는 cancel→cancelled/
   expired). 5건 누수는 일회성 cleanup.
5. **live-readiness.** 라이브 스위치 전 PAPER 에서 통과해야 할 명시 게이트(§7 AC).
6. **reproduction-first.** RC-1~4 각각 characterization/repro 테스트 선행(프로젝트 하드룰).

## 6. 제약 (mandatory constraints)

- LIVE 임박 → paper-synthetic-centric 금지. paper/live 동일 행동.
- live 경로 byte-for-byte 안전: `live_unlocked` 게이트(order.py L32-47) 불변, 합성 fill 은 live 에
  구조적으로 불가(현 `mode != PAPER` no-op 유지).
- 기존 자산 재사용(3절). EARS 요구 모듈 = 4 (A/B/C/D).
- 마이그레이션: realized_pnl_cum 백필·in-flight 락에 신규 컬럼 필요 시 **031** 예약
  (현재 최신 029; 027 결번, 030 은 SPEC-040 예약·미사용 → **031 사용**).
- KIS rate limit 인지: 인트라데이 reconcile 빈도는 throttle/캐시로 제어(§7 ADR-1 trade-off).

## 7. 미해결 질문 (run 단계로 이연)

- Q-1: 인트라데이 reconcile 빈도/트리거 — 매 매도 사이클 직전 + 매 주문 직후가 KIS rate limit
  내에서 안전한가, 아니면 짧은 TTL 캐시(예: 30~60s)로 충분한가.
- Q-2: 합성 체결 — 완전 제거(broker-poll 만) vs 좁은 paper fallback 유지. fallback 유지 시
  "당일 매도 체결 미보고" 판정 조건을 어떻게 결정성 있게 정의하는가(drift 0 보장).
- Q-3: submitted 해소 window 크기(예: N분/N사이클) 및 cancel 경로(KIS 취소 주문 TR).
- Q-4: in-flight 락 범위 — 종목당 1매도 in-flight, 쿨다운 길이, "진짜 신규 출구 시그널" 을
  쿨다운 후 막지 않는 판정.
- Q-5: realized_pnl_cum 백필 — round-trip 청산 시 증분 update vs 일배치 재계산. 헤드라인 자산
  정합(SPEC-041 D+2 basis) 방식.
- Q-6: 마이그레이션 031 필요 여부(in-flight 락이 마커로 충분한지, realized_pnl_cum 이 기존
  컬럼으로 충분한지).
