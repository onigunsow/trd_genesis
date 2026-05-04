---
id: SPEC-TRADING-007
artifact: plan
version: 0.1.0
created: 2026-05-04
updated: 2026-05-04
author: onigunsow
---

# SPEC-TRADING-007 — Implementation Plan

## 도입 시점

본 SPEC은 SPEC-TRADING-001 v0.2.0 M5 모의 운영 3주가 안정 통과한 이후에 도입한다. SPEC-001의 페르소나 시스템(Macro/Micro/Decision/Risk/Portfolio/Retrospective)이 stateless로 작동하는 상태가 baseline이며, 본 SPEC은 그 위에 비침투적으로 (1) 입력 풍부화 layer (Static Market Context) 와 (2) 누적 학습 layer (Dynamic Persona Memory) 두 레이어를 추가한다.

priority: medium — SPEC-001 안정화 이전에는 도입하지 않는다.

## Implementation Phases

본 SPEC은 시간 추정을 사용하지 않으며 우선순위 기반 P1~P5 순서로 진행한다. 각 phase는 단일 책임이며 다음 phase는 이전 phase의 검증 통과를 전제로 한다.

### Phase P1 — DB Schema (Foundation)

Primary goal — 신규 테이블 + 인덱스 + 제약조건 마이그레이션 적용 및 회귀 테스트.

**산출물**:
- `src/trading/db/migrations/008_persona_memory.sql`
  - `macro_memory` 테이블 (REQ-MEM-02-1, REQ-MEM-02-2)
  - `micro_memory` 테이블 (REQ-MEM-02-1, REQ-MEM-02-2)
  - `retrospectives.memory_proposals` JSONB 컬럼 추가 (REQ-MEM-05-2)
  - CHECK 제약 (importance 1~5, scope/kind 도메인, status 도메인, source_refs 형식)
  - 인덱스: `(scope, status, importance DESC)`, `(scope_id, status)`, `(valid_until)`
- `src/trading/memory/store.py` 초안 — CRUD 함수 시그니처만 (구현은 P3에서)
- `tests/db/test_migration_008.py` — 마이그레이션 적용 + 회귀 (백업·복원 사이클로 데이터 무결성)

**전제조건**: SPEC-001 M2~M5 마이그레이션 모두 적용된 상태.

### Phase P2 — Static Context Builders (cron + 코드 only)

Primary goal — 4개 `.md` 파일 자동 생성. LLM 호출은 macro_news.md 단 1건.

**산출물**:
- `src/trading/contexts/build_macro_context.py` (REQ-CTX-01-2) — 06:00 KST cron, FRED+ECOS+yfinance+ohlcv 캐시에서 표 렌더링
- `src/trading/contexts/build_micro_context.py` (REQ-CTX-01-3) — 06:30 KST cron, fundamentals+flows+ohlcv 캐시에서 워치리스트별 표 렌더링
- `src/trading/contexts/build_micro_news.py` (REQ-CTX-01-4) — 06:45 KST 영업일 cron, DART disclosures 캐시 7일 정리
- `src/trading/contexts/build_macro_news.py` (REQ-CTX-01-5) — 금 16:30 KST cron, 5개 RSS 피드 fetch + 단일 Sonnet 4.6 호출
- `src/trading/contexts/rss_feeds.py` — RSS 피드 URL 상수 (Reuters World, FT Markets, Bloomberg Politics, Federal Reserve press, Bank of Korea press)
- `src/trading/scheduler/daily.py` — 새 cron 잡 4건 등록
- 실패 처리 모듈 — 갱신 실패 시 기존 파일 유지 + 텔레그램 시스템 에러 (REQ-CTX-01-6)

**RSS·소스 매트릭스 (2026-05-04 리서치 후 확정 — Open Decision 1 해결)**:

### Tier 1 — 즉시 사용 (검증된 RSS, 한국 매체 경제·금융 특화)
| 매체 | 카테고리 | URL | 갱신 |
|---|---|---|---|
| 한국경제 | 경제 | `http://rss.hankyung.com/economy.xml` | 일간 |
| 한국경제 | 증시 | `http://rss.hankyung.com/stock.xml` | 일간 |
| 한국경제 | 산업 | `http://rss.hankyung.com/industry.xml` | 일간 |
| 매일경제 | 경제 | `http://file.mk.co.kr/news/rss/rss_30100041.xml` | 일간 |
| 파이낸셜뉴스 | 증시 | `http://www.fnnews.com/rss/fn_realnews_stock.xml` | 일간 |
| 파이낸셜뉴스 | 금융 | `http://www.fnnews.com/rss/fn_realnews_finance.xml` | 일간 |
| 헤럴드경제 | 증시 | `http://biz.heraldm.com/rss/010106000000.xml` | 일간 |
| 조선비즈 | 마켓 | `http://biz.chosun.com/site/data/rss/market.xml` | 일간 |
| 조선비즈 | 정책·금융 | `http://biz.chosun.com/site/data/rss/policybank.xml` | 일간 |
| 중앙 | 경제 | `http://rss.joinsmsn.com/joins_money_list.xml` | 일간 |

