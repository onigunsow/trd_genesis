# 수용 기준 — SPEC-TRADING-058 저변동성 팩터 전략 (Low-Volatility)

## Given-When-Then 시나리오

각 시나리오에 EARS 패턴 라벨을 병기한다. 모든 기준은 이진 검증 가능(binary-verifiable)하다.

> 범위(v0.2.0): 저변동성/저베타 단일 팩터만 활성. 퀄리티·결합은 SPEC-059 연기(spec.md DEF-1/2/3).

### AC-M1-1: 저변동성/저베타 팩터 결정성 (REQ-058-M1-1, M1-2) — EARS: Ubiquitous

- **Given** 고정된 `(symbol set, as-of date T)`와 주입 OHLCV 픽스처, KOSPI 대형주 유니버스(market_cap>1조 / 거래대금>100억)
- **When** 저변동성 팩터 함수를 두 번 실행
- **Then** 두 랭킹(최저 변동성/베타 분위 long)이 동일하고, lookback이 120 거래일 고정(단일 config 소스)이며, KOSPI 대형주만 포함되고, 라이브 pykrx/DB I/O가 발생하지 않는다

### AC-M1-2: no-look-ahead + 데이터 부족 제외 (REQ-058-M1-3, M1-4) — EARS: State-Driven + Event-Driven

- **Given** 리밸런스일 T와, T 이후 bar 및 일부 종목의 < 120 trailing bars를 포함한 픽스처
- **When** 팩터 함수가 T 시점 랭킹을 산출
- **Then** trailing window는 T까지만 사용하고 T 이후 bar는 개입하지 않으며, 120 trailing bars 미만 종목은 랭킹에서 명시적으로 제외되고 날조 값이 채워지지 않는다

### AC-M2-1: 1/N 등가중 + 월간 리밸런스 (REQ-058-M2-1) [HARD] — EARS: Ubiquitous

- **Given** 저변동성 최저 분위 선택 집합(~10-20종목)
- **When** 포트폴리오를 구성
- **Then** 비중이 1/N 등가중이고 리밸런스 주기가 월간이며, per-name 최적화나 월간보다 잦은 리밸런스가 없다

### AC-M2-2: 회전 예산 < 50%/월 측정·플래그 (REQ-058-M2-2) [HARD] — EARS: Ubiquitous + Unwanted

- **Given** 월간 리밸런스 시퀀스
- **When** 백테스트 실행
- **Then** 측정 월간 회전율이 리포트되고, 50%/월을 초과하면 저회전 생존 제약 위반으로 플래그된다

### AC-M2-3: engine.run 비용 인지 백테스트 + time-weighted 알파 (REQ-058-M2-3, M2-4) — EARS: Ubiquitous

- **Given** 저변동성 포트폴리오의 prices + 월간 1/N weights
- **When** 백테스트 실행
- **Then** `engine.run`을 통해 time-weighted equity curve가 산출되고 `DEFAULT_FEE_RATE`+`DEFAULT_TAX_RATE`+`DEFAULT_SLIPPAGE`가 적용되며, `run_walk_forward`나 신규 비용 모델이 사용되지 않고, net OOS 알파가 time-weighted equity-curve 수익률 vs KOSPI로 정의되며, benchmark.py money-weighted 알파(:120-131)가 보고·게이트 어디에서도 사용되지 않는다

### AC-M2-4: time-weighted → scorecard 어댑터, money-weighted 차단 (REQ-058-M2-4a) [HARD] — EARS: Ubiquitous

- **Given** engine.run의 `BacktestResult`(time-weighted `equity_curve`/`daily_returns`)와 KOSPI 수익률
- **When** 어댑터가 scorecard 입력을 산출
- **Then** `Analytics`(expectancy/PF/n)와 `Benchmark`(alpha_pct)가 산출되고, `Benchmark.alpha_pct`가 time-weighted(equity-curve 수익률 − KOSPI)이며 benchmark.py money-weighted 집계로 채워지지 않고, `scorecard.decide`가 오직 이 어댑터 출력으로만 호출된다(GO 게이트에 money-weighted 알파 유입 0)

### AC-M2-5: 생존편향 게이트 상속 fail-CLOSED (REQ-058-M2-5, M2-6) [HARD] — EARS: State-Driven

- **Given** SPEC-057 REQ-057-M1-6 precondition 게이트의 판정 결과 — (a)"불가" (b)"가능"(명시 기록) (c)**부재/null/미기록** 세 케이스
- **When** 058 저변동성 백테스트를 산출
- **Then** (a)"불가" 및 **(c)"부재/null/미기록"** 모두에서 결과가 "생존편향 상한 — 부호보고 금지, bound only"로 강제 다운그레이드되고 M3가 생존편향을 최상위 caveat로 헤드라인하며(부재가 signed alpha로 fail-open 되지 않음), (b)명시 "가능" 판정일 때만 월간 윈도우별 as-of-date 유니버스(상폐 포함)를 재구성해 포트폴리오를 구성한다(057 M1-6a surface 재사용)

