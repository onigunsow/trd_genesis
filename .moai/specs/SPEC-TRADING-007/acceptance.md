---
id: SPEC-TRADING-007
artifact: acceptance
version: 0.1.0
created: 2026-05-04
updated: 2026-05-04
author: onigunsow
---

# SPEC-TRADING-007 — Acceptance Criteria

본 문서는 SPEC-TRADING-007의 5개 모듈에 대한 검증 시나리오를 Given-When-Then 형식으로 정의한다. 모든 시나리오는 모의 환경(`TRADING_MODE=paper`) + Postgres 격리 schema에서 실행 가능해야 한다.

## Module 1 — Static Market Context

### A1.1 macro_context.md 자동 생성 (REQ-CTX-01-1, REQ-CTX-01-2)

- **Given** `macro_indicators`, `ohlcv` 테이블에 FRED+ECOS+yfinance 7일치 데이터 캐시 존재
- **When** 06:00 KST cron 실행 (`build_macro_context.py`)
- **Then** `~/trading/data/contexts/macro_context.md` 파일이 mode 644로 생성/갱신
- **And** 파일 본문에 12개 이상의 거시지표 표 (Fed funds rate, 한은 기준금리, USD/KRW, 10Y/2Y 국채, S&P500, Nasdaq, VIX, 금, 원유, BTC, RRPONTSYD, BAMLH0A0HYM2 최소 12종) 포함
- **And** LLM API 호출 0건 (audit_log에 ANTHROPIC 호출 없음 확인)

### A1.2 micro_context.md 워치리스트 표 (REQ-CTX-01-3)

- **Given** 워치리스트 5종목 등록 + `fundamentals`, `flows`, `ohlcv` 캐시 존재
- **When** 06:30 KST cron 실행 (`build_micro_context.py`)
- **Then** `micro_context.md`에 5종목 각각의 펀더(PER/PBR/ROE/EPS/시총) + 수급(외국인/기관/개인 5일·20일 누적) + 기술적(MA20/MA60/RSI/MACD/거래량) 표 포함
- **And** 5종목 모두 표 형식 일관, 누락 데이터는 `n/a`로 표시
- **And** LLM API 호출 0건

### A1.3 micro_news.md 영업일 한정 (REQ-CTX-01-4)

- **Given** 오늘이 영업일이며 보유 종목 + 워치리스트의 DART 공시 7일치가 `disclosures` 테이블에 존재
- **When** 06:45 KST cron 실행 (`build_micro_news.py`)
- **Then** `micro_news.md`에 종목별 일자별 공시 정리 (rcept_no, 제목, 일자, 공시유형) 포함
- **And** 비영업일 (주말/공휴일)에는 cron이 calendar 체크 후 skip + audit_log skip 사유 기록

### A1.4 macro_news.md 단일 LLM 호출 (REQ-CTX-01-5)

- **Given** 5개 RSS 피드 정상 응답 + ANTHROPIC_API_KEY 유효
- **When** 금요일 16:30 KST cron 실행 (`build_macro_news.py`)
- **Then** 단일 Sonnet 4.6 호출 1건이 `persona_runs`에 기록 (persona_name='macro_news_summarizer')
- **And** `macro_news.md`에 5~7개 글로벌 정책·지정학 헤드라인 bullet 포함
- **And** 각 헤드라인 끝에 출처 RSS 피드명 명시 (Reuters/FT/Bloomberg/Fed/BoK 중 하나)

### A1.5 갱신 실패 시 기존 파일 유지 (REQ-CTX-01-6)

- **Given** `macro_context.md`가 어제 정상 생성된 상태
- **When** 06:00 cron 실행 중 FRED API 타임아웃 발생
- **Then** `macro_context.md` 파일은 어제 내용 그대로 유지 (mtime 변경 없음)
- **And** Telegram chat 60443392에 "[system error] macro_context build failed: ..." 메시지 도착
- **And** `audit_log`에 event_type='CONTEXT_BUILD_FAIL' + payload에 에러 상세 기록
- **And** silent_mode=true 상태에서도 발송됨 (시스템 에러는 silent 무시)

## Module 2 — Memory Schema

### A2.1 source_refs 누락 INSERT 거부 (REQ-MEM-02-3)

- **Given** 마이그레이션 008 적용 완료
- **When** `INSERT INTO macro_memory (...) VALUES (..., source_refs='{}')` 시도 (persona_run_id 키 없음)
- **Then** DB 또는 application layer가 INSERT 거부
- **And** audit_log에 'MEMORY_OP_CREATE_FAIL' + 사유 'source_refs missing persona_run_id' 기록

