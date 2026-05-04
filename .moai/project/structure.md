# Architecture & Structure

## 디렉토리 구조

```
~/trading/
├── compose.yaml              # Docker compose: app + postgres
├── Dockerfile                # Python 3.13-slim 기반
├── pyproject.toml            # uv 의존성 관리
├── .env                      # 시크릿 (perm 600, git 제외)
├── .env.example              # 템플릿
├── .gitignore
├── README.md                 # 운영 메모, 명령어 치트시트
├── backup.sh                 # Postgres + .env + 코드 백업 (n8n 동일 패턴)
├── .moai/                    # SPEC 및 프로젝트 문서
│   ├── project/              # product.md, structure.md, tech.md
│   └── specs/                # SPEC-TRADING-001/ ...
├── src/
│   └── trading/
│       ├── __init__.py
│       ├── config.py         # .env 로딩 + 위험한도 상수 + TRADING_MODE
│       ├── healthcheck.py    # M1 헬스체크
│       ├── kis/              # M2: 한국투자증권 API 직접 구현
│       │   ├── auth.py       # OAuth 토큰 발급·캐싱 (1분 재발급 제한 대응)
│       │   ├── client.py     # REST 클라이언트, paper/live 분기
│       │   ├── market.py     # 시세 (현재가, 일봉)
│       │   ├── account.py    # 잔고, 매수가능금액
│       │   └── order.py      # 매수·매도·정정·취소
│       ├── db/               # 마이그레이션·세션
│       │   ├── models.py
│       │   ├── migrations/   # SQL 파일
│       │   └── session.py
│       ├── data/             # M3: 외부 데이터 소스 어댑터
│       │   ├── pykrx_adapter.py     # 한국 주식 일봉/외국인-기관 매매
│       │   ├── yfinance_adapter.py  # 글로벌 자산 (S&P500, VIX, USD/KRW)
│       │   ├── fred_adapter.py      # Fed 데이터 (금리, GDP, CPI)
│       │   ├── ecos_adapter.py      # 한국은행 ECOS (한국 거시 지표)
│       │   ├── dart_adapter.py      # OpenDART 공시
│       │   ├── news_adapter.py      # 종목 뉴스 (네이버 금융 등)
│       │   └── cache.py             # Postgres 캐싱 + 만료
│       ├── strategies/       # M3: 룰 기반 벤치마크 전략
│       │   ├── base.py
│       │   ├── sma_cross.py         # 학습용 단순 전략
│       │   └── dual_momentum.py     # 룰 기반 벤치마크 (페르소나 시스템과 비교)
│       ├── backtest/         # M3: vectorbt
│       │   ├── engine.py
│       │   └── reports.py
│       ├── personas/         # M4~: 5-페르소나 시스템 (핵심)
│       │   ├── base.py              # Persona 추상 클래스
│       │   ├── prompts/             # 시스템 프롬프트 템플릿 (Jinja2)
│       │   │   ├── macro.jinja
│       │   │   ├── micro.jinja
│       │   │   ├── decision.jinja   # 박세훈 페르소나
│       │   │   ├── risk.jinja
│       │   │   ├── portfolio.jinja
│       │   │   └── retrospective.jinja
│       │   ├── macro.py             # Opus 4.7, 주간
│       │   ├── micro.py             # Sonnet 4.6, 일간
│       │   ├── decision.py          # Sonnet 4.6, 일간 — 박세훈 페르소나
│       │   ├── risk.py              # Sonnet 4.6, 매매 직전 — SoD 검증
│       │   ├── portfolio.py         # Sonnet 4.6, 일간, M5+ — 사이즈 조정
│       │   ├── retrospective.py     # Sonnet 4.6, 주간, M5+ — 메타-학습
│       │   ├── orchestrator.py      # 호출 순서 관리, 페르소나 간 데이터 전달
│       │   └── audit.py             # 모든 페르소나 응답 영구 기록
│       ├── risk/             # M5: 위험 관리 (코드 룰)
│       │   ├── limits.py            # 한도 5종
│       │   ├── circuit_breaker.py
│       │   └── emergency.py         # /halt /resume
│       ├── alerts/
│       │   └── telegram.py          # 텔레그램 발송
│       ├── bot/              # M5: 텔레그램 봇 listener
│       │   └── telegram_bot.py
│       ├── scheduler/        # M4~: 스케줄링
│       │   ├── daily.py             # 평일 18:00 마이크로 → 18:30 결정 → 18:35 포트폴리오 → 18:40 리스크 → 18:50 매매
│       │   ├── weekly.py            # 금요일 18:00 매크로 / 일요일 회고
│       │   └── reports.py           # 21:00 일일 리포트
│       ├── reports/          # M5: 리포트 생성
│       │   └── daily_report.py
│       ├── scripts/          # CLI 진입점 (밖에서 호출 가능한 단발)
│       │   ├── check_kis.py
│       │   ├── paper_buy_one.py
│       │   ├── run_personas.py      # 수동 페르소나 호출 (테스트용)
│       │   ├── run_strategy.py      # 룰 기반 벤치마크 실행
│       │   └── daily_report.py
│       └── cli.py            # `trading <subcommand>` 단일 진입점
├── notebooks/                # M3: Jupyter 백테스트 실험
├── data/                     # 시세 캐시 (git 제외)
├── logs/                     # 매매·오류·페르소나 응답 로그 (git 제외)
└── backups/                  # DB 백업 (git 제외)
```

