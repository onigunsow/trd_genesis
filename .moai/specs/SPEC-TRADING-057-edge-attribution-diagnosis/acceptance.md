# 수용 기준 — SPEC-TRADING-057 엣지 귀인 진단

## Given-When-Then 시나리오

각 시나리오에 EARS 패턴 라벨을 병기한다(D6). GWT 형식은 유지한다.

### AC-M1-1: point-in-time 로더 결정성 (REQ-057-M1-2) — EARS: Ubiquitous

- **Given** 고정된 `(symbol set, date range)`와 주입 OHLCV 픽스처
- **When** 로더를 두 번 실행
- **Then** 두 bar 시퀀스가 byte-identical 하다

### AC-M1-2: look-ahead 차단 (REQ-057-M1-3, M1-5) — EARS: State-Driven + Unwanted

- **Given** cutoff 날짜 T와, T 이후 날짜의 bar를 포함한 픽스처
- **When** 로더가 cutoff T로 bar를 공급
- **Then** `ts > T`인 bar는 단 하나도 포함되지 않는다 (walk_forward `_slice_bars` 불변식과 동일)

### AC-M1-3: 커버리지 갭 명시 보고 (REQ-057-M1-4) — EARS: Event-Driven

- **Given** 캐시/DB 가용 범위를 초과하는 요청 범위
- **When** 로더 실행
- **Then** 누락 심볼/날짜가 명시 보고되고, 부분 데이터가 조용히 반환되지 않는다

### AC-M1-4: 생존편향 PRECONDITION GATE (REQ-057-M1-6, M1-6a/6b) [HARD] — EARS: Ubiquitous + State-Driven

- **Given** 현 유니버스 로더(오늘 생존 KOSPI200만 적재)와 pykrx 멤버십/상폐 OHLCV 회수 능력에 대한 미검증 가정
- **When** M1이 알파 측정 전 precondition 게이트를 실행해 (1) as-of-date 과거 멤버십(상폐 포함) 지원 여부와 (2) 상폐 종목 과거 OHLCV 회수 가능성을 실증
- **Then** 가능하면 리밸런스 윈도우별 as-of-date 유니버스(패자/상폐 포함)를 재구성해 M2에 공급하고(M1-6a), 불가능하면 M2 알파를 "생존편향 상한 — 부호보고 금지, bound only"로 강제 다운그레이드하고 M3가 생존편향을 최상위 caveat로 헤드라인한다(M1-6b). 어느 경로든 precondition 결과가 명시 기록된다

### AC-M2-1: score 피처별 net OOS 알파 측정 (REQ-057-M2-1, M2-3) — EARS: Ubiquitous

- **Given** M1 로더가 공급한 다년치(또는 가용 범위) 이력과 KOSPI 벤치마크
- **When** 각 랭킹 가능 score 피처(RSI/PER/foreign)로 포트폴리오를 구성하고 OOS 평가
- **Then** 각 피처에 대해 비용(`DEFAULT_FEE_RATE`+`DEFAULT_SLIPPAGE`+`DEFAULT_TAX_RATE`) 차감 후 **net OOS 알파 = `engine.run` time-weighted equity-curve 수익률** vs KOSPI가 산출된다(money-weighted benchmark.py와 혼용 금지, 관계 명시)

### AC-M2-1b: gate vs score 비대칭 처리 (REQ-057-M2-1) — EARS: Ubiquitous

- **Given** `daily_screen._screen_ticker`의 hard gate(market_cap`:239`/거래대금`:246`)와 score 피처(RSI/PER/foreign)
- **When** M2 측정 실행
- **Then** gate는 유니버스 필터 효과로만 특성화되고 per-feature 알파 포트폴리오로 측정되지 않으며, score 피처만 랭킹 포트폴리오로 알파가 측정된다

### AC-M2-1c: 다중검정 보호 (REQ-057-M2-3a, M2-3b) [HARD] — EARS: Ubiquitous + State-Driven

- **Given** N개(현재 3) score 피처를 KOSPI 대비 동시 검정
- **When** 각 피처 알파를 판정
- **Then** Bonferroni 보정(α/N) 유의수준을 통과해야만 PASS이며 부호만 양수인 것으로는 PASS가 아니고, 리밸런스 표본 < floor(기본 30)인 피처는 알파 부호/크기와 무관하게 INCONCLUSIVE로 라벨된다

### AC-M2-2: point-in-time 규율 유지 (REQ-057-M2-2) — EARS: State-Driven

- **Given** 리밸런스일 T
- **When** 피처 랭킹/선택 수행
- **Then** T 시점에 가용한 펀더멘털/수급만 사용하며, restated 값이나 미래 bar가 개입하지 않는다

### AC-M2-3: LLM 미백테스트 (REQ-057-M2-4) [HARD] — EARS: Unwanted

- **Given** M2 측정기
- **When** 전체 측정 실행
- **Then** LLM 재량 결정 레이어에 대한 백테스트/점수/알파 주장이 어디에도 생성되지 않는다 (기계적 피처만 측정)

### AC-M3-1: 5컴포넌트 + RESIDUAL 합치성 분해 (REQ-057-M3-1) [HARD] — EARS: Ubiquitous

- **Given** M2 측정 결과와 기존 round-trip/postmortem 데이터
- **When** 귀인 리포트 생성
- **Then** 리포트가 기계적 등가중 baseline + 순차 counterfactual(한 번에 한 팩터 swap)로 -14,840 KRW/거래를 (a)진입 (b)비용 (c)출구 (d)사이징 (e)LLM-재량 델타 + 명시적 RESIDUAL로 분해하고, **6항목 합 = 측정 총합** 합치성 검증을 표시하며, 비용(b)은 `engine.py` 비용모델로 정량화된다("insufficient" 불가)

