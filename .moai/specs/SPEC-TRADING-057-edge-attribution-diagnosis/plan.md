# 구현 계획 — SPEC-TRADING-057 엣지 귀인 진단

## 개요

연구/진단 SPEC. 새 알파를 만들지 않는다. 기존 `backtest/*` + `edge/*` 자산을 재사용해, 진입 신호에 알파가 있는지 측정할 능력을 구축하고, -14,840 KRW/거래를 귀인 분해한다.

## 기술 접근

세 마일스톤은 엄격히 순차 의존한다 (M1 → M2 → M3). M2는 M1의 point-in-time 로더 없이는 의미 없고, M3는 M2의 측정 결과를 소비한다.

### M1 — 과거 OHLCV 데이터 파이프라인 + 생존편향 게이트 (Priority: High, 선행)

- **PRECONDITION GATE 선행 (REQ-057-M1-6, D1)**: 알파 측정 전, 두 사실을 실증한다 — (1) pykrx `get_index_portfolio_deposit_file`이 as-of-date 과거 멤버십(상폐/제외 종목 포함)을 주는가, (2) 상폐 종목 과거 OHLCV가 회수되는가. 현 로더(`universe.py:80`, `kospi200_backfill.py:71-78,143-159`)는 오늘 생존종목만 받으므로 이 게이트가 point-in-time 재구성 가능 여부를 판정한다.
  - 가능 시(M1-6a): 리밸런스 윈도우별 as-of-date 유니버스를 재구성(패자/상폐 포함)해 M2에 공급. **재사용 가능한 일반 surface로 설계**(SPEC-058 팩터 백테스트 의존, ADR-057-4).
  - 불가 시(M1-6b): M2 알파를 "생존편향 상한 — 부호보고 금지, bound only"로 강제 다운그레이드하고 M3가 생존편향을 최상위 caveat로 헤드라인.
- 신규 모듈(`backtest/` 하위): `pykrx_adapter`의 `fetch_ohlcv`/`fetch_fundamentals`/`fetch_flows`를 호출해 DB/캐시에 적재한 뒤, `walk_forward.run`이 기대하는 in-memory bar 시퀀스로 변환하는 로더.
- `_slice_bars(ts <= cutoff)` 불변식을 로더 출력에 적용 — 직접 인덱싱 금지.
- 결정성: 같은 `(symbol set, range)`에 대해 byte-identical 출력. 테스트는 주입 픽스처로 검증(라이브 pykrx 미접촉).
- 커버리지 갭(누락 심볼/날짜)을 명시적으로 보고.
- 어댑터 자체는 미변경 — 회귀 0.

### M2 — 진입 신호 백테스트 가능화 (Priority: High)

- 신규 모듈: `daily_screen._screen_ticker`를 **비대칭 두 클래스로 분리**(D7):
  - score 피처(랭킹 가능, 알파 측정 대상): RSI 30-70(`:267`), PER<15(`:272`), foreign 5d net>0(`:277`) → 각 피처로 리밸런스일 T마다 랭킹 포트폴리오 구성.
  - hard gate(유니버스 정의, per-feature 포트폴리오 불가): market_cap>1조(`:239` return None), 거래대금>100억(`:246` return None) → 효과는 "유니버스 필터"로 특성화, 알파 포트폴리오로 측정하지 않음.
- 선택/랭킹은 T 시점 정보만 사용(point-in-time, M1 불변식 상속).
- 각 포트폴리오의 **net OOS 알파 = `engine.run` time-weighted equity-curve 수익률**(D5) vs KOSPI를 `DEFAULT_FEE_RATE`+`DEFAULT_SLIPPAGE`+`DEFAULT_TAX_RATE` 차감 후 산출. `benchmark.py`의 money-weighted와 혼용 금지, 관계만 명시.
- **다중검정 보호(D2)**: N개 score 피처(현재 3) 대상 → 각 피처 알파에 Bonferroni 보정(α/N) 적용. 부호만 양수라고 PASS 금지. 리밸런스 표본 < floor(기본 30) 시 INCONCLUSIVE(PASS 아님).
- LLM 레이어는 절대 백테스트하지 않음(ADR-057-1).
- (Optional) 스크리너 full-pass 복합 신호를 baseline 후보로 측정.

### M3 — 귀인 분해 리포트 (Priority: Medium)