### Tier 2 — 공식 (한국)
- **금융위원회 RSS**: `https://www.fsc.go.kr/ut060101` — 정책 발표
- **정책브리핑(korea.kr)**: `https://www.korea.kr/etc/rss.do` — 정부 통합 RSS
- **한국은행 보도자료**: `https://www.bok.or.kr/portal/singl/newsData/list.do?menuNo=201263` — RSS 미제공 → HTML 페이지 polite scrape (User-Agent 식별, 갱신 일 1회)

### Tier 3 — 공식 (글로벌)
- **Federal Reserve press**: `https://www.federalreserve.gov/feeds/press_all.xml` — RSS 검증됨
- **Federal Reserve FOMC calendar**: `https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm` — 일정 페이지 (수동 또는 weekly scrape)

### Tier 4 — 글로벌 마켓 (Google News query 우회 + 정책 모니터링)

**채택 결정 (2026-05-04 박세훈 님 동의)**: Google News RSS query 활용 (옵션 a). Google 정책 변경 위험은 모니터링으로 대응.

**쿼리 템플릿**:
```
Reuters Korea/Asia: https://news.google.com/rss/search?q=site:reuters.com+(korea+OR+asia)+(market+OR+stock+OR+economy)&hl=en-KR&gl=KR&ceid=KR:en
Bloomberg Korea: https://news.google.com/rss/search?q=site:bloomberg.com+korea+market&hl=en-KR&gl=KR&ceid=KR:en
FT Markets headlines: https://news.google.com/rss/search?q=site:ft.com+market&hl=en-KR&gl=KR&ceid=KR:en
WSJ Markets headlines: https://news.google.com/rss/search?q=site:wsj.com+market&hl=en-KR&gl=KR&ceid=KR:en
지정학(global): https://news.google.com/rss/search?q=geopolitical+oil+OR+OPEC+OR+iran+OR+china&hl=en&ceid=US:en
```
- 응답은 *제목 + 발행일 + 원문 URL*. 본문은 fetch 안 함 (저작권 회피).

### Tier 4 정책 변경 모니터링 (REQ-CTX-04-7 신규)
1. **건강 체크 cron**: 매월 1일 06:00 KST에 5개 Google News query 실행:
   - 응답 HTTP 200 + valid RSS XML
   - 24시간 이내 발행 항목 ≥ 1개
2. **실패 임계**:
   - 1회 실패 → audit_log warning (silent retry 24h 후 재시도)
   - 2회 연속 실패 → 텔레그램 시스템 에러 알림
   - 3회 연속 실패 → Tier 4 자동 비활성화 + 박세훈 님 결정 대기 (Tier 1 한국 매체로 글로벌 헤드라인 fallback)
3. **macro_news.md 빌드**:
   - Tier 4가 활성이면 query 결과를 LLM 요약에 포함
   - Tier 4가 비활성이면 *"글로벌 직접 피드 비활성 — 한국 매체 번역 인용 사용"* 명시
4. **연 1회 검토**: 매년 1월 1일에 Google News URL 형식·QPS·robots.txt 변경 여부 수동 점검 (audit_log 'TIER4_ANNUAL_REVIEW')

- **FT, WSJ**: 유료벽 — Google News query에서 헤드라인만 사용 가능, 본문 fetch 회피

### Tier 5 — 종목·산업 리포트 (저작권 고려)
- **한경 컨센서스**: `https://markets.hankyung.com/consensus` — 증권사 리포트 메타데이터 통합 (제목·요약만 추출, 본문 다운로드 회피)
- **DART API**: 이미 캐시 중 (`disclosures` 테이블) — 종목별 공시는 이걸로 충분
- **증권사 직접 자동화**: ⚠ 저작권 우려 — *제목·요약·발표일자만* 추출, 본문 PDF 다운로드 회피

### Tier 6 — 보충 (선택)
- **SBS 뉴스 RSS**: `https://news.sbs.co.kr/news/rss.do` — 일반 뉴스 + 경제 카테고리
- **MBC 경제**: `http://imnews.imbc.com/rss/news/news_04.xml`
- **노컷뉴스 경제**: `http://rss.nocutnews.co.kr/NocutEconomy.xml`

