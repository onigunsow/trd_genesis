# 구현 계획 — SPEC-TRADING-058 저변동성 팩터 전략 (Low-Volatility)

## 개요

이 프로젝트의 **첫 실제 알파 소스 시도**. LLM 재량(측정상 음수 엣지)을 대체할 **기계적·저회전·등가중 저변동성 팩터 전략**을 명세·검증한다. 새 비용 모델/하니스를 만들지 않고 SPEC-057 M1 데이터 토대 + `engine.run` + `scorecard.py`를 재사용한다. 양의 알파 발견이 아니라 **신뢰할 수 있는 답**이 목표다(6절).

**범위(v0.2.0)**: 저변동성/저베타 **단일 팩터만** 활성. 퀄리티(총수익성)·저변동성+퀄리티 결합은 입력 데이터 부재로 **SPEC-059로 연기**(spec.md DEF-1/2/3). 저변동성은 OHLCV만 필요하므로 SPEC-057 M1 가격 데이터로 즉시 진행 가능.

## SPEC-057 의존 선언 [HARD]

- 058 M2/M3는 SPEC-057 M1의 (a) point-in-time 과거 OHLCV 로더와 (b) as-of-date 유니버스 surface(상폐 포함, ADR-057-4)를 **재사용**한다. 새 로더를 만들지 않는다. **저변동성은 OHLCV만 쓰므로 펀더멘털 로더 불필요.**
- 058은 SPEC-057 REQ-057-M1-6 **생존편향 PRECONDITION GATE를 상속(fail-CLOSED)**한다. 057이 "멤버십/상폐 회수 불가"로 판정하거나 **결과가 부재/미기록이면** 058 알파는 자동으로 "생존편향 상한·부호보고 금지·bound only"가 된다(REQ-058-M2-5). 부재가 signed alpha로 fail-open 되지 않는다 — 이는 -14,840을 만든 바로 그 오류, 058의 단일 최중요 제약.
- **선행 의존(ADR-058-5)**: 057 M1 미완 시 058 M2/M3는 BLOCKED. 058 M1(저변동성 순수 함수)만 주입 픽스처로 병행 개발 가능.

## 기술 접근

세 마일스톤은 순차 의존한다(M1 → M2 → M3). M1 팩터 함수는 057 M1과 병행 가능하나, M2 실데이터 백테스트는 057 M1 완료가 선행 조건.

### M1 — 저변동성 팩터 신호 계산 (순수 함수) (Priority: High, 선행)

- 신규 모듈(`strategy/factor/` 하위 — `strategy/sizing/`의 SPEC-046 vol-targeting과 별개).
- **저변동성/저베타**(REQ-058-M1-1): trailing **120 거래일 고정**(단일 config 소스) 일간 수익률 변동성 또는 KOSPI 대비 베타로 랭킹, **최저 분위** long. KOSPI 대형주 한정(market_cap>1조 / 거래대금>100억 gate, daily_screen:239,246).
- 모든 함수: 주입 OHLCV를 받는 순수 함수, 결정적, no-look-ahead(T 시점 trailing만), 라이브 I/O 없음 → 픽스처 단위테스트(C-4).
- 데이터 부족 종목(< 120 trailing bars)은 랭킹에서 **명시적 제외**, 날조 값 imputation 금지(REQ-058-M1-4).
- **퀄리티/결합 미구현** — SPEC-059 연기(spec.md DEF-1/2). 058은 펀더멘털을 읽지 않는다.

### M2 — 포트폴리오 구성 + 비용 인지 백테스트 (Priority: High)

