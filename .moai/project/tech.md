# Technology Stack

## 결정 원칙

1. **검증된 안정 버전만** — beta/alpha/nightly 금지
2. **인접 프로젝트(~/n8n/)와 일관** — 운영 패턴 일치
3. **Linux/Mac 호환** — Windows 전용 도구 배제
4. **자체 통제 가능한 의존성** — 변동성 큰 SDK 회피, 핵심 로직 직접 구현
5. **시크릿/외부 호출 최소화** — 무료 공식 API 우선, 필요 시 키 1회 발급

## 런타임

| 항목 | 버전 | 비고 |
|---|---|---|
| OS (호스트) | Ubuntu 25 (Linux 6.17) | 박세훈 환경 |
| Docker | 28.3.2 | 설치 완료 |
| Docker Compose | v2.38.2 | 설치 완료 |
| Python | 3.13-slim (Docker) | 최신 안정 |
| 패키지 매니저 | uv (Astral) | pip 대비 10~100배 빠름 |

## 데이터 계층

| 항목 | 버전 | 용도 |
|---|---|---|
| PostgreSQL | 16-alpine | 매매·시그널·시세·페르소나 응답 영구 저장 |
| 마이그레이션 | SQL 파일 직접 관리 | 단순성 우선, alembic 미도입 |

## 핵심 라이브러리

| 패키지 | 용도 |
|---|---|
| `httpx` | KIS REST + Anthropic SDK base + 외부 API 호출 |
| `pydantic` v2 | 설정·DTO 검증 |
| `pydantic-settings` | .env 로딩 |
| `psycopg[binary]` v3 | Postgres 드라이버 |
| `SQLAlchemy` v2 | ORM (선택) — 단순 쿼리는 raw SQL |
| `pykrx` | 한국 주식 일봉/시세/외국인-기관 매매 |
| `yfinance` | 글로벌 자산 (S&P500, VIX, 환율) |
| `fredapi` | FRED API 래퍼 (선택, 직접 httpx도 가능) |
| `OpenDartReader` 또는 직접 구현 | DART 공시 |
| `pandas`, `numpy` | 데이터프레임/수치 |
| `vectorbt` | 백테스트 엔진 (M3) |
| `anthropic` | Claude API SDK (M4+) |
| `jinja2` | 페르소나 시스템 프롬프트 템플릿 |
| `python-telegram-bot` | 봇 listener (M5+) |
| `apscheduler` | 컨테이너 내 스케줄러 |
| `structlog` | 구조화 로깅 |
| `tenacity` | 재시도 (KIS 1분 토큰 제한 대응) |

## 외부 API (직접 통합)

### 한국투자증권 KIS Developers

직접 REST 호출. SDK 미사용.

| 항목 | 값 |
|---|---|
| Paper base URL | `https://openapivts.koreainvestment.com:29443` |
| Live base URL | `https://openapi.koreainvestment.com:9443` |
| 인증 | OAuth2 client_credentials → access_token (24h, 1분 재발급 제한) |
| 핵심 엔드포인트 | `/oauth2/tokenP`, `/uapi/domestic-stock/v1/...` |
| Rate limit | 초당 20건 (paper), 초당 5건 (live) |
| tr_id 접두 | paper=`V`, live=`T` |

토큰 캐싱 필수. `~/trading/data/.kis_token_cache.json` 또는 DB.

### Telegram Bot API

| 항목 | 값 |
|---|---|
| Base URL | `https://api.telegram.org/bot{TOKEN}/` |
| Bot | @sehoon_trd_bot (생성·검증 완료) |
| chat_id | 60443392 (박세훈 본인) |
| 핵심 메서드 | `getMe`, `sendMessage`, `getUpdates`, `setMyCommands` |

### Anthropic API (M4+)

| 항목 | 값 |
|---|---|
| 매크로 페르소나 | `claude-opus-4-7` (1M context) |
| 마이크로/결정/리스크/포트폴리오/회고/일일리포트 | `claude-sonnet-4-6` |
| 매매 의사결정 | **AI에게 위임 — 단, 코드 룰 한도와 리스크 페르소나 검증 통과 후** |
| 프롬프트 캐싱 | 적용 (시스템 프롬프트, 도메인 컨텍스트, 유니버스 데이터) |