### A2.2 valid_until 도달 자동 archive (REQ-MEM-02-4)

- **Given** macro_memory에 `valid_until = CURRENT_DATE - 1`, `status='active'`, `importance=2` 행 존재
- **When** 일일 retention sweep (06:55 KST) 실행
- **Then** 해당 행의 `status='archived'` 갱신
- **And** audit_log에 'MEMORY_OP_ARCHIVE_AUTO' 이벤트 기록

### A2.3 importance ≥ 4 메모리 60일 보관 (REQ-MEM-02-4)

- **Given** macro_memory에 `last_accessed_at = NOW() - INTERVAL '45 days'`, `importance=5`, `status='active'` 행 A 와 `last_accessed_at = NOW() - INTERVAL '45 days'`, `importance=2` 행 B 존재
- **When** 일일 retention sweep 실행
- **Then** 행 B (importance < 4)는 `status='archived'` (30일 임계 초과)
- **And** 행 A (importance >= 4)는 `status='active'` 유지 (60일 임계 미만)

## Module 3 — Memory Operations

### A3.1 memory_ops 단일 트랜잭션 실행 (REQ-MEM-03-2)

- **Given** Macro persona 응답 JSON에 `memory_ops` 배열 3건 (create 1, update 1, supersede 1) 포함
- **When** orchestrator가 응답 처리
- **Then** 3건 모두 단일 Postgres 트랜잭션으로 commit
- **And** 각 ops마다 audit_log에 'MEMORY_OP_*_OK' 이벤트 1건씩 = 총 3건 기록
- **And** 각 ops 결과의 source_refs에 호출의 persona_run_id 자동 첨부 확인

### A3.2 ownership 위반 거부 (REQ-MEM-03-3)

- **Given** Macro persona 응답에 `memory_ops`로 `table='micro_memory'` create 시도
- **When** orchestrator가 응답 처리
- **Then** 트랜잭션 ROLLBACK
- **And** audit_log에 'MEMORY_OP_OWNERSHIP_REJECT' 이벤트 기록 (payload에 persona_name='macro', target_table='micro_memory')
- **And** Telegram chat에 ownership 위반 알림 발송
- **And** persona_runs에는 응답 자체가 정상 기록 (의사결정 흐름은 지속)

### A3.3 부분 실패 시 ROLLBACK (REQ-MEM-03-2)

- **Given** memory_ops 4건 중 3번째에 importance=99 (CHECK 위반) 포함
- **When** orchestrator가 실행
- **Then** 트랜잭션 ROLLBACK — 1번째, 2번째 ops도 commit되지 않음
- **And** audit_log에 'MEMORY_OP_*_FAIL' 단일 이벤트 (트랜잭션 단위) 기록
- **And** Telegram 시스템 에러 알림 발송

### A3.4 모든 ops audit_log 기록 (REQ-MEM-03-4)

- **Given** memory_ops 1건 (archive)
- **When** 정상 실행
- **Then** audit_log에 event_type='MEMORY_OP_ARCHIVE_OK' 기록 + payload에 op 상세 + persona_run_id + target_id 포함

## Module 4 — Memory Injection

### A4.1 Macro persona input 구성 (REQ-MEM-04-1)

- **Given** `macro_context.md`, `macro_news.md` 파일 존재 + active macro_memory 25행 존재 (importance 1~5 다양)
- **When** Macro persona 호출
- **Then** input prompt에 macro_context.md 전문 + macro_news.md 전문 포함
- **And** macro_memory에서 importance >= 3, status='active', valid 윈도우 내 rows 중 importance DESC, updated_at DESC 정렬 후 상위 20건 포함
- **And** 25행 중 importance < 3 행은 제외 확인

### A4.2 토큰 캡 초과 시 우선순위 컷팅 (REQ-MEM-04-3)

- **Given** Macro persona input의 메모리 후보가 5,000 tokens (cap 4,000 초과)
- **When** Macro persona 호출
- **Then** importance DESC + last_accessed_at DESC 순으로 그리디 포함
- **And** 누적 4,000 tokens 도달 시 컷오프
- **And** 최종 input 토큰 카운트 ≤ 4,000 (Anthropic 토크나이저 검증)

### A4.3 last_accessed_at LRU 갱신 (REQ-MEM-04-4)

- **Given** macro_memory 행 A의 `last_accessed_at = '2026-04-01'`
- **When** Macro persona 호출 시 행 A가 input에 포함됨
- **Then** 호출 후 행 A의 `last_accessed_at = NOW()` (오늘 날짜) 로 갱신
- **And** 행 A에 대한 memory_ops가 응답에 없어도 LRU만 갱신됨 확인

