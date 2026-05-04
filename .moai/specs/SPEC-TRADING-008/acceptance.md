---
id: SPEC-TRADING-008
type: acceptance
version: 0.1.0
created: 2026-05-04
---

# SPEC-TRADING-008 — Acceptance Criteria

본 문서는 SPEC-TRADING-008 의 모듈별 인수 조건을 Given-When-Then 시나리오로 정의한다.

## Definition of Done (전체)

- [ ] DB 스키마 v9 마이그레이션 적용 (`persona_runs.cache_read_tokens`, `cache_write_tokens`, `source` 컬럼)
- [ ] 모든 페르소나/리포트 API 호출에 cache_control 마킹 적용
- [ ] `claude_cli.call_claude_cli` 모듈 구현 + 단위 테스트 통과 (timeout, fallback, audit_log)
- [ ] Daily Report / Macro News / Retrospective subprocess 전환 + API fallback 검증
- [ ] Dockerfile에 Node.js 22 + claude CLI 설치, compose.yaml에 `~/.claude` read-only mount
- [ ] `trading cost-report` CLI 명령 동작
- [ ] 월 누적 비용 200,000원 초과 시 텔레그램 알림 (silent_mode 무시)
- [ ] Phase A 1주 운영 + 캐시 적중률 ≥ 50% 검증
- [ ] Phase B 1주 운영 + subprocess 실패율 ≤ 5% 검증
- [ ] 비용 절감 실측 (Phase A 후, Phase B 후 각각 측정)

## Module 1 — Prompt Caching 시나리오

### Scenario 1.1 — 동일 페르소나 5분 내 2회 호출 시 캐시 히트
- **Given** Decision 페르소나가 09:30 정기 호출에서 응답을 받았고, 시스템 프롬프트와 Static Context가 변경되지 않은 상태
- **When** 11:00 정기 호출이 동일 페르소나로 다시 발생
- **Then** 응답의 `msg.usage.cache_read_input_tokens` 가 0보다 크고
- **And** `persona_runs.cache_read_tokens` 컬럼에 해당 값이 기록되어야 한다

### Scenario 1.2 — 마이그레이션 후 신규 컬럼 NOT NULL 운영
- **Given** DB 마이그레이션 v9가 적용된 상태
- **When** 신규 페르소나 호출 행이 `persona_runs` 에 INSERT
- **Then** `source` 컬럼은 `'api'` 또는 `'subprocess'` 중 하나여야 하고
- **And** `cache_read_tokens`, `cache_write_tokens` 는 INTEGER 또는 NULL이어야 한다

### Scenario 1.3 — 일일 리포트 캐시 적중률 표시
- **Given** 영업일 16:00 일일 리포트 생성 시점
- **When** 리포트가 텔레그램에 발송됨
- **Then** 비용 섹션에 "캐시 적중률: X.X%" 라인이 포함되어야 한다
- **And** X 값은 `sum(cache_read_tokens) / sum(input_tokens + cache_read_tokens) * 100` 으로 계산되어야 한다

### Scenario 1.4 — 시스템 프롬프트 변경 후 첫 호출 cache miss
- **Given** Decision 페르소나의 Jinja 템플릿이 수정되어 시스템 프롬프트 텍스트가 달라진 상태
- **When** 첫 호출이 발생
- **Then** `cache_read_input_tokens` 가 0이어야 하고 `cache_creation_input_tokens` 는 0보다 커야 한다
- **And** 이는 정상 동작으로 간주되며 system_error 알림을 발송하지 않는다

## Module 2 — Claude Code Subprocess 시나리오

### Scenario 2.1 — daily-report subprocess 정상 동작
- **Given** Phase B 운영 상태이고 컨테이너 내 `claude` CLI 인증이 유효
- **When** 영업일 16:00 일일 리포트 생성이 트리거됨
- **Then** subprocess `claude -p` 가 호출되어 리포트 텍스트를 반환하고
- **And** `persona_runs` (또는 `report_runs`) 에 `source='subprocess'`, `cost_krw=0`, `model='claude-code-cli'` 로 기록되어야 한다
- **And** 텔레그램에 일일 리포트가 정상 발송되어야 한다

### Scenario 2.2 — subprocess timeout 시 API fallback
- **Given** Macro News 요약 작업이 subprocess 모드로 실행 중
- **When** subprocess가 180초를 초과 (예: 인증 hang)
- **Then** 시스템은 SIGTERM 후 5초 grace, SIGKILL을 보내고
- **And** 동일 프롬프트로 Anthropic API fallback을 자동 실행하고
- **And** 결과는 `source='api'`, `cost_krw>0` 으로 기록되어야 한다

### Scenario 2.3 — subprocess + API 모두 실패 시 텔레그램 system_error
- **Given** Retrospective 페르소나가 subprocess 모드로 실행
- **When** subprocess가 non-zero exit으로 실패하고, 이어서 API fallback도 네트워크 오류로 실패
- **Then** 텔레그램에 `system_error` 알림이 발송되어야 한다 (silent_mode 무시)
- **And** `audit_log` 에 두 번의 실패 모두 기록되어야 한다

### Scenario 2.4 — Macro/Micro/Decision/Risk/Portfolio 페르소나는 subprocess 사용 금지
- **Given** Phase B 운영 상태
- **When** Decision 페르소나 호출이 발생
- **Then** 호출 경로는 항상 Anthropic API 직접 호출이어야 하고
- **And** `persona_runs.source` 는 `'api'` 여야 한다