## 컨테이너 토폴로지

```
┌──────────────────────────────────────────────────┐
│ Host (Linux Ubuntu, ~/trading/)                  │
│                                                  │
│  Docker Compose: project name "trading"          │
│                                                  │
│  ┌──────────────────────────────────────────┐    │
│  │ app (Python 3.13-slim + uv)              │    │
│  │  - trading.cli                           │    │
│  │  - apscheduler (in-container cron)       │    │
│  │  - telegram bot listener                 │    │
│  │  Volumes: ./src ./notebooks ./data ./logs│    │
│  │  Network: trading-net                    │    │
│  └──────────────────────────────────────────┘    │
│                                                  │
│  ┌──────────────────────────────────────────┐    │
│  │ postgres (postgres:16-alpine)            │    │
│  │  Volume: trading_pgdata                  │    │
│  │  Healthcheck: pg_isready                 │    │
│  │  Network: trading-net                    │    │
│  └──────────────────────────────────────────┘    │
└──────────────────────────────────────────────────┘
        │                 │              │
        │ (outbound HTTPS)               │
        ▼                 ▼              ▼
   KIS Developers    Telegram Bot   Anthropic API
   paper / live      api            (Claude)
                                    Opus 4.7 + Sonnet 4.6
        │
        ▼
   외부 데이터 소스
   - pykrx (공개 거래소 데이터, API 키 불필요)
   - yfinance (Yahoo Finance, 비공식)
   - FRED API (Fed 데이터, 무료 키)
   - ECOS API (한국은행, 무료 키)
   - OpenDART (DART 공시, 무료 키)
```

