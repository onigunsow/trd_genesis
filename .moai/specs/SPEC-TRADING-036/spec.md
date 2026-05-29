---
id: SPEC-TRADING-036
version: 0.2.0
status: draft
created: 2026-05-29
updated: 2026-05-29
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "불장 모드 + 후기 사이클 천장 방어 + 한국 모멘텀 데이터 레이어 (SPEC-016 Phase 3)"
related_specs:
  - SPEC-TRADING-016   # 3-Phase 원본. 본 SPEC = Phase 3(REQ-016-3-1/3-2) + 데이터 의존(REQ-016-2-4) 번들. S-3 우선순위표·R-3/R-7·C-2/C-6/C-7 계승
  - SPEC-TRADING-035   # Phase 2 core loop(완료). regime 캐시·get_effective_regime·regime_branch.py 위에 얹는다(불장=035 가 defer 한 aggressive 프로필)
  - SPEC-TRADING-033   # 자동 손절/익절 워치독. severe 강제 deleverage 가 position_watchdog 의 direct-sell-bypass 패턴 재사용
  - SPEC-TRADING-012   # decision.jinja dynamic_thresholds — 프롬프트 컨텍스트 주입 선례(불장 라인)
  - SPEC-TRADING-029   # balance()/cash_pct·holdings — 현금 타깃/방어 강제매도의 컨텍스트 소스
  - SPEC-TRADING-014   # 뉴스 분류기 impact-5 — adaptive 매크로 트리거(본 SPEC 비목표, defer 근거)
---

# SPEC-TRADING-036 — 불장 모드 + 후기 사이클 천장 방어 + 한국 모멘텀 데이터 레이어 (SPEC-016 Phase 3)

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-29 | 0.1.0 | Initial draft. 매도/배분 개선 A→B→C(SPEC-033/034/035 전부 배포) 의 자연스러운 후속. SPEC-016 **Phase 3**(불장 모드 REQ-016-3-1 + 후기 사이클 방어 REQ-016-3-2)를, Phase 3 방어가 의존하는 **deferred 데이터 레이어**(REQ-016-2-4: 한국 시장 모멘텀)와 **한 SPEC 으로 번들**. 사용자(리스크 오너)가 정책 확정. **3대 안전 게이트 명시**: (1) 공격 파라미터 paper-only, (2) 불장 모드는 방어와 짝으로만 출시(단독 금지), (3) `late_cycle_defense_active==true` 시 불장 강제 OFF. 사용자 정책 결정 반영 — 2026-05-29 | onigunsow |
| 2026-05-29 | 0.2.0 | 데이터 레이어 수정(REQ-036-1) — 사용자 경험적 검증 반영. 초안의 "KRX MDC/KOFIA HTML 스크래퍼" 가정을 **공식 keyed API** 로 교체: 신용융자/예탁금 = **ECOS 901Y056 S23E/S23A**(이미 통합 `ecos_adapter.py`, 라이브 검증 35.7조/124.8조, monthly), V-KOSPI = **KRX OpenAPI `idx/drvprod_dd_trd`**(keyed, 현재 401 — per-API 승인 대기, 승인 시 코드 변경 없이 자동 활성). 신규 `data/krx_market.py` MDC 스크래퍼 → `data/krx_openapi.py` + `ecos_adapter.py` 확장으로 교체. **R-2 대폭 하향**(취약 스크래핑 제거). Q-1(endpoint 사냥) RESOLVED, Q-2 를 VKOSPI row 식별로 교체. 사용자 검증 반영 — 2026-05-29 | onigunsow |

---

## Scope Summary

본 SPEC 은 SPEC-016 의 **Phase 3**(불장 모드 + 후기 사이클 천장 방어)를, 그 방어가 평가에 사용하는
**deferred 데이터 레이어**(REQ-016-2-4 한국 시장 모멘텀)와 함께 **하나의 통합 SPEC** 으로 구현한다.
SPEC-035(Phase 2 core loop, 오늘 배포)가 깐 regime 캐시·`get_effective_regime()`·`regime_branch.py`
보수 분기 위에 얹는다.

세 축:

1. **데이터 레이어 (REQ-036-1)** — `build_macro_context.py`(06:00)에 `## 한국 시장 모멘텀` 섹션을
   추가한다. robust 신호(KOSPI/KOSDAQ 일간%·5일%·52주 비교, 외국인/기관/개인 flows, VIX)는 pykrx/
   yfinance 로 **항상** 채우고, 외부 소스가 필요한 신용잔고·투자자예탁금·V-KOSPI 는 **scraper-attempt +
   graceful fallback**(실패/stale>24h → `(unavailable)` 마커, 빌드 차단 금지)으로 시도한다(R-2 다중
   소스 완화). 이 데이터는 16:00 방어 평가의 입력이기도 하다.
2. **불장 모드 (REQ-036-2)** — `current_regime=='bull'` 일 때의 **공격적** 프로필(SPEC-035 가 명시적으로
   defer 한 것). **paper-only**, **방어와 짝**, **late_cycle 시 강제 OFF**.