비용 (예상, 2026-05 기준 USD → KRW):
- Sonnet 4.6: input $3/M tok, output $15/M tok → 1회 ~1,000원
- Opus 4.7: input $15/M tok, output $75/M tok → 1회 ~5,000원

월 비용 (장중 매매 4회 + 이벤트 트리거 포함):
- M4 단계 (4-페르소나): **~30~40만원**
- M5+ 단계 (5-페르소나): **~40~50만원**

자본 1,000만원 대비 3~5%. 자본 1억+ 증액 시 0.3~0.5%로 적정.

### FRED API (Federal Reserve)

| 항목 | 값 |
|---|---|
| Base URL | `https://api.stlouisfed.org/fred/` |
| 인증 | API key (무료 발급, 즉시) |
| 데이터 | Fed funds rate, GDP, CPI, PPI, 실업률, 10Y/2Y 국채 수익률 |
| 캐싱 | 일 1회 갱신 충분 |

### ECOS API (한국은행)

| 항목 | 값 |
|---|---|
| Base URL | `https://ecos.bok.or.kr/api/StatisticSearch/...` |
| 인증 | API key (무료 발급, 가입 1일 이내) |
| 데이터 | 한국 기준금리, GDP, CPI, 환율, 통화량, 산업생산 |
| 캐싱 | 일 1회 |

### OpenDART (전자공시)

| 항목 | 값 |
|---|---|
| Base URL | `https://opendart.fss.or.kr/api/...` |
| 인증 | API key (무료, 즉시) |
| 데이터 | 공시 목록·내용, 사업보고서, 재무제표 |
| 캐싱 | 영업일 마감 후 갱신 |

### pykrx (라이브러리)

외부 호출 X, 공개 거래소 데이터를 라이브러리로 제공. API 키 불필요.
- 일봉, 분봉, 시가총액
- 외국인/기관/개인 매매 동향
- 종목 기본정보, 재무

### yfinance (Yahoo Finance)

비공식 라이브러리. 글로벌 자산 시세에 사용.
- S&P500, Nasdaq, 미국 섹터 ETF
- VIX 지수
- USD/KRW 환율
- 금/원자재 ETF

## 시크릿 (.env)

```
# 모드
TRADING_MODE=paper

# KIS 실전
KIS_LIVE_APP_KEY=...
KIS_LIVE_APP_SECRET=...
KIS_LIVE_ACCOUNT=12345678-01

# KIS 모의
KIS_PAPER_APP_KEY=...
KIS_PAPER_APP_SECRET=...
KIS_PAPER_ACCOUNT=50123456-01

# Telegram
TELEGRAM_BOT_TOKEN_TRADING=...
TELEGRAM_CHAT_ID=60443392

# Anthropic (M4+)
ANTHROPIC_API_KEY=...

# 외부 데이터 API (M3+)
FRED_API_KEY=...
ECOS_API_KEY=...
DART_API_KEY=...

# Postgres
POSTGRES_USER=trading
POSTGRES_PASSWORD=<랜덤 32바이트 hex>
POSTGRES_DB=trading
```

권한 600. git 제외. 백업 대상.

## 위험 한도 기본값 (config.py 상수)

| 한도 | 기본값 | 단위 | 시행 주체 |
|---|---|---|---|
| 일일 최대 손실 | -1.0 | 자본 % | 코드 룰 + 리스크 페르소나 |
| 종목당 최대 포지션 | 20.0 | 자본 % | 코드 룰 + 리스크 페르소나 |
| 전체 투자 비중 | 80.0 | 자본 % (현금 20% 유지) | 코드 룰 + 리스크 페르소나 |
| 단일 주문 최대 금액 | 10.0 | 자본 % | 코드 룰 |
| 일일 주문 횟수 | 10 | 건 | 코드 룰 |

