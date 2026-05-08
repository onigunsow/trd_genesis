# trading — 한국 주식 5-페르소나 AI 매매 시스템

박세훈 개인 자본 1,000만원 모의 자동 매매 시스템. KIS Open API + Claude Code CLI 5-페르소나 (Max 구독, API 비용 0원) + 40소스 뉴스 인텔리전스 + Dynamic Memory.

- **SPEC**: `.moai/specs/SPEC-TRADING-001/spec.md` (M1~M5 본 SPEC, v0.2.0)
- **SPEC**: `.moai/specs/SPEC-TRADING-007/spec.md` (페르소나 메모리 시스템 + Static Context)
- **SPEC**: `.moai/specs/SPEC-TRADING-008/spec.md` (Cost Optimization — Prompt Caching)
- **SPEC**: `.moai/specs/SPEC-TRADING-009/spec.md` (AI/LLM Architecture — Tool-calling + Reflection Loop)
- **SPEC**: `.moai/specs/SPEC-TRADING-010/spec.md` (Cost Optimization 2 — Haiku Hybrid + pgvector)
- **SPEC**: `.moai/specs/SPEC-TRADING-011/spec.md` (JIT State Reconstruction + ProtoHedge Risk)
- **SPEC**: `.moai/specs/SPEC-TRADING-012/spec.md` (Algorithm — Event-CAR Filter + Dynamic Thresholds)
- **SPEC**: `.moai/specs/SPEC-TRADING-013/spec.md` (Global + Sector News Crawling — 40소스 12섹터)
- **SPEC**: `.moai/specs/SPEC-TRADING-014/spec.md` (News Intelligence Analysis — Claude Code CLI 분석)
- **SPEC**: `.moai/specs/SPEC-TRADING-015/spec.md` (All Personas → Claude Code CLI, API 비용 제로화)
- **프로젝트 컨텍스트**: `.moai/project/{product,structure,tech}.md`

## 마일스톤 진행 상태

- [x] **M1** — 인프라 (Docker compose + Postgres + .env 보안 + healthcheck + backup.sh)
- [x] **M2** — KIS API + DB 스키마 v1 (paper 모드 작동, live 차단 작동)
- [x] **M3** — 데이터 어댑터 5종 (pykrx + yfinance + FRED + ECOS + DART) + 룰 기반 백테스트 (SMA + 듀얼 모멘텀)
- [x] **M4** — 5-페르소나 시스템 (Macro Opus + Micro/Decision/Risk Sonnet) + Telegram 시계열 브리핑 + 모의 자동 매매
- [x] **M5** — 위험관리(한도 5종 + 회로차단기) + /halt /resume + 일일리포트 + 3주 모의 운영 준비
- [x] **Phase 1** (정밀화) — 매매 비용 통합 + UNIQUE 멱등성 + 단일 트랜잭션
- [x] **Phase 2** (정밀화) — 거래 안전성 (거래정지·상하한가·매수가능금액)
- [x] **Phase 3** (정밀화) — 시스템 에러 알림 + 누적 손익 + 분할 매매 룰 + gitleaks + 백업 무결성
- [x] **Phase 4** (정밀화) — SPEC-007: Static Context (.md cron) + Dynamic Memory (페르소나 자가 관리)
- [x] **Phase A** (비용 절감) — SPEC-008: Anthropic Prompt Caching (`cache_control: ephemeral`) + 메모리 user_msg 분리 + `persona_runs.cache_*` 컬럼 + 일일 리포트 캐시 적중률
- [x] **SPEC-009** (아키텍처) — Tool-calling 기반 능동적 정보 조회 + Risk REJECT Reflection Loop (최대 2회 재시도)
- [x] **SPEC-010** (비용 2차) — Haiku 하이브리드 라우팅 (Micro/Report/News→Haiku) + pgvector 시맨틱 검색
- [x] **SPEC-011** (인프라) — JIT 실시간 파이프라인 (KIS WebSocket + DART polling) + ProtoHedge 프로토타입 리스크
- [x] **SPEC-012** (전략) — Event-CAR 필터 (예측 |CAR|≥1.5%만 트리거) + ATR 기반 동적 손절/익절
- [x] **SPEC-013** (뉴스) — 글로벌+국내 40소스 12섹터 뉴스 크롤링 (RSS+웹, 본문 추출, 6회/일)
- [x] **SPEC-014** (인텔리전스) — Claude Code CLI 기반 뉴스 분석 (요약+임팩트+[투자 주목]+트렌드, 비용 0원)
- [x] **SPEC-015** (비용 제로화) — 전체 페르소나 Claude Code CLI 전환 (Anthropic API 비용 0원)
- [ ] **M6** — 실거래 진입 (SPEC-TRADING-002 별도 작성 예정, 모의 3주 검증 후)