- 신규 포트폴리오 구성기: 최저 변동성 분위 → **1/N 등가중**(~10-20종목) + **월간 리밸런스**. 등가중·월간은 고정(최적화/고빈도 금지, REQ-058-M2-1).
- **회전 예산**(REQ-058-M2-2): 월간 회전율 측정·보고, <50%/월 초과 시 저회전 생존 제약 위반 플래그.
- **백테스트 = `engine.run`**(REQ-058-M2-3): prices + 월간 1/N weights → time-weighted equity curve. `DEFAULT_FEE_RATE`+`DEFAULT_TAX_RATE`+`DEFAULT_SLIPPAGE`(engine.py:21-23,67) 적용. **`run_walk_forward`(출구 스윕 하니스) 사용 금지**, 새 비용 모델 금지.
- **알파 정의 고정(time-weighted only)**(REQ-058-M2-4): net OOS 알파 = engine.run time-weighted equity-curve 수익률 vs KOSPI. **benchmark.py money-weighted 알파(:120-131)는 보고·게이트 어디에서도 금지.**
- **[B3] time-weighted → scorecard 어댑터(신규)**(REQ-058-M2-4a, [HARD]): `BacktestResult`(time-weighted `equity_curve`/`daily_returns`, engine.py:34-35,101-102)를 `scorecard.decide`가 소비하는 입력으로 변환 — `Analytics`(expectancy/PF/n) + `Benchmark`(alpha_pct=**time-weighted** strategy−KOSPI). [HARD] `Benchmark.alpha_pct`를 benchmark.py money-weighted로 채우지 않는다. scorecard는 오직 이 어댑터로만 공급 → 금지된 money-weighted 알파가 GO 게이트에 들어올 수 없다.
- **생존편향 게이트 상속(fail-closed)**(REQ-058-M2-5, [HARD]): 057 게이트가 불가 판정이거나 결과 부재/미기록이면 모든 058 결과를 "생존편향 상한·bound only"로 강제 다운그레이드, M3가 생존편향을 최상위 caveat로.
- 가능 판정 시(REQ-058-M2-6): 월간 리밸런스 윈도우별 as-of-date KOSPI 대형주 유니버스(상폐 포함) 재구성 후 포트폴리오 구성(057 M1-6a surface 재사용).

### M3 — Walk-forward OOS + 다중검정 + 50% 할인 + 단일 AND 판정 + 정직한 판정 (페이퍼 전용) (Priority: Medium)

- 신규 검증/판정 모듈.
- **Walk-forward OOS(반복 point-in-time)**(REQ-058-M3-1, [HARD]): **각 리밸런스 T에서 반복 point-in-time engine.run**, 랭킹/선택은 T 가용 데이터만, 성과는 후속 unseen 윈도우에서 측정, 리밸런스별로 연결. **단일 full-sample engine.run을 OOS로 보고 금지.**
- **다중검정**(REQ-058-M3-2, [HARD]): Bonferroni 보정(α/N). **저변동성 단독이라 N=1 → α/1=α.** 메커니즘은 generic 유지 → 059가 팩터 추가(N≥2) 시 자동 강화. 부호만 양수=PASS 금지(057 M2-3a 패턴).
- **50% 할인**(REQ-058-M3-3, [HARD]): GO 판정 전 백테스트 알파 50% 할인(McLean-Pontiff). raw·할인 알파 모두 표시.
- **기존 GO 게이트 재사용**(REQ-058-M3-4, [HARD]): `scorecard.py`/`validation_gate.py`, **어댑터(time-weighted) 경유 공급**. GO = expectancy>0 AND PF>1.0 AND alpha>0 AND n>=30(`_MIN_SAMPLE`). 임계 약화·병렬 관대 게이트 금지.
- **[B3·M-b] 단일 AND 판정 함수**(REQ-058-M3-4a, [HARD]): (1)Bonferroni 통과 AND (2)50% 할인 적용 AND (3)scorecard GO(어댑터 time-weighted 입력). 부호만 양수=PASS 금지. 생존편향 다운그레이드(REQ-058-M2-5) 작동 시 즉시 non-PASS 단락.
- **[M-a] 표본 floor (n=리밸런스 주기 수)**(REQ-058-M3-5, [HARD]): **n은 walk-forward OOS의 월간 리밸런스 주기 수**(round-trip 거래 수 아님). 어댑터가 `Analytics.n_closed`를 리밸런스 주기 수로 설정 → 리밸런스 내 거래 多가 ~30 리밸런스 누적 전 PASS 누설 못 함. n<30 리밸런스 → INCONCLUSIVE(PASS 금지).
- **페이퍼 전용 승급**(REQ-058-M3-6, [HARD]): GO 팩터도 **페이퍼 OOS 수집으로만**. order.py/smoke_gate.py/라이브 게이트/live_unlocked 미접촉.
- **정직한 판정**(REQ-058-M3-7, [HARD]): "저변동성 팩터 net 양의 OOS 알파 없음"을 유효·성공적 결과로 서술.
- **정직성 플래그**(REQ-058-M3-8, [HARD]): 비용모델 보수성(세금 0.18% floor·슬리피지 0.05%) + (생존편향 게이트 실패/부재 시)생존편향 최상위 caveat.

## 마일스톤 순서

