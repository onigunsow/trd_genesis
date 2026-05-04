# Plan — SPEC-TRADING-001 (v0.2.0)

상위 SPEC: `spec.md` (v0.2.0). 본 문서는 M1~M5 마일스톤의 구현 순서, 파일 단위 분해, 의존성, 위험 분석, 완화 전략을 정의한다. 시간 단위 추정은 사용하지 않으며 우선순위/마일스톤 기반으로만 표현한다. **M6 실거래는 본 SPEC 범위 외**이며 본 plan에 작업 항목으로 포함되지 않는다 (안전장치 `live_unlocked=false` 만 M1/M2에 포함).

v0.2.0 갱신: M5 정밀화 후행 반영 + 자본 손실 직결 결함 보강 + 비용 모델 통합 + Robustness 6대 원칙 cross-cutting 분산 + 별도 SPEC 5개 후보 명시.

## 기술 스택 고정

본 plan은 `tech.md`를 단일 출처로 참조한다. 핵심 라이브러리 버전은 M1 첫 `uv sync` 시점에 lockfile (`uv.lock`)로 고정한다. 본 plan에 버전을 중복 기재하지 않는다. 추가 의존성: `holidays` (KRX 캘린더), `tenacity` (retry/backoff), pykrx ≥ 1.2.8 (KRX 로그인 지원).

## 마일스톤 의존성

```
M1 Infra ──► M2 KIS ──► M3 Data/Backtest ──► M4 Personas/Briefing/Auto-Trading ──► M5 Risk/Cost/Calendar/Observability/3주 모의
                                                                                           │
                                                                                           └──► (본 SPEC 종료, 별도 SPEC 5개 후보로 분할)
```

각 마일스톤은 직전 마일스톤이 acceptance.md의 해당 시나리오를 통과해야 진입.

## 우선순위 정의

- **Primary Goal**: 마일스톤 핵심 기능
- **Secondary Goal**: 운영 안정성·관측성
- **Optional**: 비용·UX 개선

---

## M1 — Infrastructure & Security & Quality Gates

### Primary Goal

- `~/trading/compose.yaml` 작성 — `app` (python:3.13-slim + uv), `postgres:16-alpine`. 프로젝트명 `trading`, 네트워크 `trading-net`, 외부 포트 노출 없음
- `Dockerfile` — `USER 1000:1000`, uv 기반 의존성 설치
- `pyproject.toml` 초안 (의존성 최소: pydantic, pydantic-settings, httpx, structlog, psycopg, holidays, tenacity)
- `.env.example` 작성 — `tech.md` 시크릿 표 1:1 매핑 (KIS_*, KRX_*, ANTHROPIC_API_KEY, TELEGRAM_*)
- `.env` 생성 + `chmod 600` + `.gitignore` 등록
- `backup.sh` — `~/n8n/backup.sh` 패턴 1:1 (timestamp, pg_dump.gz, tar.gz, retention 30, `BACKUP_KEEP` env)
- `src/trading/config.py` — pydantic-settings로 .env 로드 + 위험한도 5종 상수 + **비용 모델 상수 5종** (REQ-COST-05-13) + `TRADING_MODE` 분기
- `src/trading/healthcheck.py` — DB 연결, .env 로드, 권한 600 검증
- DB 첫 마이그레이션: `system_state(live_unlocked=false, halt_state=false, silent_mode=false, trading_mode_active='paper')`

### Secondary Goal (보강 v0.2.0)

- `.pre-commit-config.yaml` — **gitleaks 훅 강제** (REQ-INFRA-01-9). `KIS_*`, `ANTHROPIC_API_KEY`, `KRX_*`, `TELEGRAM_*` 패턴 차단
- CI 파이프라인 — ruff + mypy strict + pytest with coverage gate (≥85%, 한도/회로차단/트랜잭션 모듈 100%) (REQ-INFRA-01-10)
- 운영 문서: 시크릿 검증은 `[ -n "$VAR" ] && echo "VAR: present"` 마스킹 패턴 강제, `.env` 평문 출력 절대 금지 (REQ-INFRA-01-11)
- README.md 운영 메모 (compose 기동/중지, healthcheck, backup, restore 명령)

### 의존성