## 페르소나 시스템

| # | 페르소나 | 실행 방식 | 주기 | 역할 |
|---|---|---|---|---|
| 1 | Macro | **Claude Code CLI** (Max 구독, 0원) | 금 17:00 (주간 캐시) | 거시·글로벌·정책 분석, 워치리스트 |
| 2 | Micro | **Claude Code CLI** (0원) | 영업일 07:30 + 장중 4회 | 종목·섹터 매수/매도/관망 후보 (20종목 스크리닝) |
| 3 | **Decision (박세훈)** | **Claude Code CLI** (0원) | 영업일 07:30 + 장중 4회 + 이벤트 | 박세훈 본인의 트레이딩 철학을 외부화한 결정자 |
| 4 | Risk | **Claude Code CLI** (0원) | 매매 시그널 발생 시 | SoD 검증자 — APPROVE/HOLD/REJECT |
| 5 | Portfolio (M5+) | Claude Code CLI (0원) | 보유 5종 이상 시 | 사이즈·섹터 분산 조정 |
| - | Retrospective | Claude Code CLI (0원) | 일요일 18:00 | 주간 회고, 시스템 프롬프트 개선 제안 |

> SPEC-015: 모든 페르소나가 Claude Code CLI(`claude -p`)로 실행됩니다. Anthropic API 직접 호출 비용 0원.
> CLI 실패 시 Haiku API로 자동 fallback. 3회 연속 실패 시 CLI 자동 비활성화.

**SPEC-007 추가**:
- Static Context (cron-managed): `data/contexts/{macro_context,macro_news,micro_context,micro_news}.md`
- Dynamic Memory (DB-managed): `macro_memory`, `micro_memory` 테이블 — 페르소나 자가 갱신, source_refs 의무

**SPEC-013/014 추가**:
- Intelligence Context: `data/contexts/{intelligence_macro,intelligence_micro}.md`
- 40소스 12섹터 글로벌+국내 뉴스 크롤링 → Claude Code CLI 분석 → 투자 시사점 + 임팩트 스코어 + [투자 주목] 태깅
- 비용 0원 (Claude Code = Max 구독 포함)

## 자동 사이클 일정 (KST)

```
01:00  뉴스 크롤링 (40소스)
01:05  export pending → 01:10 Claude Code 분석 → 01:15 import + report
04:00  뉴스 크롤링
04:05  export → 04:10 분석 → 04:15 import + report
06:00  build_macro_context.md   (코드, 매일)
06:30  build_micro_context.md   (코드, 매일)
06:45  build_micro_news.md      (코드, 영업일)
07:30  Pre-market   Micro → Decision → Risk → 09:00 시가 매매
08:00  뉴스 크롤링
08:05  export → 08:10 분석 → 08:15 import + report
09:30  Intraday 1차   Decision → Risk → 즉시 매매
11:00  뉴스 크롤링 + Intraday 2차
11:05  export → 11:10 분석 → 11:15 import + report
13:30  Intraday 3차
14:30  뉴스 크롤링 + Intraday 4차
14:35  export → 14:40 분석 → 14:45 import + report
15:30  KRX 마감
16:00  Daily Report             (Haiku, 매일)
금 16:30  Macro News             (RSS + Sonnet 요약, 주간)
금 17:00  Macro persona          (Opus, 주간 가이드)
일 18:00  Retrospective          (주간 회고)
22:00  뉴스 크롤링
22:05  export → 22:10 분석 → 22:15 import + report
이벤트 트리거 (실시간)   보유 ±3% / DART 공시 / VIX 급변 → Decision
```