3. **후기 사이클 천장 방어 (REQ-036-3)** — 평일 16:00 에 5개 신호를 평가해 단계(stage)별 현금 바닥·진입
   차단·강제 deleverage 를 강제한다. SPEC-016 S-3 우선순위표대로 방어는 **항상 불장보다 우선**한다.

### A→B→C 그 다음

- **A = SPEC-033(완료)**: 자동 손절/익절 워치독(`*/5`). ATR **변동성 레짐**(macro regime 과 분리).
- **B = SPEC-034(완료)**: 휴면 포트폴리오 페르소나 사이클 연결(buy-only 사이징).
- **C = SPEC-035(완료)**: macro regime 을 Decision/Risk/Portfolio 보수 분기에 반영(현금 타깃 시프트).
- **본 SPEC(Phase 3)**: C 가 깐 regime 레일 위에서 **불장 활용 + 천장 방어**를 한 묶음으로 켠다.

### 비즈니스 목표 vs 안전 가드 (Capital Preservation)

사용자(가족 부양 책임 개인 투자자)는 월 10~20% 의 공격적 목표를 명시했으나 "자본 보전 우선" 을 반복
강조했다. 따라서 Phase 3 의 공격성은 **세 안전 게이트**로 봉인된다(아래 Constraints, REQ-036-2 참조):
공격 파라미터는 **paper 환경에서만**, 불장 모드는 **방어와 짝으로만**, 방어 활성 시 **불장 강제 OFF**.

---

## Goals

- **G-1**: `build_macro_context.md` 가 `## 한국 시장 모멘텀` 섹션을 갖고, robust 신호는 항상, 외부 신호는
  graceful 하게(가능하면 값, 실패 시 `(unavailable)`) 채운다.
- **G-2**: `current_regime=='bull'` AND NOT `late_cycle_defense_active` AND `trading_mode=='paper'` 일 때
  Decision/Risk 가 공격 프로필(1~2종 집중, 현금 10~20%, 보유 4~10일, event-CAR |1.0%|)로 동작한다.
- **G-3**: 평일 16:00 에 5개 후기 사이클 신호가 평가되고, 임계 초과 시 단계별 방어(현금 바닥/진입 차단/
  강제 deleverage)가 강제되며 `late_cycle_events` 에 기록된다.
- **G-4 (안전)**: 불장 모드는 방어 페어링 없이 단독 활성화되지 않으며(C-2), `late_cycle_defense_active`
  가 true 인 동안 자동 OFF 되고(S-3), 공격 파라미터는 live 모드에서 적용되지 않는다(C-7).
- **G-5 (안전)**: `limits.py` 하드 게이트·halt·회로차단은 최종 게이트로 불변. 외부 fetcher 실패는 빌드/
  사이클을 절대 크래시시키지 않는다(R-2).
- **G-6**: 베이스라인 853 passed 대비 신규 회귀 0, 신규 코드 85%+ 커버리지.

---

## Requirements (EARS)

### REQ-036-1: 한국 시장 모멘텀 데이터 레이어 (Ubiquitous + Event-Driven)

`build_macro_context.py`(06:00 `ctx_macro` 잡)가 생성하는 `macro_context.md` 는 `## 한국 시장 모멘텀`
섹션을 **반드시 포함**하고, 가용한 신호로 채워야 한다. 이 데이터는 16:00 방어 평가(REQ-036-3)의 입력이다.
모든 외부 소스는 **공식 keyed API**(취약한 HTML/OTP 스크래핑 아님)다 — ECOS 는 이미 통합되어 있고,
KRX OpenAPI 는 keyed.

- **(a) Ubiquitous (robust — 항상 채움)** — 시스템은 매 빌드마다 다음을 pykrx/yfinance 로 채워야 한다:
  - KOSPI / KOSDAQ 일간 변동률(%), 5일 변동률(%) (pykrx `get_index_ohlcv`, 코드 `1001`/`2001`)
  - 외국인 / 기관 / 개인 순매수 (최근 5거래일, 기존 `flows` 테이블)
  - VIX (yfinance `^VIX`)
  - KOSPI 52주 최고가 대비 현재가 비율(%)
- **(b) Ubiquitous (신용융자/예탁금 — ECOS 공식 API, monthly)** — 시스템은 신용융자 잔고(빚투)와
  투자자예탁금을 **한국은행 ECOS API**(이미 통합 — `src/trading/data/ecos_adapter.py`, `ECOS_API_KEY`
  존재)로 채운다:
  - 통계표 **901Y056 "증시주변자금동향"**, item **S23E = 신용융자 잔고**, **S23A = 투자자 예탁금**
    (cycle=`M` 월별, 단위=원). 기존 `macro_indicators` 테이블에 캐시.
  - 월 단위·약 1개월 시차는 이 **느린 구조적 후기 사이클 신호**(절대 레벨 임계, 일간 델타 아님)에
    허용 가능하다. 검증 라이브값: 신용융자 2026-04 = **35.7조원**(이미 >35조 moderate 임계),
    예탁금 2026-04 = **124.8조원**.
  - fetch 실패 또는 stale(>예상 갱신 주기) 시 `(unavailable: <사유>)` 마커, 빌드 차단 금지.