### A4.4 Micro persona 워치리스트 + 섹터 필터 (REQ-MEM-04-2)

- **Given** 워치리스트 종목 5개 (각각 다른 섹터) + micro_memory 50행 (다양한 ticker/sector)
- **When** Micro persona 호출
- **Then** input에 포함되는 micro_memory 행은 워치리스트 5종목 + 그 5개 섹터에 해당하는 rows만 (그 외 종목/섹터 메모리는 제외)
- **And** 토큰 캡 ≤ 2,000

## Module 5 — Audit & Retrospective

### A5.1 일일 리포트 메모리 통계 한 줄 (REQ-MEM-05-3)

- **Given** macro_memory 활성 12건, micro_memory 활성 8건, 최근 7일 ops {created: 5, updated: 3, archived: 2, superseded: 1}
- **When** 16:00 KST 일일 리포트 생성
- **Then** 텔레그램 일일 리포트 본문에 정확히 한 줄 포함: `메모리 활성 20건 (M:12, U:8), 최근 7일 ops {created: 5, updated: 3, archived: 2, superseded: 1}`
- **And** 이 라인은 리포트 본문 끝부분에 위치

### A5.2 회고 페르소나 모순 후보 제안 (REQ-MEM-05-1, REQ-MEM-05-2)

- **Given** active macro_memory에 `(scope='global', kind='regime', summary='risk-on 우세')` 행과 `(scope='global', kind='regime', summary='risk-off 강화')` 행이 동시 존재 (모순 후보)
- **When** 일요일 Retrospective persona 호출
- **Then** 페르소나 input에 모순 후보 페어 1건 포함된 consistency report 전달
- **And** 페르소나 응답에 `memory_proposals` 항목이 포함되면 `retrospectives.memory_proposals` JSONB 컬럼에 저장
- **And** 자동으로 archive/supersede가 실행되지 않음 (REQ-MEM-05-2 사용자 검토 의무)
- **And** 박세훈 텔레그램 ack 수신 전까지 모순 메모리 그대로 유지

### A5.3 자동 hard-delete 금지 (REQ-MEM-05-4)

- **Given** macro_memory에 `status='archived'`, `last_accessed_at = NOW() - INTERVAL '365 days'` 행 존재
- **When** 어떤 자동 작업(retention sweep, retrospective, daily report) 실행
- **Then** 해당 행이 `DELETE` 되지 않고 `status='archived'` 상태 그대로 유지
- **And** 박세훈이 직접 `DELETE FROM macro_memory WHERE id=...` SQL 수동 실행한 경우에만 영구 삭제됨

## 통합 시나리오 (Cross-Module)

### A-INT-1 전체 흐름 — 컨텍스트 + 메모리 통합

- **Given** 06:00~06:45 cron 모두 정상 종료, `.md` 4종 최신 상태, active 메모리 30건
- **When** 07:30 KST Micro persona 호출 → 07:50 Decision → 08:00 Risk → 09:00 모의 매수
- **Then** Micro persona input에 micro_context.md + micro_news.md + 워치리스트 micro_memory top 20 (≤ 2,000 tokens) 포함 확인
- **And** Macro persona는 (다음 금요일까지) 캐시된 응답 사용, 별도 호출 없음
- **And** Decision/Risk persona는 본 SPEC 버전에서 메모리 직접 소비하지 않으므로 input 변화 없음 (orchestrator는 Micro 응답만 전달)
- **And** 모의 매수 1건 정상 체결, audit_log + persona_runs + 가능 시 micro_memory 신규 row 생성 확인

### A-INT-2 비용 영향 측정

- **Given** 본 SPEC 도입 후 1주 운영
- **When** 주간 토큰 비용 집계
- **Then** SPEC-001 baseline 대비 페르소나 호출당 input 토큰 평균 +1,500 (Macro+Micro)
- **And** 주간 비용 증분 ≤ +35% (목표 +30%, 마진 5%)
- **And** 비용 증분이 목표 초과 시 input cap 조정 ADR 기록

### A-INT-3 SPEC-001 회귀 부재

- **Given** 본 SPEC 도입 전후
- **When** SPEC-001의 acceptance.md 시나리오 전체 실행
- **Then** SPEC-001 시나리오 100% 통과 (회귀 0건)
- **And** 특히 SoD (REQ-RISK-04-7), 회로차단 (REQ-RISK-05-3), 한도 5종 (REQ-RISK-05-2) 동작 무영향 확인