휴장일 (`holidays.KR()` + 주말 + 12/31) 자동 skip — Anthropic 토큰 절약.

## 거버넌스 안전장치

- **Code-rule limits 5종**: 일일 손실 -1%, 종목당 20%, 전체 80%, 단일 주문 10%, 일일 10건
- **Risk Persona SoD**: 결정자(Decision)와 검증자(Risk) 분리
- **Live trading 차단**: `live_unlocked=false` 기본, manual SQL + audit_log 없이 실거래 불가
- **Trade safety**: 거래정지·관리·투자위험·단기과열·상하한가 종목 자동 차단
- **매수가능금액 정확 차감**: KIS `nrcvb_buy_amt` 미체결 매수금 차감 후 평가
- **트랜잭션 + UNIQUE**: orders + audit_log 단일 트랜잭션, `kis_order_no` UNIQUE (멱등성)
- **시스템 에러 silent 금지**: Anthropic/KIS/DB 실패 즉시 텔레그램 알림
- **gitleaks pre-commit**: 시크릿 git 커밋 차단

## 비용 모델

| 항목 | 값 |
|---|---|
| 매수 수수료 (live) | 0.015% |
| 매도 KOSPI (수수료+거래세+농특세) | 0.345% |
| 매도 KOSDAQ (수수료+거래세) | 0.195% |
| Round-trip KOSPI | ≈ 0.36% |
| Anthropic API 비용 (SPEC-015 적용 후) | **월 0원** (전체 페르소나 + 뉴스 분석 = Claude Code CLI) |
| Claude Max 5x 구독료 | 월 $100 (고정, 모든 LLM 작업 포함) |
| ~~Anthropic 비용 (M5+ 풀 가동, 캐시 전)~~ | ~~월 ~30~50만원~~ → 0원 |

### SPEC-008 Prompt Caching (Phase A)

- 시스템 프롬프트에 `cache_control: ephemeral` 적용 (TTL 5분)
- 메모리 블록은 `user_message` 로 분리 → 시스템 해시 안정화 → cache hit 보장
- `persona_runs` 테이블에 `cache_read_tokens`, `cache_creation_tokens` 컬럼
- 비용 계산: input 1.0x · cache_creation 1.25x · cache_read 0.10x
- 일일 리포트에 캐시 적중률 라인 노출 (운영 1주차 검증 게이트: ≥50%)
- Phase B (Claude Code subprocess): SPEC-014에서 뉴스 인텔리전스 분석에 활용 중 (비용 0원)


## 시스템 아키텍처

```
┌─────────────── Docker Containers ───────────────┐    ┌──── Host (bare metal) ────┐
│                                                  │    │                           │
│  trading-scheduler (APScheduler cron)            │    │  persona-watcher.service  │
│    │                                             │    │  (systemd, 2s polling)    │
│    ├─ 06:00 macro_context 빌드                    │    │    │                      │
│    ├─ 06:30 micro_context + daily_screen (코드)   │    │    │  claude -p (Max 구독) │
│    ├─ 06:45 micro_news 빌드                       │    │    │                      │
│    ├─ 07:30 pre_market 사이클                      │    │    │                      │
│    │   ├─ Micro.run() ─── export prompt ──────────┼────→   execute, return ───────┤
│    │   │   import result ←────────────────────────┼────┘                          │
│    │   ├─ Decision.run() ── export ───────────────┼────→   execute, return ───────┤
│    │   │   import result ←────────────────────────┼────┘                          │
│    │   ├─ Risk.run() ──── export ─────────────────┼────→   execute, return ───────┤
│    │   │   import result ←────────────────────────┼────┘                          │
│    │   └─ KIS 주문 실행                            │    │                          │
│    ├─ 08:00 뉴스 크롤링 (40소스)                    │    │  analyze_news.sh (cron)  │
│    ├─ 08:05 export 뉴스분석 ──────────────────────┼────→  08:10 claude -p 분석    │
│    │  08:15 import 결과 ←─────────────────────────┼────┘                          │
│    ├─ 09:30/11:00/13:30/14:30 장중 사이클          │    │  daily_screen.sh (cron)  │
│    ├─ 16:00 Daily Report                          │    │  06:35 LLM 스크리닝      │
│    └─ 금 17:00 Macro                              │    │                          │
│                                                  │    │                          │
│  trading-bot (Telegram listener)                  │    │                          │
│  trading-app (CLI manual execution)               │    │                          │
│  trading-postgres (pgvector/pg16)                  │    │                          │
│                                                  │    │                          │
│  공유 볼륨: ./data/ ↔ /app/data/                   │    │                          │
│    ├─ persona_calls/   (export 프롬프트)           │    │                          │
│    ├─ persona_results/ (import 결과)               │    │                          │
│    ├─ contexts/        (intelligence .md 등)       │    │                          │
│    └─ pending_*.json   (뉴스/스크리닝 대기)         │    │                          │
└──────────────────────────────────────────────────┘    └───────────────────────────┘
```