- 없음 (`~/n8n/` 패턴 차용)

### 산출 acceptance

acceptance.md M1 시나리오 + M1 보강 시나리오 통과

---

## M2 — KIS API Integration & Order Audit & Trade Safety

### Primary Goal

- `src/trading/kis/auth.py` — OAuth2 토큰 발급 + 캐시 (1분 재발급 제한 대응)
- `src/trading/kis/client.py` — paper/live 분기 (`TRADING_MODE` + `live_unlocked` 이중 체크), tr_id prefix 분기 (V/T), **tenacity retry max 4 + exponential backoff + circuit breaker** (REQ-KIS-02-9), rate limit 준수 (paper 20 req/s)
- `src/trading/kis/market.py` — 현재가, 일봉, **종목 정보 조회 (거래정지/관리/투자위험/상하한가 사전 검증, REQ-KIS-02-12)**
- `src/trading/kis/account.py` — 잔고, 매수가능금액 (`nrcvb_buy_amt` 차감 반영, REQ-KIS-02-11)
- `src/trading/kis/order.py` — 매수/매도/정정/취소
- `src/trading/db/transactions.py` — **단일 트랜잭션 보장** (orders + audit_log + positions, REQ-KIS-02-10) + advisory lock
- DB 마이그레이션 v1: `orders` (kis_order_no UNIQUE 제약, REQ-KIS-02-13), `positions`, `audit_log`
- `src/trading/scripts/check_kis.py` — paper 인증·잔고·시세 단발 검증
- `src/trading/scripts/paper_buy_one.py` — 1주 매수 + DB 영속 + Telegram 브리핑
- `src/trading/alerts/telegram.py` 기본 구현 (sendMessage 단방향)

### 핵심 안전장치

- `TRADING_MODE=live` AND `live_unlocked=false` 시 모든 주문 진입점에서 `LiveLockedError` 즉시 raise + audit_log 기록
- `TRADING_MODE` 변경 시 audit_log 자동 기록
- KIS rt_cd=1 / EGW00201 시 retry, 5xx 시 fail-fast + 텔레그램 알림
- 거래정지/관리/투자위험/상하한가 종목 사전 차단

### Secondary Goal

- `audit_log`에 모든 KIS 호출 (성공/실패/거부) 기록
- 토큰 만료 ≤ 1시간 시 사전 갱신 시도 (단, 1분 재발급 제한 준수)

### 산출 acceptance

acceptance.md M2 시나리오 + M2 보강 시나리오 통과 (retry, transaction atomicity, idempotency, halted-stock block)

---

## M3 — Market Data & Benchmark Backtesting

### Primary Goal

- `src/trading/data/pykrx_adapter.py` — pykrx ≥ 1.2.8. **KRX_ID/KRX_PW 로그인 지원** (REQ-DATA-03-8). 로그인 실패 시 OHLCV는 정상, fundamentals/flows는 graceful skip
- `src/trading/data/yfinance_adapter.py`, `fred_adapter.py`, `ecos_adapter.py`, `dart_adapter.py`, `news_adapter.py` — 통일된 `fetch(symbol, start, end)` 인터페이스
- `src/trading/data/cache.py` — Postgres 기반 OHLCV/거시지표/공시 캐시. upsert idempotent.
- DB 마이그레이션 v2: `ohlcv`, `macro_indicators`, `disclosures`, **`fundamentals`, `flows`** (REQ-DATA-03-9)
- 2019-01-01부터 KOSPI200 + KOSDAQ150 + 워치리스트 OHLCV 백필 스크립트
- 워치리스트 종목별 일일 PER/PBR/EPS/BPS/DIV/DPS/시가총액 + 외국인/기관/개인 매매 수치 캐시 (REQ-DATA-03-9)
- FRED 5종 (RRPONTSYD, BAMLH0A0HYM2, DCOILWTICO, STLFSI4, DTWEXBGS) 추가 캐싱 (REQ-DATA-03-10) → Macro persona input
- `src/trading/strategies/sma_cross.py`, `dual_momentum.py` — 룰 기반 벤치마크
- `src/trading/backtest/engine.py` — vectorbt 기반. **비용 상수는 config.py 단일 출처 사용** (REQ-COST-05-17). CAGR/MDD/Sharpe + 거래 원장 산출
- `notebooks/` 백테스트 실험 환경