### macro_news.md 빌드 시 사용 (REQ-CTX-01-5)
**1차 시도**: Tier 1 (한국 경제 매체 10건 RSS) + Tier 2 (한국 공식 3건) + Tier 3 (Fed RSS) → 총 ~14 피드 헤드라인 수집 → Sonnet 4.6 1회 호출로 *"지정학·정책·환율·유가·시장 체제 5~7개 헤드라인 요약"*.

**Reuters/Bloomberg fallback**: Google News query `https://news.google.com/rss/search?q=...` 사용. 서드파티 의존성 명시.

### micro_news.md 빌드 시 사용 (REQ-CTX-01-4)
- DART 공시 캐시 (보유·관심 종목)
- Tier 1 매체에서 종목 코드 키워드 매칭 (한경·매경·파이낸셜뉴스 RSS 본문에서 005930, 000660 등 6자리 매칭)
- 한경 컨센서스 (종목별 컨센서스 변경)

### 라이브러리/도구
- **feedparser** (Python): RSS 파싱
- **httpx**: HTTP 요청 (User-Agent 식별 — `trading-bot/0.1 (personal use)`, polite delay 2초)
- **BeautifulSoup**: HTML scrape (한국은행 fallback)

### Phase P3 — Persona Base + Memory Ops Execution

Primary goal — 페르소나 응답에서 memory_ops 추출 + 단일 트랜잭션 실행 + ownership 검증.

**산출물**:
- `src/trading/personas/base.py` 확장 — 응답 JSON 파서가 `memory_ops` 추출, orchestrator로 전달
- `src/trading/personas/orchestrator.py` 확장 — `memory.store.execute_ops(persona_name, persona_run_id, ops)` 호출
- `src/trading/memory/store.py` 본 구현
  - `execute_ops()` — 단일 트랜잭션, 부분 실패 시 ROLLBACK (REQ-MEM-03-2)
  - ownership 검증 (REQ-MEM-03-3)
  - source_refs 자동 첨부 (persona_run_id) + 검증 (REQ-MEM-02-3)
  - 모든 ops 결과를 audit_log에 기록 (REQ-MEM-03-4)
  - 일일 retention sweep `archive_stale_memories()` (REQ-MEM-02-4)

**테스트**: ownership 위반 시 거부, source_refs 누락 시 거부, 트랜잭션 부분 실패 시 ROLLBACK 100% 커버리지 (TRUST 5에서 한도/회로차단 모듈과 같은 등급).

### Phase P4 — Memory Injection into Persona Input

Primary goal — `.md` 파일 + 활성 메모리 조회 + 토큰 캐핑 + persona input 조립.

**산출물**:
- `src/trading/memory/injector.py`
  - `load_macro_input()` — macro_context.md + macro_news.md + active macro_memory top 20 with cap 4,000 tokens (REQ-MEM-04-1, REQ-MEM-04-3)
  - `load_micro_input()` — micro_context.md + micro_news.md + active micro_memory (워치리스트 종목 + 섹터) top 20 with cap 2,000 tokens (REQ-MEM-04-2, REQ-MEM-04-3)
  - `mark_accessed()` — last_accessed_at LRU 갱신 (REQ-MEM-04-4)
- 토큰 카운팅: Anthropic SDK의 token counter 사용 (`anthropic.tokenizers`)

**오케스트레이터 통합**:
- Macro persona 호출 직전 `injector.load_macro_input()` 호출
- Micro persona 호출 직전 `injector.load_micro_input()` 호출
- Decision/Risk/Portfolio/Retrospective는 본 SPEC 버전에서 메모리 직접 소비하지 않음 (Future Scope)

### Phase P5 — Persona Prompts + Retrospective Memory Audit

Primary goal — 시스템 프롬프트 보강 + 회고 페르소나가 memory consistency report 소비.

**산출물**:
- `personas/prompts/macro.jinja` 확장
  - memory_ops 응답 스키마 명시 (REQ-MEM-03-1)
  - memory bias 차단 directive (REQ-MEM-04-5)
  - 활성 메모리 input 섹션 placeholder
- `personas/prompts/micro.jinja` 확장 — 동일 항목
- `src/trading/personas/retrospective.py` 확장
  - 주간 memory ops 통계 + consistency report (코드 산출, LLM X) 입력에 추가 (REQ-MEM-05-1)
  - 회고 응답에서 `memory_proposals` 추출 후 `retrospectives.memory_proposals` JSONB 저장 (REQ-MEM-05-2)
- `src/trading/reports/daily_report.py` 확장 — 메모리 통계 한 줄 추가 (REQ-MEM-05-3)

## Risk Analysis