- 신규 모듈: 단일 리포트로 -14,840을 5컴포넌트((a)진입(b)비용(c)출구(d)사이징(e)LLM-재량 델타)로 분해.
- **분해 방법론 명시(D4)**: 기계적 등가중 후보 포트폴리오를 baseline으로 두고, 한 번에 한 팩터만 swap하는 순차 counterfactual로 각 컴포넌트의 marginal 효과를 귀인. **명시적 RESIDUAL 버킷**을 두어 (a)+(b)+(c)+(d)+(e)+residual = 측정 총합(-14,840) 합치성 검증을 리포트에 표시.
- **비용(b)은 필수 정량 컴포넌트** — `engine.py` 비용모델로 직접 계산 가능하므로 "insufficient data" valve 불가. (a)(c)(d)(e)는 point-in-time 데이터가 실제 부재할 때만 insufficient 허용(blanket escape 금지).
- **비용모델 보수성 플래그(D3)**: 세금 0.18% floor(실제 0.18-0.23% 하단)·대형주 슬리피지 0.05% 가정 → 실제 비용은 소형/저유동성에서 이를 초과할 수 있고 알파를 상향 편향시킴을 명시(n=8 정직성 플래그의 비용 측 대응).
- `postmortem.py`/`confidence.py`/`roundtrips.py`/`trade_stats.py` 재사용 — 재구현 금지.
- [HARD] n=8 합성 SELL 페이퍼 표본은 일화로 명시; load-bearing은 M1/M2 과거 백테스트. M1-6b 발동 시 생존편향을 최상위 caveat로.
- M2가 양의 알파 신호를 못 찾으면 그것을 명확한 성공적 진단 결과로 서술.
- 정량화 불가 컴포넌트는 "insufficient data" 라벨(날조 금지).

## 마일스톤 순서

1. M1 (선행, High) — 데이터 파이프라인 없이는 나머지가 무의미
2. M2 (High) — M1 완료 후
3. M3 (Medium) — M2 결과 소비

## 위험 (Risks)

- **R-1**: pykrx 다년치 백필이 느리거나 일부 심볼/기간 결측 → M1-4(갭 명시 보고)로 흡수, 진단은 가용 범위로 진행.
- **R-2** [구조적, BLOCKING 출신]: 생존편향 — 현 유니버스 파이프라인(`universe.py:80`, `kospi200_backfill.py`)은 오늘 생존한 KOSPI200만 적재하므로 패자/상폐가 데이터셋에서 통째로 빠진다. 진입 피처(RSI/PER/foreign)가 바로 승자와 패자를 가르는 변수이므로 이는 핵심 진단 질문을 직접 오염시키고 알파를 상향 편향시킨다. "한계만 문서화"는 불충분 → **REQ-057-M1-6 PRECONDITION GATE로 승격**: point-in-time 멤버십·상폐 OHLCV 회수 가능성을 실증하고, 가능 시 재구성(M1-6a), 불가 시 M2 알파를 "생존편향 상한·부호보고 금지"로 강제 다운그레이드 + M3 최상위 caveat(M1-6b).
- **R-5** [다중검정]: score 피처 3개를 KOSPI 대비 동시 검정 → 순수 노이즈가 1개에서 가짜 양의 알파를 낼 수 있음 → REQ-057-M2-3a Bonferroni + M2-3b 표본 floor로 차단.
- **R-3**: "측정한 모든 신호가 알파 없음" 결과가 빌드 실패로 오인될 위험 → REQ-057-M3-4 + 6절 "이김의 정의"로 차단.
- **R-4**: 신규 로더가 `pykrx_adapter` 기존 호출자(스크리너)에 회귀를 줄 위험 → ADR-057-3(래핑만, 어댑터 미변경)로 차단, 통합테스트로 검증.

## MX 태그 대상

- 신규 point-in-time 로더 슬라이스 경계: `@MX:ANCHOR` (walk_forward `_slice_bars`와 동일 불변식, look-ahead 누출 방지).
- 진입 피처 측정기 진입점(fan_in 발생 시): `@MX:NOTE`.

## 검증 방식

- 모든 신규 코드는 주입 픽스처 위 단위테스트(C-4).
- SQL/DB 경로 변경 시 실-Postgres 통합테스트(SPEC-056) 실행 — 거짓그린 차단.
- 라이브 경로 미접촉 확인(order.py/smoke_gate.py/라이브 게이트 diff 0).