리스크 페르소나는 위 코드 한도와 별개로 *의미 단위* 검토 (섹터 편중, 상관관계, 페르소나 응답 모순). M6 진입 시 일일 손실 -0.5%로 강화.

## 페르소나별 모델/주기/입출력 요약

장중 매매 기반.

| 페르소나 | 모델 | 주기 | 주요 입력 | 출력 |
|---|---|---|---|---|
| Macro | Opus 4.7 | 주간 (금 17:00) | FRED+ECOS 거시지표, 글로벌 자산, KOSPI 흐름, 정책 일정 | 시장 모멘텀 + 위험선호도 + 워치리스트 |
| Micro | Sonnet 4.6 | 영업일 Pre-market 07:30 (풀 분석) + 장중 캐시 갱신 | 시세·공시·재무·기술적·수급 (KOSPI200 + KOSDAQ150 + 워치리스트) | 매수/매도/관망 후보 + 근거 |
| Decision (박세훈) | Sonnet 4.6 | 영업일 07:50 + 장중 정기 4회 (09:30/11:00/13:30/14:30) + 이벤트 트리거 | Macro 캐시 + Micro 출력 + 현재 시세 + 트리거 컨텍스트 | 매매 시그널 (종목/방향/수량) |
| Risk | Sonnet 4.6 | 매매 시그널 발생 시 | 시그널 + 포지션 + 한도 | APPROVE/HOLD/REJECT |
| Portfolio (M5+) | Sonnet 4.6 | Decision 직후 (보유 5종 이상) | 시그널 + 포트폴리오 | 사이즈 조정된 시그널 |
| Retrospective (M5+) | Sonnet 4.6 | 주간 (일요일) | 지난주 매매 + 페르소나 응답 | 회고 리포트 + 시스템 개선안 |
| Daily Report | Sonnet 4.6 | 영업일 16:00 | 당일 매매 + 페르소나 응답 + 포지션 | 일일 리포트 (텔레그램) |

## 도구 체인

| 항목 | 도구 |
|---|---|
| 린팅·포매팅 | ruff (lint + format 통합) |
| 타입 체크 | mypy strict |
| 테스트 | pytest, pytest-asyncio |
| 커버리지 | coverage.py 85% 목표 |
| 사전훅 | pre-commit (ruff + mypy + 시크릿 스캔) |
| 시크릿 스캔 | gitleaks |

## 미적용/미고려 (ADR 형태로 명시적 거부)

| 항목 | 이유 |
|---|---|
| Kafka·Redis | 단일 사용자 |
| Celery | apscheduler로 충분 |
| FastAPI 웹 UI | 로컬 단일 사용자, CLI + 텔레그램으로 충분 |
| GraphQL | 외부 API 없음 |
| Kubernetes | 단일 호스트, compose로 충분 |
| 키움/LS xingAPI | Windows 전용 |
| LangChain/LangGraph | 페르소나 5개 단순 직렬, 직접 구현이 더 명확 |
| RAG | 시스템 프롬프트 + 데이터 어댑터로 충분 |
| OpenAI/기타 LLM | 단일 벤더(Anthropic) 일관성, AI 거버넌스 단순화 |

## 의존성 lockfile

`uv.lock` 커밋. 모든 환경 동일 버전 보장. 업그레이드는 의식적(`uv sync --upgrade`).

## 운영 명령어 표준

```bash
# 빌드/기동
docker compose up -d
docker compose ps

# 헬스체크
docker compose exec app python -m trading.healthcheck

# CLI
docker compose exec app trading <subcommand>
  # check-kis, paper-buy, run-personas, run-strategy, daily-report ...

# 페르소나 수동 호출 (테스트)
docker compose exec app trading run-personas --persona macro
docker compose exec app trading run-personas --persona micro --date today

# 백업
./backup.sh                 # 기본 retention 30
BACKUP_KEEP=7 ./backup.sh

# 로그
docker compose logs -f app
docker compose logs -f postgres

# DB 접근
docker compose exec postgres psql -U trading -d trading
```