### Secondary Goal

- 어댑터별 단위 테스트 + 캐시 idempotency 테스트
- 백테스트 결과를 DB `benchmark_runs` 테이블에 기록

### 핵심 의도

- 백테스트는 페르소나 시스템 검증 도구가 아니라 **forward 모의 매매와 비교할 룰 기반 벤치마크**
- backtest와 live 비용 모델 일치 → 공정 비교

### 산출 acceptance

acceptance.md M3 시나리오 + M3 보강 시나리오 통과

---

## M4 — 5-Persona Intraday System & Telegram Briefing & Paper Auto-Trading

### Primary Goal

- `src/trading/personas/base.py` — Persona 추상 클래스 (build_prompt, invoke, persist_run)
- `src/trading/personas/prompts/*.jinja` — 6개 페르소나 시스템 프롬프트 템플릿
- **`decision.jinja` 정밀화** (REQ-PERSONA-04-11): 박세훈 본인 트레이딩 원칙 + 7-rule (현금 30~50%, 익절 RSI>85, 손절 -7%, 섹터 40%, 종목 3~7개, 가치트랩 회피, 공매도 금지) + 매매 비용 인지 + 매매 빈도 가이드
- **`decision.jinja`/`risk.jinja` Cost Awareness** (REQ-PERSONA-04-12): 매매 비용 명시 (매수 0.015% + 매도 0.345% KOSPI / 0.195% KOSDAQ). 익절 룰은 "+1% 평가익 (수수료 차감 후 +0.5% 순익)"
- `macro.py` (Opus 4.7), `micro.py`, `decision.py`, `risk.py`, `portfolio.py` (skeleton, M5+ 활성), `retrospective.py` (skeleton, M5+ 활성)
- Micro persona input에 fundamentals/flows 자동 주입 (REQ-DATA-03-9)
- Macro persona input에 FRED 5종 통합 (REQ-DATA-03-10)
- `personas/orchestrator.py` — Pre-market / 장중 / 이벤트 트리거 시퀀스 관리
- `personas/audit.py` — `persona_runs`, `persona_decisions`, `risk_reviews` 영속화
- DB 마이그레이션 v3: `persona_runs`, `persona_decisions`, `risk_reviews`
- `scheduler/daily.py`, `scheduler/weekly.py`, `scheduler/events.py`
- `alerts/telegram.py` 확장 — 페르소나/매매/트리거 브리핑 형식 표준화
- `risk/limits.py` — 한도 5종 코드 룰 + 주문 진입점 사전 검증

### 핵심 의도

- **모든 페르소나 응답 = persona_runs DB 영속 + Telegram 브리핑 5초 이내**
- **모든 매매 체결 = orders DB 영속 + Telegram 브리핑 (자산현황 갱신 포함) 5초 이내**
- Risk APPROVE + 코드 룰 모두 통과해야만 매매 (REQ-RISK-04-7 SoD)

### Secondary Goal

- 모의 자동 매매 — paper mode 자동, live mode는 차단된 채로 끝남
- 페르소나 호출 비용 추적

### 산출 acceptance

acceptance.md M4 시나리오 + M4 보강 시나리오 통과

---

## M5 — Risk, Cost, Calendar & Observability & 3-Week Paper Operation

### Primary Goal

- `risk/circuit_breaker.py` — 일일 손실 -1.0% 도달 시 `halt_state=true`
- `risk/emergency.py` — `/halt`, `/resume` 핸들러
- `bot/telegram_bot.py` — getUpdates polling, chat_id 60443392 화이트리스트, `/halt /resume /verbose /silent` 명령 처리
- `personas/portfolio.py` 활성화 — 보유 5종 이상 시
- `personas/retrospective.py` 활성화 — 일요일 회고
- `reports/daily_report.py` — 16:00 일일 리포트 (Sonnet 4.6)
- DB 마이그레이션 v4: `portfolio_adjustments`, `retrospectives`, `circuit_breaker_state`, `daily_reports`
- 알림 피로 침묵 모드

### v0.2.0 신규 작업 — Cost Model 통합