### AC-M3-1: Walk-forward OOS = 반복 point-in-time engine.run (REQ-058-M3-1) [HARD] — EARS: Ubiquitous

- **Given** M1 로더가 공급한 다년치(또는 가용) 이력
- **When** 저변동성 팩터를 walk-forward로 평가
- **Then** 각 리밸런스 T에서 point-in-time engine.run이 반복 실행되어 랭킹/선택이 T 가용 데이터만 사용하고 성과가 후속 unseen 윈도우에서 측정·연결되며, 단일 full-sample engine.run이 OOS로 보고되지 않고 look-ahead가 없다

### AC-M3-2: 다중검정 보정 (REQ-058-M3-2) [HARD] — EARS: Ubiquitous

- **Given** 저변동성 단일 팩터(N=1)를 KOSPI 대비 검정
- **When** 팩터 알파를 판정
- **Then** Bonferroni 보정 유의수준(N=1이므로 α/1=α)을 통과해야만 PASS이며, 부호만 양수인 것으로는 PASS가 아니고, 메커니즘이 generic하여 SPEC-059가 N≥2로 확장 시 자동으로 강화된다(057 M2-3a 패턴)

### AC-M3-3: 50% 백테스트 할인 (REQ-058-M3-3) [HARD] — EARS: Ubiquitous

- **Given** 측정된 백테스트 알파
- **When** GO 판정을 산출
- **Then** 알파가 50% 할인되어 판정에 사용되고, 리포트가 raw 알파와 할인 알파를 모두 표시한다

### AC-M3-4: 단일 AND 판정 함수 + GO 임계 불변 + n=리밸런스 주기 수 (REQ-058-M3-4, M3-4a, M3-5) [HARD] — EARS: Ubiquitous + State-Driven

- **Given** Bonferroni 유의성·50% 할인·`scorecard.decide`(어댑터 time-weighted 입력) 세 조건과 walk-forward 리밸런스 주기 수
- **When** 최종 판정 실행
- **Then** 세 조건의 AND로만 PASS이고(어느 하나만 통과하면 non-PASS, 부호만 양수도 non-PASS), 생존편향 다운그레이드 작동 시 즉시 non-PASS로 단락하며, scorecard GO 임계(expectancy>0 AND PF>1.0 AND alpha>0 AND n>=30)가 약화되거나 병렬 관대 게이트가 추가되지 않고, **n은 리밸런스 주기 수(거래 수 아님)이며 n<30 리밸런스는 알파 부호/크기와 무관하게 INCONCLUSIVE로 라벨된다(PASS 금지)**

### AC-M3-5: 페이퍼 전용 승급 (REQ-058-M3-6) [HARD] — EARS: Event-Driven

- **Given** GO 판정을 받은 저변동성 팩터
- **When** 승급 처리
- **Then** 페이퍼 OOS 수집으로만 승급되고, `order.py`/`smoke_gate.py`/라이브 게이트/`live_unlocked`가 전혀 변경되지 않는다(diff 0)

### AC-M3-6: 정직한 판정 — "알파 없음"도 성공 (REQ-058-M3-7) [HARD] — EARS: Event-Driven

- **Given** 저변동성 팩터가 비용·생존편향 보정 후 net 양의 OOS 알파를 보이지 않는 경우
- **When** 리포트 생성
- **Then** 리포트가 이를 유효하고 성공적인 결과로 명확히 서술하며 에러/미완료/실패로 처리하지 않는다

### AC-M3-7: 비용/생존편향 정직성 플래그 (REQ-058-M3-8) [HARD] — EARS: Ubiquitous

- **Given** engine.py 비용 상수(세금 0.18% floor, 슬리피지 0.05%)와 057 생존편향 게이트 결과(불가/가능/부재)
- **When** 리포트 생성
- **Then** 리포트가 세금은 실제 0.18-0.23% 범위의 하단이고 슬리피지는 대형주 가정이며 소형/저유동성에서 실제 비용이 이를 초과해 알파를 상향 편향시킬 수 있음을 명시하고, 생존편향 게이트 실패 또는 부재 시 생존편향을 다른 어떤 컴포넌트보다 먼저 최상위 caveat로 명시한다

## 엣지 케이스