| 위험 | 영향 | 대응 |
|---|---|---|
| 메모리 폭증 (1년 1,000+ rows) | input 토큰 비용 증가 + 페르소나 혼란 | importance 기반 retention 정책 (REQ-MEM-02-4) + token cap (REQ-MEM-04-3) + Retrospective 주간 검토 (REQ-MEM-05-1) |
| 환각으로 가짜 메모리 생성 | 페르소나가 잘못된 인사이트 누적 → 의사결정 오염 | source_refs 의무 (REQ-MEM-02-3) + 회고 검토 + Retrospective 모순/중복 후보 자동 산출 |
| 토큰 비용 증가 (~+30%) | 월 ~13~17만원 추가 | input cap (4K/2K) + LRU 컷팅 + 매크로 호출 빈도 (주 1회) 변경 없음 → 절대 증가량 제한적 |
| Memory bias (낡은 정보가 최신 데이터 무시) | 의사결정 품질 저하 | 시스템 프롬프트 directive (REQ-MEM-04-5) + 현재 데이터 우선 룰 + memory_ops로 archive/supersede 가능 |
| cron .md 갱신 실패 | 페르소나 input 부재 → 분석 깊이 저하 | 기존 파일 유지 + 텔레그램 시스템 에러 (REQ-CTX-01-6) + audit_log |
| 외부 RSS 피드 의존성 (macro_news.md) | 주 1회 갱신 실패 → 1주 stale | RSS fallback 리스트 (5개 중 일부 실패해도 다른 피드로 보강) + RSS 미제공 피드는 HTML scrape 또는 대체 피드로 교체 (P2 ADR) + macro_news.md는 미션 크리티컬 X |
| ownership 위반 시도 (Macro persona가 micro_memory 수정) | 데이터 오염 | DB 트랜잭션 거부 + audit_log 'MEMORY_OP_OWNERSHIP_REJECT' + 텔레그램 알림 (REQ-MEM-03-3) |
| Anthropic API 토큰 카운팅 오차 | input cap 초과로 페르소나 호출 실패 | 캡 산정 시 10% 마진 + 실패 시 메모리 부분 truncate fallback |

## Reference Implementations

- 인접 시스템 `~/n8n/`의 cron + Postgres 패턴을 본 SPEC의 Static Context cron 4종에 동일 적용
- SPEC-001의 `daily_reports` 테이블 라이프사이클 패턴 (생성·조회·archive)을 `macro_memory`, `micro_memory`에 동일 적용
- SPEC-001의 `audit_log` 패턴 (event_type prefix + JSON payload)을 `MEMORY_OP_*` 이벤트에 그대로 사용
- cron 스케줄링은 `src/trading/scheduler/daily.py`의 KST cron 인프라(SPEC-001 REQ-CAL-05-18)를 그대로 활용

## 비용 영향

- 월 LLM 비용: SPEC-001 M5+ 기준 ~40~50만원 → 본 SPEC 도입 시 페르소나 input 토큰 +1.5K (Macro+Micro 평균) → 호출당 비용 +30% → 월 ~13~17만원 추가 → 총 ~53~67만원
- 자본 1,000만원 대비 5~7% — 부담 큼. 자본 1억+ 증액 시 적정.
- 비용 절감 옵션 (P5 운영 후 검토): input cap 강화 (Macro 4K → 3K, Micro 2K → 1.5K), 또는 메모리 importance 임계 4 이상으로 강화

## Dependencies

본 SPEC은 다음 SPEC-001 컴포넌트에 의존한다:
- `persona_runs` 테이블 (FK semantic for source_refs.persona_run_id)
- `audit_log` 테이블 (event_type 패턴 재사용)
- `holidays.KR()` + KRX 영업일 calendar (REQ-CAL-05-18)
- `silent_mode` 플래그 (REQ-FATIGUE-05-9) — 본 SPEC의 시스템 에러는 silent_mode에서도 발송
- 페르소나 base/orchestrator (REQ-PERSONA-04-1, REQ-PERSONA-04-2)

본 SPEC은 SPEC-001을 수정하지 않는다 — 모든 통합은 base.py + orchestrator.py + prompts/*.jinja의 비파괴적 확장으로 처리한다.

## Open Decisions (P2 진입 시 결정)

1. ~~RSS 피드 가용성 재확인 후 일부 미제공 시 대체 피드 선정~~ → **2026-05-04 해결**: Tier 1~6 매트릭스로 확정. 한국은행은 HTML scrape, Reuters/Bloomberg는 Google News query, 나머지는 직접 RSS 사용.
2. 토큰 카운터 정확도 검증 (Anthropic SDK vs tiktoken-style approximation)
3. memory consistency report의 모순/중복 heuristic 임계 (코사인 유사도 단순 키워드 vs 추후 임베딩으로 격상 — Future Scope)

이 결정은 ADR(Architecture Decision Record) 형식으로 본 plan.md 끝에 추가 append 한다.