- `config.py`에 비용 상수 5종 (REQ-COST-05-13): `PAPER_FEE_BUY=0`, `LIVE_FEE_BUY=0.00015`, `LIVE_FEE_SELL_KOSPI=0.00345`, `LIVE_FEE_SELL_KOSDAQ=0.00195`, `SLIPPAGE_BPS=0.0005`
- `estimate_fee(mode, side, market, notional)` 함수 — 단일 출처
- `risk/limits.py` cost-aware 갱신 (REQ-COST-05-14): 한도 5종 모두 `notional + estimated_fee`로 평가
- `kis/order.py` → `orders.fee` 컬럼 자동 채움 (REQ-COST-05-15). KIS 체결 응답의 실제 수수료 필드 우선
- `reports/daily_report.py`에 누적 매매 수수료 + 거래세 + 슬리피지 추정 라인 추가 (REQ-COST-05-16)
- `backtest/engine.py` 비용 상수 동기화 (REQ-COST-05-17)

### v0.2.0 신규 작업 — Calendar

- `scheduler/calendar.py` (REQ-CAL-05-18): `holidays.KR()` + 주말 + 12/31 KRX 폐장 체크. 휴장일 페르소나 호출 0건. audit_log skip 기록

### v0.2.0 신규 작업 — Observability 보강

- `reports/daily_report.py`에 일/주/월 누적 손익 자동 계산 (REQ-OPS-05-19). KIS balance mark-to-market
- `alerts/system_error.py` (REQ-OPS-05-20): Anthropic/KIS/Postgres/Telegram 호출 실패 시 audit_log + 텔레그램 즉시 알림. silent_mode에서도 시스템 에러는 발송
- `backup.sh` 보강 (REQ-OPS-05-21): 실행 직후 `pg_dump --schema-only` 무결성 검증. 실패 시 텔레그램 경보

### 3주 모의 운영

- M5 기능 완성 후 영업일 ~15일 무중단 가동
- 종료 시점 평가 리포트 자동 생성

### 백업 복원 리허설

- M5 중 1회: backup.sh 산출물 → 새 docker compose 환경 restore → 데이터 무결성 검증

### 산출 acceptance

acceptance.md M5 시나리오 + M5 신규 시나리오 통과 + 3주 평가 리포트 생성

---

## 위험 분석 및 완화 (v0.2.0 강화)

본 SPEC v0.2.0은 자본 손실 직결 결함 5종 + 운영 결함 4종 + 위생 결함 3종, 총 12개 결함을 식별하고 모듈별로 mitigation을 분산 배치한다.

### 자본 손실 직결 결함 (5종)

| # | 결함 | 영향 | mitigation REQ |
|---|---|---|---|
| 1 | 매매 비용 미반영으로 손익 오판 | 매우 고 | REQ-COST-05-13~17 (cost-aware limits, fee 캡처, 일일 리포트, backtest 일치) |
| 2 | 동시 다발 주문이 매수가능금액 초과 | 매우 고 | REQ-KIS-02-10 (단일 트랜잭션 + advisory lock), REQ-KIS-02-11 (`nrcvb_buy_amt` 차감) |
| 3 | 거래정지·관리·투자위험·상하한가 종목 매매 | 매우 고 | REQ-KIS-02-12 (사전 차단) |
| 4 | 같은 시그널 두 번 INSERT로 이중 매매 | 매우 고 | REQ-KIS-02-13 (UNIQUE 제약), Robustness-6 |
| 5 | KIS 5xx silent fail 후 모의로 인지 못함 | 고 | REQ-KIS-02-9 (retry + circuit breaker), REQ-OPS-05-20 (즉시 알림) |

### 운영 결함 (4종)

| # | 결함 | 영향 | mitigation REQ |
|---|---|---|---|
| 6 | 휴장일 페르소나 호출로 토큰 낭비 | 중 | REQ-CAL-05-18 (`holidays.KR()` + 주말 + 12/31) |
| 7 | 누적 손익 추적 부재 | 중 | REQ-OPS-05-19 (daily/weekly/monthly mark-to-market) |
| 8 | 시스템 에러 silent fail | 고 | REQ-OPS-05-20 (silent_mode에서도 발송) |
| 9 | 백업이 실제로 복원되지 않음 | 매우 고 | REQ-OPS-05-21 (`pg_dump --schema-only` 무결성 검증), M5 복원 리허설 |