### AC-M3-1b: 비용모델 보수성 플래그 (REQ-057-M3-1b) [HARD] — EARS: Ubiquitous

- **Given** `engine.py` 비용 상수(세금 0.18% floor, 슬리피지 0.05%)
- **When** 귀인 리포트 생성
- **Then** 리포트가 세금은 실제 0.18-0.23% 범위의 하단이고 슬리피지는 대형주 가정이며 소형/저유동성에서 실제 비용이 이를 초과해 알파를 상향 편향시킬 수 있음을 명시한다

### AC-M3-2: edge/* 재사용 (REQ-057-M3-2) — EARS: Ubiquitous

- **Given** 귀인 리포트 모듈
- **When** 코드를 검사
- **Then** postmortem/confidence/roundtrips/trade_stats를 import해 사용하며, 동일 로직(4분류, Spearman, round-trip, per-trade stats)을 재구현하지 않는다

### AC-M3-3: n=8 정직성 플래그 (REQ-057-M3-3) [HARD] — EARS: Ubiquitous

- **Given** 라이브-fill postmortem 데이터(n=8, 합성 SELL)
- **When** 리포트 생성
- **Then** 리포트가 이 n=8을 일화적/통계적 무의미로 명시하고, load-bearing 증거가 M1/M2 과거 백테스트임을 명시한다

### AC-M3-4: "알파 없음"도 성공 (REQ-057-M3-4) [HARD] — EARS: Event-Driven

- **Given** M2가 양의 net OOS 알파 신호를 하나도 찾지 못한 경우
- **When** 리포트 생성
- **Then** 리포트가 이를 유효하고 성공적인 진단 결과로 명확히 서술하며 에러/미완료로 처리하지 않는다

### AC-M3-5: 미정량 컴포넌트 라벨 (REQ-057-M3-5) — EARS: State-Driven

- **Given** 어떤 귀인 컴포넌트가 가용 데이터로 정량화 불가(단, 비용(b)은 제외 — 항상 정량)
- **When** 리포트 생성
- **Then** 해당 컴포넌트가 "insufficient data"로 라벨되고 날조된 숫자가 들어가지 않는다

## 엣지 케이스

- pykrx 백필이 일부 기간만 반환 → M1-4 갭 보고 + M3 한계 명시
- 단일 피처 리밸런스 표본 < floor(기본 30) → INCONCLUSIVE 라벨(PASS 금지), 부호만으로 결론 내지 않음
- 다중검정: 3 피처 중 1개만 우연히 양수 → Bonferroni 보정 통과 못하면 PASS 아님
- M1-6 게이트 불가 판정(상폐 OHLCV/멤버십 회수 불가) → M2 알파 전체가 "생존편향 상한·bound only", M3 최상위 caveat
- KOSPI 지수 데이터 결측 → 알파 컴포넌트 "insufficient data" 라벨(단 비용(b)은 영향 없음, 계속 정량)

## 품질 게이트 (Quality Gate)

- 신규 코드 단위테스트: 주입 픽스처 기반, look-ahead 불변식 + 결정성 검증
- DB/SQL 경로 변경 시 실-Postgres 통합테스트(SPEC-056) 통과
- 라이브 경로 diff 0 (`order.py`/`smoke_gate.py`/라이브 게이트)
- 기존 테스트 회귀 0
- `pykrx_adapter`/`walk_forward`/`engine`/`exit_sweep`/`edge/*` 기존 동작 회귀 0

## Definition of Done

- [ ] M1 로더가 walk_forward에 다년치(또는 가용) 한국 주식 이력을 point-in-time 결정적으로 공급
- [ ] [HARD] M1-6 생존편향 PRECONDITION GATE 실행: 멤버십/상폐 OHLCV 회수 가능성 실증 → 가능 시 as-of-date 유니버스 재구성(재사용 가능 surface), 불가 시 M2 알파 "생존편향 상한·bound only" 강제
- [ ] M2가 각 score 진입 피처(RSI/PER/foreign)의 net OOS 알파 = time-weighted equity-curve vs KOSPI(비용 차감)를 산출, gate(market_cap/거래대금)는 유니버스 필터로 특성화
- [ ] [HARD] 다중검정 보호: Bonferroni(α/N) + 표본 floor(30) 미달 INCONCLUSIVE 적용 (부호만 양수=PASS 금지)
- [ ] M3 단일 리포트가 -14,840을 5컴포넌트 + RESIDUAL로 분해(baseline + 순차 counterfactual), 합치성 검증 표시, edge/* 재사용, 비용(b) 필수 정량
- [ ] [HARD] 리포트가 n=8 일화성 + 비용모델 보수성(세금 floor·슬리피지) + (M1-6b 시)생존편향을 명시하고 load-bearing=과거 백테스트를 명시
- [ ] [HARD] "알파 없음" 결과가 성공적 진단으로 서술됨
- [ ] 알파 정의 = time-weighted equity-curve(engine.run)로 고정, benchmark.py money-weighted와 혼용 안 함(관계만 명시)
- [ ] LLM 레이어 미백테스트(ADR-057-1) 보존
- [ ] 라이브 경로/어댑터 미접촉, 비용 모델 신규 생성 없음
- [ ] 6절 "이김의 정의" 충족: 신뢰할 수 있는 답 산출 (양의 알파 발견 여부와 무관)