### Scenario 2.5 — 컨테이너 내 claude CLI 인증
- **Given** Dockerfile + compose.yaml 변경 후 컨테이너 재빌드
- **When** `docker compose exec app claude --version` 실행
- **Then** 버전 출력이 정상적으로 반환되어야 한다
- **And** `docker compose exec app claude -p "say hi"` 호출이 비-API 인증 (Max 구독 OAuth) 으로 성공해야 한다

## Module 3 — Cost Monitoring 시나리오

### Scenario 3.1 — 월 누적 200,000원 초과 시 텔레그램 경고 (월 1회)
- **Given** 이번 달 누적 `cost_krw` 합계가 199,500원
- **When** 새 페르소나 호출로 누적이 200,001원에 도달
- **Then** 텔레그램에 비용 경고가 1회 발송되어야 한다 (silent_mode 무시)
- **And** 같은 달 내 두 번째 200,000원 초과 시점에는 추가 발송이 없어야 한다

### Scenario 3.2 — `trading cost-report --month 2026-05` 출력
- **Given** 2026년 5월 운영 데이터가 `persona_runs` 에 누적됨
- **When** 운영자가 `docker compose exec app trading cost-report --month 2026-05` 실행
- **Then** 출력에 페르소나별 호출 건수, input/output/cache 토큰, 캐시 적중률, subprocess 비율, 월 합계 KRW가 표 형식으로 표시되어야 한다

### Scenario 3.3 — 일일 리포트 비용 섹션 항목
- **Given** 영업일 16:00 일일 리포트 생성
- **When** 리포트가 발송됨
- **Then** 비용 섹션에 다음 5개 라인이 포함되어야 한다.
  - 오늘 합계 (KRW)
  - 이번 주 누적
  - 이번 달 누적
  - 캐시 적중률
  - subprocess 호출 건수

## Module 4 — Subprocess Wrapper 시나리오

### Scenario 4.1 — timeout 정확도
- **Given** `call_claude_cli(prompt, timeout=180)` 호출
- **When** subprocess가 응답 없이 hang
- **Then** 약 180초 시점에 SIGTERM이 도달하고 약 185초 시점에 SIGKILL이 도달해야 한다
- **And** orphan 프로세스 또는 zombie가 남지 않아야 한다 (`ps -ef | grep claude` 후 잔존 프로세스 0)

### Scenario 4.2 — audit_log에 prompt 평문 미저장
- **Given** `call_claude_cli` 호출
- **When** audit_log 행이 INSERT 됨
- **Then** `details` JSON에 `prompt_hash` (SHA-256, 64 hex char) 와 `prompt_bytes` (정수) 만 포함되어야 한다
- **And** 평문 prompt 텍스트는 어느 컬럼에도 저장되지 않아야 한다

### Scenario 4.3 — expect_json 모드 파싱 실패 처리
- **Given** `call_claude_cli(prompt, expect_json=True)` 호출
- **When** subprocess stdout이 JSON이 아닌 문자열
- **Then** 함수는 `JSONDecodeError` 를 발생시키거나 fallback_to_api=True 일 경우 API 호출로 전환해야 한다

### Scenario 4.4 — fallback_used 플래그 기록
- **Given** subprocess 실패 후 API fallback 성공
- **When** audit_log에 두 행이 기록됨
- **Then** subprocess 행의 `details.fallback_used = false` (or null), API 행의 `details.fallback_used = true` 로 표시되어야 한다

## Module 5 — Migration & Phasing 시나리오

### Scenario 5.1 — Phase A 적중률 < 50% 시 Phase B 진입 차단
- **Given** Phase A 운영 1주 종료 시점, 캐시 적중률 = 35%
- **When** Phase B 진입 결정 검토
- **Then** 시스템 (또는 운영 절차) 은 Phase B 자동 진입을 차단하고
- **And** 박세훈 님에게 텔레그램 알림 ("적중률 35%, 50% 미달 — Phase B 진입 보류") 을 발송해야 한다

### Scenario 5.2 — Phase B 실패율 > 5% 시 자동 rollback
- **Given** Phase B 운영 1주 종료 시점, subprocess 실패율 = 12%
- **When** 자동 검증 로직이 동작
- **Then** `COST_OPTIMIZATION_PHASE` config 플래그가 `'A'` 로 자동 변경되고
- **And** Daily Report / Macro News / Retrospective 호출 경로가 API 모드로 복귀하고
- **And** 박세훈 님에게 텔레그램 알림 ("Phase B rollback — 실패율 12%") 을 발송해야 한다

### Scenario 5.3 — Phase A 진입 직후 첫 호출은 cache miss 허용
- **Given** P1 (cache_control 마킹) 배포 직후, 캐시가 비어있는 상태
- **When** Decision 페르소나 첫 호출
- **Then** `cache_read_input_tokens=0`, `cache_creation_input_tokens>0` 이어도 정상으로 간주
- **And** system_error 알림을 발송하지 않는다

### Scenario 5.4 — Phase B 진입 후 5개 페르소나는 영향 없음
- **Given** Phase B 활성 상태
- **When** Macro/Micro/Decision/Risk/Portfolio 페르소나 호출이 발생
- **Then** 모두 Anthropic API 직접 호출 (prompt caching 적용) 로 동작하고
- **And** subprocess 호출은 발생하지 않아야 한다

## 운영 검증 지표

| 지표 | Phase A 목표 | Phase B 목표 |
|---|---|---|
| 캐시 적중률 (페르소나 호출 평균) | ≥ 50% | ≥ 60% |
| 월 비용 절감액 (vs SPEC-001 baseline) | ≥ 30% | ≥ 60% |
| subprocess 실패율 (Phase B만) | n/a | ≤ 5% |
| 일일 리포트 발송 성공률 | ≥ 99% | ≥ 99% |
| 월 비용 임계 경고 정확도 | 200,000원 도달 시 1회만 | 동일 |
