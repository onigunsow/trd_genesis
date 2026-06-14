# SPEC-TRADING-044 — Implementation Plan

## Technical approach

측정 인프라 3축을 **기존 모듈 확장**으로 구현한다(신규 모듈은 walk-forward 1개만). 모든 룰 검증은
결정적이며 주입된 OHLCV 로 단위 테스트 가능해야 한다. look-ahead 부재는 테스트된 불변식.

핵심 재사용:
- `backtest/exit_sweep.py` — `simulate_position` / `run_exit_simulation` / `run_sweep` / `ExitParams`
  를 walk-forward 가 그대로 호출(출구 의미론 재구현 금지).
- `edge/benchmark.py` — `kospi_closes` / `Benchmark` / `cached_ohlcv` 재사용.
- `edge/analytics.py` / `edge/scorecard.py` — 지표/렌더 확장.
- `config.py` — fee/tax/slippage 단일 진실원천 통합.

## Milestones (priority-ordered, no time estimates)

### M1 (Priority High) — config.py 비용 단일 진실원천 + 2026 세율 보정
- `LIVE_FEE_SELL_KOSPI` 를 명명 컴포넌트(broker fee / 거래세 / 농특세)로 분해, sell 합계 + round-trip
  을 그 컴포넌트에서 파생. 2026 개편(매도측 ≈0.20%) 반영, 코멘트에 근거 명시.
- 분산된 매직넘버(`decision.jinja` L37-38 프롬프트 코멘트)를 단일 원천 파생/참조로 좁힘.
- Q-C1 확정 전에는 기존 0.00345 를 유지하되 보정 대상으로 마킹 — 운영자 확인 후 플립.
- `@MX:ANCHOR` 비용 단일소스.
- **이유:** M3/M4 의 net expectancy 가 정확한 round-trip cost 에 의존하므로 선행.

### M2 (Priority High) — Walk-forward / point-in-time 하니스 (`backtest/walk_forward.py`)
- 롤링 train/test 윈도 스케줄(train_len/test_len/step). 각 윈도 종료일 `T` 기준 `ts <= T` 슬라이스.
- train 윈도에서 `run_sweep` 로 파라미터 적합 → 다음 test 윈도에서 `run_exit_simulation` 평가.
- OOS 집계 메트릭이 헤드라인. in-sample 은 라벨된 보조 진단.
- look-ahead 불변식 테스트(미래 누출 픽스처 → 단언 실패) + `@MX:ANCHOR` slice.
- 결정성: 주입 OHLCV, 라이브 pykrx 페치 없음.

### M3 (Priority High) — Cost-adjusted scorecard 확장 (`edge/analytics.py` + `scorecard.py`)
- net expectancy = (win% × avg_win) − (loss% × avg_loss) − round_trip_cost (M1 의 비용 사용).
- Sortino, cost-adjusted win rate 추가. 기존 profit factor / expectancy / 슬리피지 보정 유지.
- `render()` 에 신규 라인 추가, GO/NO-GO + 한계 푸터 의미론 보존.

### M4 (Priority Medium) — KOSPI buy-and-hold 누적 초과수익 (`edge/benchmark.py` + 일일리포트)
- 누적 초과수익 surface 추가(money-weighted vs time-weighted 라벨링). graceful `available=False`.
- 일일 리포트 빌더에 "전략 vs KOSPI 매수후보유 누적 초과수익" 라인 와이어링(Q-B1).

### M5 (Priority Medium) — vectorbt optional extra + 경계 가드
- pyproject `[project.optional-dependencies] backtest` 에 vectorbt 추가(런타임 미설치).
- walk-forward 의 그리드/윈도 벡터화에만 사용. 런타임 모듈이 vectorbt 를 import 하지 않음을 테스트로 단언.
- `@MX:WARN` import 경계.

## Technical risks

- **메타 오버피팅(ADR-002):** walk-forward 설정 자체를 룰에 유리하게 튜닝할 위험. 완화: OOS 가 헤드라인,
  robustness(grid-neighbour blend, 기존 `recommend()`) 유지, 모든 출력에 "룰만 검증·LLM 미검증" 캐비엇.
- **pykrx 히스토리 길이:** OOS 윈도 수가 데이터 길이에 제약. 완화: 윈도 스케줄 파라미터화(Q-A1).
- **세율 플립 부작용:** KOSPI round-trip 비용이 낮아지면 SPEC-040 익절 floor / GO-NO-GO 게이트 재튜닝 필요.
  완화: Q-C1 운영자 확인 게이트, M1 에서 보정 대상 마킹 후 플립.
- **vectorbt 무게:** 런타임 누출 시 numba/llvmlite 로 이미지 비대. 완화: optional extra + import 경계 테스트.

## Dependencies / sequencing

M1 → (M2, M3 병렬 가능, 둘 다 M1 비용 의존) → M4 → M5. M5 는 M2 완성 후 성능 최적화로 후행 가능.