- **(c) Ubiquitous (V-KOSPI — KRX OpenAPI, keyed)** — 시스템은 V-KOSPI(코스피200 변동성지수)를
  **KRX 공식 OpenAPI**(openapi.krx.co.kr)로 채운다:
  - endpoint `idx/drvprod_dd_trd`("파생상품지수 시세정보" — KOSPI200 변동성지수/VKOSPI 포함).
    base `https://data-dbg.krx.co.kr/svc/apis`, header `AUTH_KEY: <key>`, param `basDd=YYYYMMDD`.
    키는 `.env` 에 존재(`#openapi.krx.co.kr` 주석 + `api_key=...`).
  - **현재 상태**: 모든 endpoint 가 HTTP **401 "Unauthorized API Call"** 을 반환한다 — 키는 있으나
    KRX OpenAPI My Page 에서 **per-API 이용신청+승인(~1일)** 이 아직 완료되지 않았기 때문. 따라서
    V-KOSPI 는 승인 전까지 graceful `(unavailable)` 폴백을 쓰고, **승인 후 코드 변경 없이 자동 활성화**
    된다.
- **(d) Event-Driven** — **When** 외부 fetcher(ECOS·KRX OpenAPI)가 예외/타임아웃/401 을 받으면, **then**
  시스템은 해당 필드를 `(unavailable: <사유>)` 로 기록하고 다음 필드로 진행한다(전체 빌드 abort 금지,
  `except Exception:` graceful).
- **(e)** 신규 어댑터 `src/trading/data/krx_openapi.py`(KRX OpenAPI 파생상품지수) + 기존
  `src/trading/data/ecos_adapter.py` **확장**(901Y056 S23E/S23A 시리즈 추가)이 외부 fetch 를 담당한다.
  섹션 형식은 S-2 형식을 따른다.
- **(f) 비목표(명시 defer)** — adaptive impact-5 뉴스 트리거(REQ-016-2-3 b)는 구현하지 않는다.

#### Acceptance Criteria — REQ-036-1

- [ ] `python -m trading.contexts.build_macro_context` (또는 동등) 실행 후 `macro_context.md` 에
      `## 한국 시장 모멘텀` 섹션이 존재한다(`grep -c '한국 시장 모멘텀' ≥ 1`).
- [ ] robust 신호(KOSPI/KOSDAQ 일간%·5일%, 외국인/기관/개인 flows, VIX, 52주 비율)가 채워진다.
- [ ] ECOS 901Y056 S23E/S23A fetch 가 비어있지 않은 **조원 단위** 값을 반환한다(신용융자·예탁금;
      라이브 sanity: 신용융자 ~35조, 예탁금 ~125조 규모).
- [ ] V-KOSPI fetcher 가 **401 에 `(unavailable)` 을 반환**하고, KRX OpenAPI 파생상품지수 서비스가
      **승인되면 코드 변경 없이 수치값을 반환**한다(승인 전/후 양쪽 테스트 — 401 시 graceful).
- [ ] 외부 fetch 강제 실패 mock 주입 시 신용융자/예탁금/V-KOSPI 가 `(unavailable: ...)` 로 표기되고
      빌드는 성공(exit 0)한다(음성 테스트 — 크래시/abort 없음).
- [ ] stale 데이터는 `(unavailable: stale)` 마커로 표기된다.
- [ ] `krx_openapi.py` / `ecos_adapter.py` fetcher 가 예외/타임아웃/401 시 `(unavailable)` 을 반환하고
      절대 raise 하지 않는다(graceful, 빌드/사이클 크래시 금지).

**Dependencies**: 없음 (REQ-036-2/3 와 병렬 가능, 단 REQ-036-3 의 일부 신호가 이 데이터를 읽음).

---

### REQ-036-2: 불장 모드 — 공격 프로필 (State-Driven + Unwanted)

**While** `current_regime == 'bull'` AND `late_cycle_defense_active == false` AND
`trading_mode == 'paper'` 이면, Decision 과 Risk 페르소나는 **공격적 매매 모드**로 전환된다.
이는 SPEC-035 의 conservative 분기와 **별개의(더 공격적인) 프로필**이며, 위 세 조건이 **모두** 참일
때만 적용된다.

- **(a) State-Driven — Decision 목표 (SPEC-016 원본 파라미터)**:
  - 동시 보유 종목 **1~2개**로 집중 (`target_holdings_count ∈ [1, 2]`)
  - 현금 바닥 **10~20%** (`cash_target ∈ [10, 20]`)
  - 보유 기간 가이드 **4~10일**
  - event-CAR 임계 **|1.0%|** (기존 |1.5%| 에서 강화)
- **(b) State-Driven — Risk 한도**:
  - 섹터 집중도 한도 **+10%pt**
  - 단일 종목 max position **+10%pt**