**비용 구조**: Claude Max 5x 구독($100/월) 하나로 전체 LLM 작업 커버. Anthropic API 추가 비용 0원.

---

## 텔레그램 Feature Flag 활성화 가이드

모든 새 기능은 feature flag로 제어됩니다. **모의 운영 안정 확인 후** 주 단위로 하나씩 활성화하세요.

```
Week 1: /tool-calling on       → 페르소나가 도구로 필요한 데이터만 능동 조회 (토큰 80% 절감)
Week 2: /reflection on         → Risk REJECT 시 Decision에 피드백 → 재시도 (매매 품질 향상)
Week 3: /model-routing on      → Micro/DailyReport/MacroNews → Haiku 4.5 (비용 73% 절감)
Week 4: /semantic-search on    → pgvector 시맨틱 검색 (컨텍스트 정밀화)
Week 5: /jit-pipeline on       → 장중 실시간 델타 반영 (KIS WebSocket + DART polling)
Week 6: /prototype-risk on     → 시장 프로토타입 유사도 기반 동적 익스포저 조절
Week 7: /car-filter on         → 예측 |CAR|≥1.5% 이벤트만 Decision 트리거 (노이즈 제거)
Week 8: /dyn-threshold on      → ATR 기반 동적 손절/익절 (종목별 변동성 적응)
/cli on|off                    → 전체 페르소나 Claude Code CLI 모드 (현재 ON)
```

**롤백**: 문제 발생 시 같은 명령어에 `off`를 붙이면 즉시 비활성화됩니다.
예: `/tool-calling off`, `/reflection off`

**현재 상태 확인**: `/status` 텔레그램 명령으로 전체 flag 상태 확인 가능.

**주의**: Decision/Risk 페르소나는 절대 Haiku로 라우팅되지 않습니다 (하드코딩 보호).

---

## 운영 명령어