외부 인터페이스 7개:
1. **KIS Developers** — paper(`openapivts.koreainvestment.com:29443`), live(`openapi.koreainvestment.com:9443`)
2. **Telegram Bot API** — 알림 + 명령어 수신
3. **Anthropic API** — Claude Opus 4.7 (매크로) + Sonnet 4.6 (그 외 4 페르소나 + 일일 리포트)
4. **pykrx** — 한국 주식 공개 데이터 (라이브러리, 외부 호출 없음)
5. **yfinance** — 글로벌 자산
6. **FRED API** — Fed 거시 데이터 (https://fred.stlouisfed.org/)
7. **ECOS API** — 한국은행 경제통계 (https://ecos.bok.or.kr/)
8. **OpenDART** — DART 공시 (https://opendart.fss.or.kr/)

(데이터 소스는 5종이지만 라이브러리·웹 인터페이스로 다양화 — pykrx는 외부 호출 없이 동작)

## 모드 분리

`TRADING_MODE` 환경변수로 분기. 기본값 `paper`.

| 모드 | KIS 엔드포인트 | tr_id 접두 | 자본 영향 |
|---|---|---|---|
| `paper` (기본) | `openapivts.koreainvestment.com:29443` | `VTTC...` | 0 (모의) |
| `live` | `openapi.koreainvestment.com:9443` | `TTTC...` | 실제 |

`live` 모드는 명시적 활성화 + M6 진입 조건 충족 시에만 사용.

## 페르소나 호출 순서 — 장중 매매 기반

### Pre-market (07:00~09:00)
```
07:00  데이터 갱신
       - pykrx 전일 종가/외국인-기관 매매
       - 야간 미국 시장 종가 (yfinance: S&P500, Nasdaq, VIX)
       - 환율 (USD/KRW)
       - DART 공시 (전일 마감 후 ~ 오늘 새벽까지)
       - FRED/ECOS (주 1회, 캐시)

07:30  Micro Persona (Sonnet 4.6) — 풀 분석
       - 입력: 갱신된 시세·공시·재무·기술적·수급 + KOSPI200/KOSDAQ150/워치리스트
       - 출력: 매수/매도/관망 후보 종목 + 근거

07:50  Decision Persona (Sonnet 4.6, 박세훈 페르소나)
       - 입력: Macro 주간 캐시 + Micro 출력
       - 출력: 매매 시그널 (종목/방향/권장수량/근거)
       - DB persona_decisions에 영구 기록

07:55  [M5+] Portfolio Persona (Sonnet 4.6) — 보유 5종 이상 시
       - 사이즈 조정 또는 reject

08:00  Risk Persona (Sonnet 4.6) — SoD 검증
       - APPROVE / HOLD / REJECT + 근거

08:05  코드 룰 검증 (회로차단기, 한도 5종)

09:00  KRX 시가에 자동 매매 (paper / live)
```

### 장중 정기 (4회)
```
09:30 / 11:00 / 13:30 / 14:30
       - Micro 캐시 + 현재 KIS 시세 갱신
       - Decision Persona 호출 (변경 시그널만)
       - Risk Persona 검증
       - 코드 룰 → 즉시 매매 (변경 시그널만)
```

### 이벤트 트리거 (실시간, 장중 09:00~15:30)
```
[보유 종목 ±3% 변동 / 신규 공시 / VIX 급변 등 감지]
   ↓
Decision Persona (트리거 컨텍스트 포함) → Risk → 코드 룰 → 즉시 매매
```

### 장 마감 후
```
15:30  KRX 마감

16:00  일일 리포트 생성 (Sonnet 4.6)
       - 오늘 매매 + PnL + 페르소나 응답 요약 + AI 인사이트
       - 텔레그램 발송
```

### 주간 / 회고
```
금요일 17:00  Macro Persona (Opus 4.7)
              - 다음 주 시장 가이드, 캐시 1주

일요일 임의   [M5+] Retrospective Persona (Sonnet 4.6)
              - 지난 주 매매 + 페르소나 응답 회고
              - 시스템 프롬프트 개선 제안 (자동 적용 X, 박세훈에게 보고)
```

### 트리거 임계치 (M5에서 정밀화)
| 트리거 | 임계치 | 호출 |
|---|---|---|
| 보유 종목 가격 변동 | ±3% (장중 분 단위 체크) | Decision + Risk |
| 신규 공시 도착 | 보유/관심 종목 DART 공시 | Decision + Risk |
| 글로벌 변동성 | VIX +15% 또는 USD/KRW ±1% | Macro 캐시 갱신 검토 알림 |
| 일일 손실 한도 근접 | -0.7% 도달 | 자동 알림, -1.0% 도달 시 회로차단 |

### 텔레그램 브리핑 패턴 (시계열 채널)

박세훈 님이 텔레그램에서 *"AI가 무슨 생각으로 무엇을 했는가"* 를 시계열로 추적할 수 있도록, 모든 페르소나 호출과 매매 직후 브리핑 메시지를 발송한다.

#### 메시지 형식 표준
| 메시지 종류 | 발송 시점 | 포함 정보 |
|---|---|---|
| 페르소나 응답 | 페르소나 호출 직후 | 페르소나 이름, 모델, 응답 요약(3~5줄), 토큰/비용 |
| 매매 체결 | KIS 주문 응답 후 | 종목/방향/수량/체결가, 수수료, **자산현황 갱신** (총자산/현금%/주식%) |
| 이벤트 트리거 | 트리거 감지 + 페르소나 결과 묶음 | 트리거 사유, 페르소나 응답, 매매 여부 |
| 회로차단 | 한도 위반 즉시 | 위반 한도, 현재 값, 차단된 주문 |
| /halt 응답 | 명령어 수신 | 정지 상태 확인, 영향 범위 |
| 일일 리포트 | 영업일 16:00 | 오늘 매매·PnL·페르소나 비용·SoD 통계 |

#### 알림 피로 방지 (M5)
- 동일 결과("신규 시그널 없음")가 3회 연속 → 침묵 모드 자동 전환
- 침묵 모드: 매매 발생, 이벤트 트리거, 회로차단만 발송. 정기 페르소나 응답은 일일 리포트에서 일괄.
- 박세훈 님이 `/verbose` 명령으로 침묵 모드 해제 가능.

#### 메시지 예시 (Pre-market 매매 흐름)
```
[Micro · Sonnet · 07:30]
오늘 후보 3건
• 매수 후보: SK하이닉스 — 외국인 5일 순매수, RSI 55
• 관망: 삼성전자 — 실적 발표 D-2
• 매도 검토: ABC바이오 — 거래정지 가능성
4,521 in / 832 out / 1,150원

[Decision · 박세훈 · 07:50]
시그널: SK하이닉스 5주 매수 (3.6%)
근거: Macro risk-on + 외국인 매수 + 한도 내

[Risk · SoD · 08:00]
APPROVE — 종목 한도 내, 섹터 편중 없음

[매매 체결 · 09:00]
SK하이닉스 5주 @ 142,500 매수 / 수수료 107원
자산: 9,287,500원 (현금 96.4% / 주식 3.6%)
```

## 데이터 흐름

### 매매 실행 (M4~M5)
```
[Schedule trigger]
   ↓
[Data adapters: 시세·공시·재무·뉴스]
   ↓
[Personas orchestrator: Micro → Decision → (Portfolio) → Risk]
   ↓
[Risk + Code rules]
   ├ APPROVE → KIS Order API → DB orders/audit_log → Telegram alert
   └ REJECT → DB persona_decisions(rejected) → Telegram alert
```

### 일일 리포트 (M5)
```
[21:00 KST trigger]
   ↓
[DB: positions/orders/persona_runs] + [KIS balance API]
   ↓
[Daily report persona (Sonnet)] — 정보 가공만
   ↓
[Telegram daily report]
```

### 회로차단기 (M5)
```
[모든 신규 주문] → [Risk Persona] + [Code limits check]
                       ├ pass → KIS order
                       └ fail → block + audit_log + Telegram
                                   ↓
                             [/halt state in DB]
```

## 영속 데이터 (DB 스키마 진화)

| 마일스톤 | 추가 테이블 |
|---|---|
| M2 | `orders`, `positions`, `audit_log` |
| M3 | `ohlcv` (시세 캐시), `macro_indicators` (FRED/ECOS 캐시), `disclosures` (DART 캐시) |
| M4 | `persona_runs` (모든 페르소나 호출+응답+토큰비용), `persona_decisions` (결정 페르소나 시그널), `risk_reviews` (리스크 페르소나 검토) |
| M5 | `portfolio_adjustments`, `retrospectives`, `circuit_breaker_state`, `daily_reports` |
| M6 | `live_capital_history` (자본 증액 이력) |

마이그레이션은 `src/trading/db/migrations/`에 SQL 파일로 버전 관리. 마일스톤별 적용.

## 보안 경계

- 시크릿: `.env` 만에 평문 저장, `chmod 600`, git 절대 커밋 X
- KIS 호출 IP 제한: M6 실거래 진입 전 KIS Developers 포털에서 박세훈 외부 IP 1개로 제한
- 컨테이너 user: 비-root (`USER 1000:1000` in Dockerfile)
- 외부 노출 포트: **없음**. Postgres는 컨테이너 네트워크 내부에서만, app은 outbound only.
- Jupyter: 켤 때만 `127.0.0.1`에 바인딩, 토큰 인증.
- 페르소나 응답에 시크릿 노출 금지 — 시스템 프롬프트에서 자격증명 참조 차단

## 인접 시스템과의 일관성

`~/n8n/`(이미 운영 중)과 동일 패턴 유지:
- `compose.yaml` + `.env` (perm 600) + `.env.example` + `.gitignore` + `backup.sh`
- backup.sh: timestamp 디렉토리 + pg_dump.gz + tar.gz + retention 30개
- `name: trading` 명시 (n8n과 분리)