- **(c) Unwanted — paper-only 하드 가드** — `trading_mode == 'live'` 인 동안 시스템은 (a)/(b)의 공격
  파라미터를 **적용해서는 안 된다**. live 에서는 SPEC-035 의 conservative bull 분기(현금 바닥 20%,
  conf −0.05)로 폴백한다. 이 게이트는 **Python enforcement(하드)** 로 — 프롬프트 신뢰 금지. 실거래
  전환은 별도 사용자 승인이 필요하다(C-7).
- **(d) Unwanted — 방어 우선 강제 OFF** — `late_cycle_defense_active == true` 인 동안 불장 모드는
  **자동 비활성**된다(강제 OFF, 사용자 토글 불가). S-3 우선순위표 참조.
- **(e) Ubiquitous — 컨텍스트 주입** — 불장 활성 시 Decision/Risk 시스템 프롬프트에 SPEC-016 불장
  컨텍스트 라인을 자동 주입한다(예: "지금은 강세장. 보수 모드 해제. 불장 활용 적극. 단 후기 사이클 신호
  시 즉시 방어 전환."). + 하드 현금 바닥(10~20%)은 `regime_branch.enforce_cash_floor` 패턴의 Python
  가드로 병행 enforce(LLM 무시 방지).
- **(f) Event-Driven — 전환 알림** — **When** 불장 모드가 ON/OFF 로 전환되면, **then** Telegram 알림을
  송출한다(예: `"BULL MODE ON: regime=bull, late_cycle=clear, paper"`).
- **(g) 설계 권고** — `bull_mode` 는 **저장 플래그가 아니라 읽기 시점 파생**으로 권고한다:
  `bull_mode = (current_regime=='bull' AND NOT late_cycle_defense_active AND trading_mode=='paper')`.
  저장하는 것은 `late_cycle_defense_active` + stage + `entered_at`(쿨다운용)뿐이다(REQ-036-3 / S-1).

#### Acceptance Criteria — REQ-036-2

- [ ] `regime='bull'` + `late_cycle_defense_active=false` + `trading_mode='paper'` 시 Decision 응답에
      `target_holdings_count ∈ [1,2]`, `cash_target ∈ [10,20]` 가 적용된다.
- [ ] `regime='bull'` + `trading_mode='live'` 시 공격 파라미터가 **적용되지 않고** SPEC-035 conservative
      bull(현금 바닥 20%)로 폴백한다(음성 테스트 — paper-only 가드 보증).
- [ ] `regime='bull'` + `late_cycle_defense_active=true` 시 불장이 자동 OFF 되어 방어 단계 동작으로
      전환된다(S-3 우선순위 — 테스트 시나리오).
- [ ] event-CAR 임계: 동일 후보가 불장 시 |1.0%| 통과, 비불장 시 |1.5%| 통과(parametrize 테스트).
- [ ] 하드 현금 바닥 Python 가드: 불장이라도 현금이 10% 미만이면 신규 buy 가 차단된다(`enforce_cash_floor`
      패턴 재사용).
- [ ] 모드 ON/OFF 전환 Telegram 알림이 송출된다(mock 검증).
- [ ] `decision.jinja`, `risk.jinja` 에 불장 컨텍스트 라인이 존재한다(grep 검증).

**Dependencies**: SPEC-035(regime 캐시·`get_effective_regime`·`regime_branch.py`). REQ-036-3(방어와
**동시 출시 필수** — C-2).

---

### REQ-036-3: 후기 사이클 천장 방어 (Event-Driven + State-Driven)

시스템은 **후기 사이클 위험 신호**를 평일 16:00(post-market)에 평가하고, 임계 초과 시 즉시 방어 모드로
전환한다. 방어는 가용한 신호만으로 평가하며(missing 신호는 트리거되지 않음), robust 신호(KOSPI 일일%,
지수 모멘텀, flows, VIX)가 방어선의 바닥을 지킨다.

- **(a) Ubiquitous — 신호 정의 (SPEC-016 draft 초기값)**:

  | 신호 | 임계 | 단계(level) |
  |---|---|---|
  | 신용잔고 (빚투) | > 35조원 | moderate |
  | 신용잔고 (빚투) | > 40조원 | severe |
  | 투자자예탁금 | > 140조원 | top warning |
  | V-KOSPI | ≥ 30 | immediate de-risk |
  | KOSPI 일일 하락 | ≤ −3% | flash de-risk |

- **(b) Event-Driven — 단계별 강제** — **When** 위 임계 중 하나라도 트리거되면, **then** 시스템은
  `late_cycle_defense_active = true` 로 설정하고 해당 단계를 강제한다(REQ-016-3-2 b 그대로):
  - moderate: 현금 바닥 30%, 신규 진입 1일 최대 1건
  - severe: 현금 바닥 50%, 보유 종목의 **30% 부분 매도(강제 deleverage)**
  - top warning: 현금 바닥 60%, 신규 진입 차단(24h)
  - immediate de-risk: 현금 바닥 30% 즉시, 모든 신규 진입 차단
  - flash de-risk: 손실컷 강화, 다음 cycle 까지 신규 진입 차단
- **(c) graceful 평가** — fetch 불가(`(unavailable)`)한 신호는 **평가에서 제외**(트리거되지 않음).
  방어는 가용 신호로만 동작하며, 신용잔고/예탁금/V-KOSPI 가 전부 unavailable 이어도 KOSPI 일일 −3%
  flash de-risk 는 robust 하게 동작한다(R-2 완화 — robust 신호가 floor).
- **(d) State-Driven — 불장 상호배제** — **While** `late_cycle_defense_active == true` 이면 REQ-036-2
  불장 모드는 자동 비활성된다(S-3).
- **(e) severe 강제 매도 = direct-sell-bypass** — severe 단계의 30% 강제 부분매도는 SPEC-033
  `position_watchdog` 의 **direct `kis_sell` bypass 패턴**(orchestrator halt 게이트·일일 주문수 사전체크
  우회 — 위험 축소 exit 는 buy gate 미통과)을 따른다. `enforce_cash_floor`(buy 차단)와 방향이 반대인
  강제 매도 경로다.
- **(f) Event-Driven — 해제 + 쿨다운** — **When** 임계 신호가 해소되면, **then** 시스템은 **24h
  cooldown** 이후에 `late_cycle_defense_active = false` 로 복귀한다(`late_cycle_entered_at` 기준).
- **(g) Ubiquitous — 로깅 + 알림** — 모든 트리거/해제는 `late_cycle_events` 테이블에 기록되고 Telegram
  으로 알림 송출한다(예: `"⚠️ LATE-CYCLE DEFENSE: 신용잔고=41조, level=severe"`).
- **(h)** 평일 16:00 평가 잡을 `runner.py` 에 추가한다(`daily_report` 16:00 와 충돌 회피 — 별도 잡).

#### Acceptance Criteria — REQ-036-3

- [ ] 평일 16:00 late-cycle 평가 잡이 `runner.py` 에 등록된다(트리거 단위 테스트).
- [ ] mock 시나리오 5종 — 각 신호 임계 초과 시 정확한 단계(moderate/severe/top/immediate/flash)로 진입.
- [ ] severe 진입 시 30% 강제 부분매도가 `position_watchdog` 의 direct-sell-bypass 패턴으로 실행된다
      (mock `kis_sell` 호출 검증, buy gate 미통과 확인).
- [ ] 신용잔고/예탁금/V-KOSPI 가 전부 `(unavailable)` 이고 KOSPI 일일 −4% 일 때 flash de-risk 가
      트리거된다(robust 신호 floor — graceful 평가 보증).
- [ ] 임계 해소 후 24h cooldown 정상 작동(`late_cycle_entered_at` 기준).
- [ ] `late_cycle_events` 테이블 마이그레이션 적용 + 트리거/해제 INSERT 검증.
- [ ] 불장 + late-cycle 동시 발생: 불장 자동 OFF, late-cycle 단계가 effect(S-3 충돌 없음).
- [ ] Telegram 알림 포맷 `"⚠️ LATE-CYCLE DEFENSE: {signal}={value}{unit}, level={...}"` 송출.

**Dependencies**: REQ-036-1(모멘텀 데이터). REQ-036-2(불장 상호배제). SPEC-033(direct-sell-bypass 패턴).

---

## Specifications

### S-1: 마이그레이션 025 — late_cycle_events 테이블 + system_state 방어 컬럼

> raw SQL, 순차(`025_`), 멱등(information_schema 가드 — `023`/`024` 하우스 스타일). `migrate.py` 가
> 자동 발견. 파일 스스로 `schema_migrations` + `audit_log` 에 INSERT. `docker exec trading-app trading
> migrate` 수동 적용(자동 boot 미적용 — lessons).

파일명 예: `src/trading/db/migrations/025_late_cycle_defense.sql`

```sql
-- SPEC-TRADING-036 REQ-036-3: 후기 사이클 천장 방어.
--   late_cycle_events: 모든 트리거/해제 이벤트 로그.
--   system_state: 방어 활성 플래그 + 단계 + 진입 시각(24h 쿨다운용).
-- bull_mode 는 저장하지 않는다(REQ-036-2 g: 읽기 시점 파생).
-- 멱등: information_schema 가드. 재실행 안전.

DO $$
BEGIN
    -- late_cycle_events 테이블
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'late_cycle_events'
    ) THEN
        CREATE TABLE late_cycle_events (
            id          BIGSERIAL PRIMARY KEY,
            event_type  TEXT NOT NULL
                        CHECK (event_type IN ('trigger','clear')),
            signal_name TEXT NOT NULL,        -- 'margin'|'deposits'|'vkospi'|'kospi_daily'
            value       NUMERIC,              -- 관측값 (unavailable 시 NULL)
            unit        TEXT,                 -- '조원'|''|'%' 등
            level       TEXT
                        CHECK (level IS NULL OR level IN
                               ('moderate','severe','top','immediate','flash')),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    END IF;

    -- system_state.late_cycle_defense_active
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'late_cycle_defense_active'
    ) THEN
        ALTER TABLE system_state
            ADD COLUMN late_cycle_defense_active BOOLEAN NOT NULL DEFAULT false;
    END IF;

    -- system_state.late_cycle_level
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'late_cycle_level'
    ) THEN
        ALTER TABLE system_state
            ADD COLUMN late_cycle_level TEXT
                CHECK (late_cycle_level IS NULL OR late_cycle_level IN
                       ('moderate','severe','top','immediate','flash'));
    END IF;

    -- system_state.late_cycle_entered_at (24h 쿨다운 기준)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'system_state' AND column_name = 'late_cycle_entered_at'
    ) THEN
        ALTER TABLE system_state
            ADD COLUMN late_cycle_entered_at TIMESTAMPTZ;
    END IF;
END $$;

COMMENT ON TABLE late_cycle_events IS
    'SPEC-TRADING-036 REQ-036-3(g): 후기 사이클 방어 트리거/해제 이벤트 로그.';
COMMENT ON COLUMN system_state.late_cycle_defense_active IS
    'SPEC-TRADING-036 REQ-036-3: true 면 방어 활성 → 불장 모드 자동 OFF(S-3).';
COMMENT ON COLUMN system_state.late_cycle_entered_at IS
    'SPEC-TRADING-036 REQ-036-3(f): 방어 진입 시각. 해소 후 24h 쿨다운 기준.';

INSERT INTO schema_migrations (version) VALUES ('025_late_cycle_defense')
    ON CONFLICT DO NOTHING;

INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"025_late_cycle_defense"}'::JSONB);
```

> 컬럼/테이블명은 권고안이며 일관성 내에서 조정 가능.

### S-2: macro_context 한국 모멘텀 섹션 형식 (REQ-036-1)

```markdown
## 한국 시장 모멘텀 (as of 2026-05-29 KST)

### 지수
- KOSPI: 7,498.34 (+0.42% / +2.18% over 5d) — 52주 고점 대비 99.2%
- KOSDAQ: 1,210.55 (+0.31% / +1.05% over 5d)

### 수급 (최근 5거래일, 억원)
| 주체 | -5d | -4d | -3d | -2d | -1d | 누적 |
|---|---|---|---|---|---|---|
| 외국인 | -2,310 | -1,840 | +850 | -3,210 | -2,920 | -9,430 |
| 기관 | +1,200 | -350 | -1,420 | +2,180 | +1,950 | +3,560 |
| 개인 | +1,110 | +2,190 | +570 | +1,030 | +970 | +5,870 |

### 변동성 / 레버리지
- VIX: 22.1
- V-KOSPI: (unavailable: KRX OpenAPI 401 — 파생상품지수 서비스 승인 대기)
- 신용융자 잔고: 35.7조원 (ECOS 901Y056 S23E, 2026-04)   ← 또는 (unavailable: stale)
- 투자자예탁금: 124.8조원 (ECOS 901Y056 S23A, 2026-04)
```

> robust 신호(지수·flows·VIX)는 항상 값. ECOS 신용융자/예탁금은 월별 공식 API 값(또는 stale 시
> `(unavailable)`). V-KOSPI 는 KRX OpenAPI 승인 후 자동으로 수치값(승인 전 `(unavailable)`).

### S-3: 불장 모드 vs 후기 사이클 방어 우선순위 (SPEC-016 S-3 재현)

```
priority(late_cycle_defense_active=true) > priority(regime='bull')
```

| `current_regime` | `late_cycle_defense_active` | `trading_mode` | Effective Mode |
|---|---|---|---|
| bull | false | paper | **BULL (REQ-036-2 공격 프로필)** |
| bull | false | live | conservative bull (SPEC-035, 공격 파라미터 OFF — C-7) |
| bull | true | (any) | **LATE-CYCLE DEFENSE (REQ-036-3 단계 적용, 불장 강제 OFF)** |
| neutral | false | (any) | NEUTRAL (current behavior) |
| neutral | true | (any) | LATE-CYCLE DEFENSE |
| bear | false | (any) | BEAR (SPEC-035 conservative bear) |
| bear | true | (any) | LATE-CYCLE DEFENSE (더 보수적인 쪽 채택) |

> `bull_mode` 는 저장 플래그가 아니라 위 표를 **읽기 시점에 파생**(REQ-036-2 g). 저장은
> `late_cycle_defense_active` + `late_cycle_level` + `late_cycle_entered_at` 뿐.

### S-4: 불장 모드 활성 조건 (3-AND 게이트)

```
bull_mode_active  ==  (current_regime == 'bull')
                  AND (late_cycle_defense_active == false)
                  AND (trading_mode == 'paper')
```

세 조건 중 하나라도 거짓이면 공격 프로필은 적용되지 않는다(안전 게이트).

---

## Constraints (구현 제약 — 반드시 준수)

- **C-1 (capital preservation — 3대 안전 게이트)**:
  (1) 공격 파라미터(현금 10~20%, 1~2종 집중, 보유 4~10일, CAR |1.0%|, +10%pt 한도)는 **paper 환경
  한정**(S-4, REQ-036-2 c — Python 하드 가드). (2) 불장 모드는 **방어(REQ-036-3)와 짝으로만 출시**
  (단독 활성 금지 — SPEC-016 C-2). (3) `late_cycle_defense_active==true` 시 불장 **강제 OFF**(S-3).
- **C-2 (CLI 경로 강제)**: 모든 페르소나 호출은 `is_cli_mode_active() → call_persona_via_cli`
  (`base.py:747`/`:555`). bare `call_persona`(`base.py:210`) 금지 — cli_only_mode 에서 유료/크래시.
- **C-3 (마이그레이션)**: raw SQL `025_*.sql`, 순차, 멱등(information_schema 가드, `023`/`024` 본보기),
  `migrate.py` 자동 적용. 재배포 후 **`docker exec trading-app trading migrate` 수동 실행**(자동 boot
  미적용 — lessons). system_state 모델/헬퍼를 그에 맞게 갱신.
- **C-4 (lint)**: ruff 가 BLE001 을 select 하지 **않음** → `# noqa: BLE001` 금지(RUF100 유발). 평범한
  `except Exception:` 사용(graceful fetcher 포함).
- **C-5 (테스트)**: `.venv/bin/python -m pytest`(docker 이미지에 pytest 없음). 베이스라인 **853 passed
  / 6 pre-existing fail**(web_scraper ×1, volatility ×2, tools/registry ×3) — **신규 회귀 0**. 신규 코드
  85%+(TRUST 5).
- **C-6 (방어 임계 보정)**: late-cycle 임계(35조/40조/140조/V-KOSPI 30/−3%)는 **초기값**이며, paper
  trading 1주 후 사용자 승인 하에 보정 가능(SPEC-016 C-6).
- **C-7 (실거래 분리)**: 불장 보유기간(4~10일)·단일 종목 max 상향은 **paper 한정**. 실거래 전환은
  **별도 사용자 승인 + 별도 SPEC**(SPEC-016 C-7).
- **C-8 (브랜치)**: 작업 브랜치는 이미 `fix/SPEC-TRADING-026-overheating-softening` — **신규 브랜치
  생성 금지**, 커밋 금지(오케스트레이터가 배포 처리).
- **C-9 (외부 fetcher graceful — R-2)**: 외부 데이터는 **공식 keyed API**(ECOS·KRX OpenAPI; HTML/OTP
  스크래핑 아님)로 받는다. 모든 외부 fetcher 는 예외/타임아웃/401 시 `(unavailable)` 로 graceful 실패
  하며 빌드/사이클을 **절대 크래시시키지 않는다**.

---

## Deferred / Non-Goals (명시적 비목표)

- **실거래(live) 불장 모드**: 공격 파라미터의 live 적용 — **별도 SPEC**(C-7). 본 SPEC 은 paper 검증까지.
- **adaptive impact-5 매크로 뉴스 트리거** (REQ-016-2-3 b, SPEC-014 연계). 향후 SPEC 으로 defer.
- **A(손절)의 macro regime 연결**: ATR 손절/익절 임계는 별개의 **변동성 레짐**을 유지(SPEC-033, 과매도
  방지).
- **regime 캐시/읽기 헬퍼/Decision·Risk·Portfolio 의 conservative 분기 자체**: SPEC-035 에서 완료. 본
  SPEC 은 그 위에 불장(aggressive)을 추가할 뿐.
- **risk 한도(`limits.py`)·회로차단·halt 게이트 로직 변경 없음** — 최종 hard gate 로 불변.

---

## Risks

| ID | 리스크 | 영향 | 가능성 | 완화 |
|---|---|---|---|---|
| R-2 (계승, **대폭 하향**) | 외부 데이터 소스 취약성 | Low~Medium | Low~Medium | **HTML/OTP 스크래핑 제거** — 3종 모두 공식 keyed API. 신용융자/예탁금 = ECOS(이미 통합, 라이브 검증 35.7조/124.8조), V-KOSPI = KRX OpenAPI(keyed). 잔여 R-2 는 (1) KRX OpenAPI 파생상품지수 **승인 대기**(graceful `(unavailable)` + VIX 가 임시 변동성 프록시), (2) ECOS **월별 시차**(느린 신호라 허용). robust 신호(KOSPI 일일%·flows·VIX)가 방어선 바닥(REQ-036-3 c) |
| R-3 (계승) | 불장 활성 직후 시장이 천장 형성 | Critical | Medium | 방어와 **한 묶음 출시**(C-1), `late_cycle_defense_active` 자동 발동(REQ-036-3), paper 1주 검증 의무 |
| R-7 (계승) | 1~2종 집중이 단일 종목 사고로 큰 손실 | Critical | Medium | paper 한정(C-7), 하드 현금 바닥 가드(REQ-036-2 e), late-cycle 자동 방어 |
| R-M1 | 외부 fetcher 가 사이클/빌드를 크래시 | High | Medium | `except Exception:` graceful, 빌드 abort 금지(C-9). 음성 테스트(AC REQ-036-1) |
| R-M2 | paper-only 가드 누락으로 공격 파라미터가 live 에 적용 | Critical | Low | `trading_mode=='paper'` Python 하드 게이트(REQ-036-2 c, S-4) + 음성 테스트 |
| R-M3 | 불장↔방어 상호배제 race(동시 발생 시 불장이 안 꺼짐) | High | Low | S-3 우선순위 단일 진실. `bull_mode` 읽기 시점 파생(REQ-036-2 g)으로 stale 플래그 제거 |
| R-M4 | severe 강제매도가 buy gate/halt 에 막혀 미실행 | High | Medium | SPEC-033 direct-sell-bypass 패턴 재사용(REQ-036-3 e) + 이중매도 가드 |

---

## Open Questions

- **Q-1 (RESOLVED — endpoint 확정됨)**: 신용융자/예탁금/V-KOSPI 의 데이터 소스는 **경험적으로 확정**.
  신용융자/예탁금 = **ECOS 901Y056 S23E/S23A**(monthly, 이미 통합 어댑터), V-KOSPI = **KRX OpenAPI
  `idx/drvprod_dd_trd`**(파생상품지수 시세정보, keyed). KRX MDC/KOFIA HTML 스크래핑은 **불필요**.
- **Q-2 (run 시 확인 — V-KOSPI row 식별)**: KRX OpenAPI `idx/drvprod_dd_trd` 응답은 **모든 파생상품
  지수**를 나열한다. 승인(이용신청 완료) 후, 응답에서 **변동성지수/VKOSPI row 의 정확한 식별자**
  (지수명/코드 필드값)를 확인해 필터링한다. 승인 전에는 401 → graceful `(unavailable)`.
- **Q-3**: 16:00 평가 잡을 `daily_report`(16:00 정각)와 같은 분에 둘지, 데이터 의존성(06:00 모멘텀이
  당일분이어야 함) 고려해 16:05 로 미세 조정할지? — 구현자가 의존성 확인 후 결정.
- **Q-4**: severe 30% 강제매도의 "30%" 는 보유 종목 **수량의 30%** vs **평가금액의 30%** vs **종목 수의
  30%** 중 무엇인가? — SPEC-016 원문은 "보유 종목의 30% 부분 매도". 권고: 종목별 수량의 30%(SPEC-033
  take-profit 절반 매도와 일관). run 에서 확정.
- **Q-5 (계승 Q-4)**: 임계값을 5년 데이터로 백테스트 보정할 가치 — Phase 3 후속 개선 항목 후보.

---

## Traceability

| 요구 | SPEC-016 원본 | 영향 파일 | 테스트(신규) |
|---|---|---|---|
| REQ-036-1 | REQ-016-2-4 | `data/ecos_adapter.py`(901Y056 S23E/S23A 확장), `data/krx_openapi.py`(신규, 파생상품지수), `contexts/build_macro_context.py`(섹션 추가), `data/pykrx_adapter.py`/`yfinance_adapter.py`(재사용) | `tests/data/test_krx_openapi.py`, `tests/data/test_ecos_market_funds.py` |
| REQ-036-2 | REQ-016-3-1 | `personas/regime_branch.py`(aggressive 프로필 확장), `personas/decision.py`, `personas/risk.py`, `prompts/decision.jinja`, `prompts/risk.jinja`, Telegram notifier(BULL ON/OFF), paper-only Python 가드 | `tests/personas/test_bull_mode.py` |
| REQ-036-3 | REQ-016-3-2 | `db/migrations/025_*.sql`(신규), `risk/late_cycle.py`(신규), `scheduler/runner.py`(16:00 잡), `watchers/position_watchdog.py`(direct-sell-bypass 재사용), `db/session.py`(방어 플래그 읽기/쓰기), Telegram notifier | `tests/risk/test_late_cycle.py`, `tests/scheduler/test_late_cycle_job.py` |

| 외부 의존 | 설명 |
|---|---|
| SPEC-TRADING-016 | Phase 3 원본(REQ-016-3-1/3-2), 데이터 의존(REQ-016-2-4), S-3 우선순위표, C-2/C-6/C-7, R-3/R-7 |
| SPEC-TRADING-035 | regime 캐시·`get_effective_regime`·`regime_branch.py`(불장=035 가 defer 한 aggressive 프로필) |
| SPEC-TRADING-033 | `position_watchdog` direct-sell-bypass 패턴(severe 강제 deleverage 재사용) |
| SPEC-TRADING-012 | decision.jinja dynamic_thresholds — 컨텍스트 주입 선례 |
| SPEC-TRADING-029 | balance()/cash_pct·holdings — 현금 타깃/강제매도 컨텍스트 |
| SPEC-TRADING-014 | 뉴스 분류기 impact-5 — adaptive 트리거(비목표 defer 근거) |
