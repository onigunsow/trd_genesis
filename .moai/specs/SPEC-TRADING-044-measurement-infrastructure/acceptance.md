# SPEC-TRADING-044 — Acceptance Criteria

모든 시나리오는 주입된 OHLCV / 라운드트립으로 단위 테스트 가능해야 한다(라이브 pykrx·DB 없음).

## AC-1 — Walk-forward point-in-time / no-look-ahead invariant (REQ-044-A1, A2, A7)

- **Given** 종료일 `T` 의 train 윈도와, `T` 이후에 룰 선택을 바꿀 만한 미래 바를 포함한 픽스처가 주어지고
- **When** walk-forward 하니스가 그 윈도에서 파라미터를 적합하면
- **Then** `ts <= T` 인 바만 적합에 사용되어야 하고, 미래 바를 주입한 변형은 **단언에 실패**해야 한다
  (look-ahead 누출이 테스트로 잡힌다).
- **And** 동일 입력에 대해 반복 실행 시 선택된 파라미터와 윈도 슬라이스가 결정적으로 동일해야 한다.

## AC-2 — Rolling train/test split, OOS-only headline (REQ-044-A3, A4, A5, A6)

- **Given** 다수 윈도를 만들 수 있는 길이의 주입 OHLCV 가 주어지고
- **When** 하니스가 롤링 train/test 로 실행되면
- **Then** 각 train 윈도에서 적합한 파라미터가 **직후의 unseen test 윈도**에서 평가되고,
  헤드라인 출력은 OOS(test) 집계 메트릭이어야 한다.
- **And** in-sample 메트릭은 헤드라인이 아닌 "in-sample 진단"으로 명시 라벨되어야 한다.
- **And** 출구 시뮬레이션은 `exit_sweep.simulate_position` 의미론(stop 우선, intraday low/high)을
  재사용해야 한다(재구현 아님).

## AC-3 — KOSPI buy-and-hold cumulative excess in daily report (REQ-044-B1, B2, B3, B4)

- **Given** 전략 라운드트립/스냅샷과 동일 기간 KOSPI 종가가 주어지고
- **When** 일일 리포트가 생성되면
- **Then** "전략 vs KOSPI 매수후보유 누적 초과수익" 이 표시되고 비교 기준(money-weighted vs time-weighted)이
  라벨되어야 한다.
- **And (graceful)** KOSPI 종가가 없으면 `available=False` 로 "알파 미확인" 을 표시하고 가짜 비교를 만들지 않아야 한다.
- **And** KOSPI 로딩은 `edge/benchmark.py` 의 `kospi_closes` / `cached_ohlcv` 를 재사용해야 한다(병렬 경로 없음).

## AC-4 — Cost-adjusted expectancy scorecard (REQ-044-C1, C2, C6)

- **Given** 청산된 라운드트립 시퀀스가 주어지고
- **When** 스코어카드가 계산되면
- **Then** net expectancy = (win% × avg_win) − (loss% × avg_loss) − round_trip_cost 가 출력되고,
  round_trip_cost 는 설정 단일소스(AC-5)에서 읽혀야 한다(하드코딩 리터럴 아님).
- **And** Sortino, cost-adjusted win rate 가 추가 출력되고 기존 profit factor / expectancy / 슬리피지 보정이 유지되어야 한다.
- **And** 표본 부족 시 어떤 좋은 헤드라인 수치라도 GO 가 될 수 없고, 한계 푸터는 항상 출력되어야 한다.

## AC-5 — Tax single source of truth + 2026 correction (REQ-044-C3, C4, C5)

- **Given** `config.py` 의 fee/tax/slippage 상수가 주어지고
- **When** round-trip cost 가 파생되면
- **Then** KOSPI 매도측이 2026 구조(거래세 ≈0.05% + 농특세 0.15% ≈ 0.20% 합계)를 반영해야 한다
  (기존 0.18% 거래세 가정 보정; Q-C1 확정 후 플립).
- **And** fee/tax/slippage 가 단일 명명 상수에서 1회 파생되어, 세율 변경이 한 줄 수정으로 끝나야 한다.
- **And** 모든 소비자(analytics / exit_sweep / scorecard / walk-forward)가 동일 값을 읽어야 한다.
- **And** `decision.jinja` 의 비용 코멘트가 단일 원천을 참조/파생해야 한다(분산 매직넘버 제거).

## AC-6 — vectorbt boundary (REQ-044-A6, ADR-001)

- **Given** 런타임 트레이딩 모듈 집합이 주어지고
- **When** import 경계 테스트가 실행되면
- **Then** 어떤 런타임 모듈도 vectorbt 를 import 하지 않아야 한다(vectorbt 는 offline `backtest` extra 전용).
- **And** `backtest/engine.py` 의 가중치 백테스트 경로는 변경되지 않아야 한다.

## Edge cases

- 라운드트립 0건 → 스코어카드는 "청산 없음" 처리, net expectancy 미산출, 푸터 출력.
- OOS 윈도 0개(데이터 짧음) → 하니스는 명시적 "윈도 부족" 을 보고하고 빈 결과를 GO 로 위장하지 않음.
- 손실 거래 0건 → Sortino / profit factor 의 분모 0 처리(inf 또는 정의된 대체값, 기존 관례 따름).
- KOSPI 부분 결측(2 미만 종가) → `available=False`.

## Definition of Done

- [ ] AC-1~AC-6 전부 통과(주입 데이터, 네트워크/DB 없음).
- [ ] look-ahead 부재가 실패하는 테스트로 증명됨(테스트된 불변식).
- [ ] 신규 코드 85%+ 커버리지, ruff/black clean.
- [ ] `@MX:ANCHOR`(point-in-time slice, 비용 단일소스) + `@MX:WARN`(vectorbt 경계) 부착.
- [ ] 전체 테스트 스위트 회귀 0(기존 통과 수 유지).
- [ ] redeploy 헬스체크 + 라이브 스모크 PASS, 다음 일일리포트에 KOSPI 누적 초과수익 라인 관측.
- [ ] 운영자 Q-A1/Q-A2/Q-B1/Q-C1/Q-C2 확인 반영.