1. M1 (선행, High) — 저변동성 순수 함수(057 M1과 픽스처로 병행 가능)
2. M2 (High) — 057 M1 완료 후, engine.run 백테스트 + time-weighted 어댑터 + 생존편향 게이트 상속(fail-closed)
3. M3 (Medium) — M2 결과 소비, OOS 검증·단일 AND 판정·페이퍼 승급

## 위험 (Risks)

- **R-1** [구조적·BLOCKING 상속]: 생존편향 — 057 M1-6 게이트가 불가/부재면 058 팩터 백테스트도 동등하게 오염. 저변동성은 바로 승자/패자(상폐 포함)를 가르는 변수이므로 생존종목만 백테스트하면 무가치 → REQ-058-M2-5(fail-closed)로 강제 다운그레이드. **058의 단일 최중요 제약.**
- **R-2** [선행 의존]: 057 M1 미완 → 058 M2/M3 BLOCKED → ADR-058-5로 058 M1만 병행, M2는 대기.
- **R-3** [GO 게이트 알파 모순, B3]: scorecard.decide가 benchmark.py money-weighted 알파를 소비 → "scorecard 그대로 재사용"이 금지된 money-weighted를 GO에 재유입 → REQ-058-M2-4a 어댑터(time-weighted)로 차단, C-7로 money-weighted GO 사용 금지.
- **R-4** [낙관 편향]: 백테스트 알파 과신 → REQ-058-M3-3 50% 할인 + REQ-058-M3-4a 단일 AND 판정 + REQ-058-M3-6 페이퍼 전용 승급으로 구조적 차단.
- **R-5** [회전 폭증]: 월간이라도 팩터 회전이 높으면 한국 비용에 잠식 → REQ-058-M2-2 회전 예산 측정·플래그.
- **R-6** [KOSPI 지수 결측]: 알파 산출에 KOSPI 벤치마크 필요 → 결측 시 알파 "insufficient data" 라벨(단 비용은 계속 정량).
- **R-7** ["알파 없음" 오인]: 빌드 실패로 오인 위험 → REQ-058-M3-7 + 6절 "이김의 정의"로 차단.
- **R-8** [n 정의 누설, M-a]: n을 거래 수로 잡으면 리밸런스 내 다수 거래가 30 미달 리밸런스에서 PASS 누설 → REQ-058-M3-5로 n=리밸런스 주기 수 고정, 어댑터가 n_closed를 리밸런스 수로 설정.
- **R-9** [퀄리티 연기 누락 위험]: 059가 퀄리티 구현 시 filing-date point-in-time(공시일 키잉)을 안 하면 새 look-ahead 발생 → spec.md DEF-3에 059 선행 요구로 기록(058에는 N/A).

## MX 태그 대상

- 저변동성 팩터 신호 함수 진입점(fan_in 발생 시): `@MX:NOTE` (팩터 정의·120일 window·config 소스 명시).
- 포트폴리오 구성기의 월간 리밸런스/1/N 비중 경계: `@MX:ANCHOR` (저회전 불변식, look-ahead 차단).
- time-weighted → scorecard 어댑터 진입점: `@MX:ANCHOR` (B3 불변식 — money-weighted 알파 주입 금지, n=리밸런스 주기 수). `@MX:REASON` 필수.
- engine.run 배선 지점: `@MX:NOTE` (비용 모델·time-weighted 알파 정의 참조).

## 검증 방식

- 모든 신규 코드는 주입 픽스처 위 단위테스트(C-4): 저변동성 결정성, no-look-ahead, 1/N·월간 불변식, 회전 측정, **어댑터의 time-weighted 알파 = engine.run equity-curve 수익률−KOSPI(money-weighted 미사용)**, **어댑터 n=리밸런스 주기 수**.
- 단일 AND 판정 함수: Bonferroni·50%할인·scorecard GO 셋 중 하나만 통과해도 non-PASS임을 단위테스트.
- 생존편향 fail-closed: 057 게이트 결과 부재/null/미기록 입력 시 bound-only 다운그레이드(signed alpha 금지)를 단위테스트.
- SQL/DB 경로 변경 시 실-Postgres 통합테스트(SPEC-056) 실행 — 거짓그린 차단.
- 라이브 경로 미접촉 확인(order.py/smoke_gate.py/라이브 게이트/live_unlocked diff 0).
- 기존 테스트 회귀 0; engine.py/scorecard.py/validation_gate.py/benchmark.py 기존 동작 회귀 0(benchmark.py는 미사용·미변경).