- 057 M1 미완 → 058 M2/M3 BLOCKED, 058 M1만 픽스처로 병행(ADR-058-5)
- 057 생존편향 게이트 불가 판정 OR **결과 부재/null/미기록** → 058 알파 전체 "생존편향 상한·bound only", M3 최상위 caveat(fail-closed, signed alpha fail-open 금지)
- 저변동성 리밸런스 표본 < 30 (리밸런스 주기 수 기준) → INCONCLUSIVE(부호만으로 결론 금지)
- 리밸런스 내 거래 多로 거래 수는 30 초과하나 리밸런스 주기 수 < 30 → 여전히 INCONCLUSIVE(n=리밸런스 주기 수, M-a)
- 단일 팩터(N=1)라도 Bonferroni·50%할인·scorecard GO 셋을 AND로 통과 못하면 PASS 아님
- 월간 회전율 > 50% → 저회전 생존 제약 위반 플래그
- KOSPI 지수 결측 → 알파 "insufficient data" 라벨(비용은 계속 정량)
- 어댑터가 실수로 benchmark.py money-weighted 알파를 채우려 하면 → 단위테스트가 차단(time-weighted만 허용)
- 퀄리티 관련 입력 요청 시 → 058 범위 밖, SPEC-059 연기(펀더멘털 미접촉)

## 품질 게이트 (Quality Gate)

- 신규 코드 단위테스트: 주입 픽스처 기반, 저변동성 결정성 + no-look-ahead + 1/N·월간 불변식 + 회전 측정 + 어댑터 time-weighted 알파(money-weighted 미사용) + 어댑터 n=리밸런스 주기 수 + 단일 AND 판정 + 생존편향 fail-closed 검증
- DB/SQL 경로 변경 시 실-Postgres 통합테스트(SPEC-056) 통과
- 라이브 경로 diff 0 (`order.py`/`smoke_gate.py`/라이브 게이트/`live_unlocked`)
- GO 게이트 임계 불변(scorecard.py expectancy>0/PF>1.0/alpha>0/n>=30, `_MIN_SAMPLE` 변경 없음)
- 기존 테스트 회귀 0; `engine.py`/`scorecard.py`/`validation_gate.py`/`benchmark.py`/`pykrx_adapter.py` 기존 동작 회귀 0(benchmark.py·pykrx_adapter.py는 미사용·미변경)

## Definition of Done

- [ ] M1 저변동성 순수 함수가 결정적·no-look-ahead·주입 OHLCV로 산출(120일 고정 lookback), 데이터 부족(<120 bars) 종목 명시 제외
- [ ] [HARD] M2 포트폴리오가 최저 변동성 분위 → 1/N 등가중(~10-20종목) + 월간 리밸런스로 구성, 회전 예산<50%/월 측정·플래그
- [ ] M2 백테스트가 `engine.run`(time-weighted equity curve) + 기존 비용 모델로 net OOS 알파 vs KOSPI 산출(`run_walk_forward` 미사용)
- [ ] [HARD][B3] time-weighted → scorecard 어댑터 신규 구현, `Benchmark.alpha_pct`가 time-weighted 전용(benchmark.py money-weighted 주입 0), scorecard는 어댑터로만 공급
- [ ] [HARD] 생존편향 게이트 상속 fail-CLOSED: 057 게이트 불가/부재/미기록 시 058 알파 "생존편향 상한·bound only" 강제(signed alpha fail-open 금지), 가능 시 as-of-date 유니버스(상폐 포함) 재구성
- [ ] [HARD][M-c] M3 walk-forward = 리밸런스별 반복 point-in-time engine.run(단일 full-sample OOS 보고 금지) + Bonferroni(N=1→α/1) + 50% 할인 적용
- [ ] [HARD][M-b] Bonferroni·50%할인·scorecard GO 단일 AND 판정 함수, 부호만 양수 non-PASS, 생존편향 작동 시 즉시 non-PASS 단락
- [ ] [HARD][M-a] n=리밸런스 주기 수(거래 수 아님), n<30 리밸런스 INCONCLUSIVE, GO 임계 약화 없음
- [ ] [HARD] GO 팩터도 페이퍼 OOS 전용 승급, 라이브 경로 미접촉(diff 0)
- [ ] [HARD] "저변동성 알파 없음"이 성공적 결과로 서술됨 + 비용/생존편향 정직성 플래그 명시
- [ ] 모멘텀·단기reversal·추세추종·vol-managed·팩터타이밍·ML/DL·최적화 미구현(7.2절 EX 전부) + 퀄리티/결합 미구현(7.1절 DEF, SPEC-059 연기)
- [ ] 6절 "이김의 정의" 충족: 신뢰할 수 있는 답 산출(양의 알파 발견 여부와 무관)