### 위생 결함 (3종)

| # | 결함 | 영향 | mitigation REQ |
|---|---|---|---|
| 10 | 시크릿 git 커밋 노출 | 매우 고 | REQ-INFRA-01-9 (gitleaks pre-commit) |
| 11 | 시크릿 평문 출력 운영 사고 | 고 | REQ-INFRA-01-11 (마스킹 패턴 강제) |
| 12 | 한도/회로차단 모듈 테스트 부족 | 고 | REQ-INFRA-01-10 (한도/회로차단/트랜잭션 100% 커버리지) |

### 기존 위험 (v0.1.0 유지)

| 위험 | 발생 가능성 | 영향 | 완화 |
|---|---|---|---|
| 3주 모의 표본 부족 (영업일 ~15일) | 확정 | 중 | 정성 지표 우선. M6 정량 임계는 미래 SPEC으로 이연 |
| KIS API 토큰 1분 재발급 제한 위반 | 중 | 중 | `kis/auth.py` 토큰 캐시 + tenacity 재시도 |
| LLM 환각 (잘못된 종목/수량/방향) | 중 | 고 | Risk SoD + 코드 룰 5종 + persona_runs 감사 + 거래정지 종목 사전 차단 |
| 페르소나 응답 일관성 부족 | 확정 | 중 | 모든 응답 영구 기록 → Retrospective 주간 분석 |
| LLM 비용 ~30~50만원/월 | 확정 | 중 | persona_runs.cost_krw 추적 + 휴장일 호출 0건 (REQ-CAL-05-18) |
| Telegram 알림 피로 | 확정 | 중 | M5 silent mode. 매매·트리거·회로차단·시스템 에러는 침묵 모드에서도 발송 |
| 코드 버그가 자본을 잠식 | 저 | 매우 고 | 회로차단 + Risk SoD + paper 우선 + `live_unlocked` + 한도 5종 + 트랜잭션 |
| 페르소나 드리프트 | 중 | 중 | persona_runs prompt 버전 + Retrospective + 시스템 프롬프트 변경 audit_log |
| Anthropic API 장애 | 저 | 중 | tenacity 재시도 + 1회 fallback 후 cycle skip + 즉시 알림 |
| **pykrx 외부 의존성 파손** (KRX 페이지 변경) | **중** | **중** | **REQ-DATA-03-8 graceful degradation: OHLCV 정상, fundamentals/flows skip + 경고. pykrx 버전 핀 + 정기 헬스 체크** |

### Lessons Learned (2026-05-04)

- **시크릿 평문 출력 사고**: 운영자가 `.env` 검증 시 KRX_PW를 평문으로 출력한 사례 발생. 이후 모든 시크릿 검증은 `[ -n "$VAR" ] && echo "VAR: present"` 마스킹 패턴 강제 (REQ-INFRA-01-11). gitleaks pre-commit과 함께 이중 방어
- 인접 시스템 `~/n8n/` 패턴 차용 시 시크릿 처리 절차도 함께 가져온다

## 인접 시스템 참고

- `~/n8n/compose.yaml`, `~/n8n/backup.sh` 패턴을 1:1 차용 (네트워크 분리, retention 정책, healthcheck)
- `~/n8n/`은 외부 자동화 서비스로 운용 중. `~/trading/`은 별도 docker compose project로 분리

## 품질 게이트 (마일스톤별)

- 테스트 커버리지 ≥ 85% (TRUST 5)
- **한도 / 회로차단 / 트랜잭션 모듈은 100% 커버리지** (REQ-INFRA-01-10)
- ruff + mypy strict clean
- pre-commit (**gitleaks 포함**) clean
- 모든 외부 호출은 mock 가능한 어댑터 경계로 분리
- 모든 시크릿 노출 경로 (logs, persona prompts, error messages) 검사

## 다음 단계

1. M1 acceptance 시나리오 통과 → M2 진입
2. 각 마일스톤 종료 시점에 acceptance.md 시나리오 전수 통과 확인
3. M5 종료 후 3주 평가 리포트 작성 → 별도 SPEC 5개 후보 (TRADING-002~006) 분할 작성 트리거
