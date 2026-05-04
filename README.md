# trading — 한국 주식 5-페르소나 AI 매매 시스템

박세훈 개인 자본 1,000만원 모의 자동 매매 시스템. KIS Open API + Anthropic Claude 5-페르소나 + Static Context + Dynamic Memory.

- **SPEC**: `.moai/specs/SPEC-TRADING-001/spec.md` (M1~M5 본 SPEC, v0.2.0)
- **SPEC**: `.moai/specs/SPEC-TRADING-007/spec.md` (페르소나 메모리 시스템 + Static Context)
- **SPEC**: `.moai/specs/SPEC-TRADING-008/spec.md` (Cost Optimization — Prompt Caching)
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
- [ ] **M6** — 실거래 진입 (SPEC-TRADING-002 별도 작성 예정, 모의 3주 검증 후)

## 페르소나 시스템

| # | 페르소나 | 모델 | 주기 | 역할 |
|---|---|---|---|---|
| 1 | Macro | Opus 4.7 | 금 17:00 (주간 캐시) | 거시·글로벌·정책 분석, 워치리스트 |
| 2 | Micro | Sonnet 4.6 | 영업일 07:30 + 장중 캐시 | 종목·섹터 매수/매도/관망 후보 |
| 3 | **Decision (박세훈)** | Sonnet 4.6 | 영업일 07:50 + 장중 4회 + 이벤트 | 박세훈 본인의 트레이딩 철학을 외부화한 결정자 |
| 4 | Risk | Sonnet 4.6 | 매매 시그널 발생 시 | SoD 검증자 — APPROVE/HOLD/REJECT |
| 5 | Portfolio (M5+) | Sonnet 4.6 | 보유 5종 이상 시 | 사이즈·섹터 분산 조정 |
| - | Retrospective | Sonnet 4.6 | 일요일 18:00 | 주간 회고, 시스템 프롬프트 개선 제안 |

**SPEC-007 추가**:
- Static Context (cron-managed): `data/contexts/{macro_context,macro_news,micro_context,micro_news}.md`
- Dynamic Memory (DB-managed): `macro_memory`, `micro_memory` 테이블 — 페르소나 자가 갱신, source_refs 의무

## 자동 사이클 일정 (KST)

```
06:00  build_macro_context.md   (코드, 매일)
06:30  build_micro_context.md   (코드, 매일)
06:45  build_micro_news.md      (코드, 영업일)
07:30  Pre-market   Micro → Decision → Risk → 09:00 시가 매매
09:30/11:00/13:30/14:30  장중 정기 4회   Decision → Risk → 즉시 매매
이벤트 트리거 (실시간)   보유 ±3% / DART 공시 / VIX 급변 → Decision
15:30  KRX 마감
16:00  Daily Report             (Sonnet, 매일)
금 16:30  Macro News             (RSS + Sonnet 요약, 주간)
금 17:00  Macro persona          (Opus, 주간 가이드)
일 18:00  Retrospective          (주간 회고)
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
| Anthropic 비용 (M5+ 풀 가동, 캐시 전) | 월 ~30~50만원 |
| Anthropic 비용 (Phase A 캐시 적용 후) | 월 ~18~30만원 (≈40% 절감 검증, 1주 운영 후 재산정) |

### SPEC-008 Prompt Caching (Phase A)

- 시스템 프롬프트에 `cache_control: ephemeral` 적용 (TTL 5분)
- 메모리 블록은 `user_message` 로 분리 → 시스템 해시 안정화 → cache hit 보장
- `persona_runs` 테이블에 `cache_read_tokens`, `cache_creation_tokens` 컬럼
- 비용 계산: input 1.0x · cache_creation 1.25x · cache_read 0.10x
- 일일 리포트에 캐시 적중률 라인 노출 (운영 1주차 검증 게이트: ≥50%)
- Phase B (Claude Code subprocess 활용) 는 ToS 회색지대 + Max 구독 한도 위험으로 Future Scope 보류


## 운영 명령어

```bash
cd ~/trading
docker compose up -d --build         # 빌드 + 기동
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
- **Anthropic API**: Claude Opus 4.7 + Sonnet 4.6
- **Telegram Bot API**: @sehoon_trd_bot
- **FRED, ECOS, OpenDART**: 거시·공시 데이터
- **pykrx 1.2.8+**: KRX 데이터 (KRX_ID/KRX_PW 환경변수 필수)
- **yfinance**: 글로벌 자산
- **holidays**: 한국 KRX 휴장 캘린더
- **feedparser**: SPEC-007 RSS 피드 파싱

## 라이선스

Proprietary — 박세훈 개인 자본 운용 시스템. 외부 사용 X.

---

_마지막 백업: 2026-05-04 23:13:47 KST_