```bash
cd ~/trading
docker compose up -d --build         # 빌드 + 기동
docker compose restart scheduler bot # 코드 변경 후 필수
docker compose ps                    # 4 컨테이너 상태 (postgres, app, bot, scheduler)
docker compose logs -f scheduler     # 스케줄러 cron 실행 로그
docker compose logs -f bot           # 텔레그램 명령 listener

# 시스템 상태
docker compose exec app trading healthcheck
docker compose exec app trading status
docker compose exec app trading calendar           # 향후 14일 영업일/휴장 표

# 매매
docker compose exec app trading paper-buy --ticker 005930 --qty 1
docker compose exec app trading run-personas --cycle pre_market
docker compose exec app trading run-personas --cycle weekly_macro

# 위험 통제
docker compose exec app trading halt                # 매매 정지
docker compose exec app trading resume              # 재개
# (또는 텔레그램에서 /halt /resume /status /pnl /verbose /silent)

# 데이터 + 백테스트
docker compose exec app trading fetch-data --source pykrx --symbol 005930
docker compose exec app trading fetch-data --fundamentals 005930
docker compose exec app trading fetch-data --flows 005930
docker compose exec app trading backtest --strategy sma_cross --symbol 005930

# Static Context 빌드
docker compose exec app trading build-context all   # 4개 모두
docker compose exec app trading build-context macro
docker compose exec app trading build-context news-macro    # 주간 LLM 요약

# 뉴스 크롤링 + 인텔리전스 분석 (SPEC-013/014)
docker compose exec app trading crawl-news           # 수동 크롤링
docker compose exec app trading analyze-news         # 수동 분석 (API fallback)
docker compose exec app trading news-health          # 소스 건강 상태
bash scripts/analyze_news.sh                         # 수동 Claude Code 분석 (호스트)

# Persona Watcher (SPEC-015, Claude Code CLI)
systemctl --user status persona-watcher              # 상태 확인
systemctl --user restart persona-watcher             # 재시작
systemctl --user stop persona-watcher                # 중지
journalctl --user -u persona-watcher -f              # 로그

# 리포트 / 백업
docker compose exec app trading daily-report
./backup.sh                                          # 매일 권장
BACKUP_KEEP=7 ./backup.sh                            # retention 일시 변경
```

## 보안 원칙

- `.env` perm 600, git 절대 커밋 X (`.gitignore` 박혀 있음)
- `TRADING_MODE=paper` 기본. `live` 는 `live_unlocked=true` (DB) + 매뉴얼 audit_log 없이 차단
- 컨테이너 user 1000:1000 (비-root)
- Postgres 호스트 포트 미노출, 컨테이너 네트워크 내부에서만
- 시크릿 검증 시 평문 출력 금지 (`[ -n "$VAR" ] && echo "VAR: present"` 패턴)
- KIS 호출 IP 제한은 M6 진입 전 KIS Developers 포털에서 설정

## 트러블슈팅

**`docker compose up -d` 빌드 단계 실패**
→ `pyproject.toml` 의존성 변경 후 캐시 문제. `docker compose build --no-cache app` 시도.

**Healthcheck 실패**
→ `.env` 시크릿 누락 또는 권한 문제. `python -m trading.healthcheck` 로 단계별 확인.

**Scheduler가 새 코드 못 봄**
→ Python 임포트 캐시. **코드 변경 시 항상 `docker compose restart scheduler bot` 필수.**

**페르소나 호출 실패 (credit balance too low)**
→ Anthropic credits 부족. https://console.anthropic.com → Plans & Billing → Add credits.

**KIS rate limit**
→ KIS client에 자동 retry 박혀 있음 (`kis/client.py`). 영구 실패 시 KIS Developers 포털 점검.

## 외부 의존성

- **한국투자증권 KIS Developers**: paper/live API
- **Anthropic API**: Claude Opus 4.7 + Sonnet 4.6 + Haiku 4.5 (SPEC-010 활성화 시)
- **Telegram Bot API**: @sehoon_trd_bot
- **FRED, ECOS, OpenDART**: 거시·공시 데이터
- **pykrx 1.2.8+**: KRX 데이터 (KRX_ID/KRX_PW 환경변수 필수)
- **yfinance**: 글로벌 자산
- **holidays**: 한국 KRX 휴장 캘린더
- **feedparser**: SPEC-007/013 RSS 피드 파싱
- **beautifulsoup4 + lxml**: SPEC-013 웹 스크래핑 (본문 추출)
- **pgvector**: SPEC-010/011 벡터 유사도 검색 (postgres 확장)
- **Voyage AI / OpenAI Embedding API**: SPEC-010 시맨틱 임베딩 (EMBEDDING_API_KEY 필요)
- **Claude Code CLI (v2.1.83+)**: 전체 페르소나 + 뉴스 분석 + 스크리닝 (호스트 systemd + cron, Max 구독)

## 라이선스

Proprietary — 박세훈 개인 자본 운용 시스템. 외부 사용 X.

---

_마지막 업데이트: 2026-05-08 — SPEC-009~015 반영 (전체 페르소나 CLI 전환, API 비용 0원)_
