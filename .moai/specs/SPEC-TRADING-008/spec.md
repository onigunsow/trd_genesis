---
id: SPEC-TRADING-008
version: 0.1.0
status: draft
created: 2026-05-04
updated: 2026-05-04
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: Cost Optimization — Prompt Caching + Claude Code Subprocess + Cost Monitoring
related_specs:
  - SPEC-TRADING-001
  - SPEC-TRADING-007
---

# SPEC-TRADING-008 — 운영 비용 최적화

## HISTORY

| 일자 | 버전 | 변경 | 작성자 |
|---|---|---|---|
| 2026-05-04 | 0.1.0 | 초안 — Prompt Caching + Claude Code subprocess fallback + Cost Monitoring (5 모듈) | onigunsow |

## 범위 요약

SPEC-TRADING-001 v0.2.0과 SPEC-TRADING-007이 5-페르소나 시스템 기능을 정의했다면, 본 SPEC은 **운영 비용 최적화** 전담이다. 박세훈 님이 보유한 Claude Max 5x 구독을 최대한 활용하면서 별도 청구되는 Anthropic API 비용을 50~70% 절감하는 것을 목표로 한다.

핵심 수치:

- 현재 예상 월 비용 (M5+ 운영): 30~50만원
- 목표 월 비용: 5~10만원
- 절감 동력: (1) Prompt Caching, (2) Claude Code subprocess 우회, (3) 사용량 가시화

본 SPEC은 신규 페르소나나 매매 로직을 추가하지 않는다. 기존 호출 경로의 비용 효율만 개선한다.

## 환경

- **박세훈 님 Claude Max 5x 구독 활성** — Claude Code CLI 호출은 구독으로 커버되며 추가 과금 없음 (fixed monthly cost)
- Anthropic API 호출은 별도 청구 — 본 SPEC의 절감 대상
- `~/.claude/.credentials.json` 호스트에 존재 — Claude Code CLI OAuth 인증 파일
- 컨테이너 (Python 3.13-slim) 에 Node.js 22+ 추가 가능 (Dockerfile 변경 필요)
- 인터넷/정책 모니터링 필요 — Anthropic prompt caching 정책, Claude Code CLI 업데이트, 가격 변경

## 가정

- Anthropic prompt cache 5분 또는 1시간 TTL 정책이 본 SPEC 운영 기간 동안 유지된다.
- Claude Code CLI는 Max 구독 인증으로 OAuth 토큰을 사용하며 `ANTHROPIC_API_KEY` 환경변수와 충돌하지 않는다 (인증 우선순위 별도 검증 필요).
- 컨테이너에서 호스트 `~/.claude/.credentials.json` read-only mount가 Anthropic ToS 위반이 아니다 (개인 사용 + 1대 호스트 + Max 구독자 본인 사용).
- subprocess timeout 180초로 단순 요약 작업 (Daily Report, Macro News, Retrospective) 처리에 충분하다.
- 페르소나 호출 간격 (5~30분) 안에 prompt cache TTL이 만료되지 않아 캐시 히트율 70% 이상을 달성한다.

## 요구사항 (EARS)

본 SPEC은 5개 모듈로 구성된다.

### Module 1 — Prompt Caching (REQ-CACHE-01-*)

- **REQ-CACHE-01-1 [U]**: 시스템은 모든 페르소나 호출 (Macro/Micro/Decision/Risk/Portfolio/Retrospective) 과 Daily Report 호출에서 시스템 프롬프트와 Static Context 블록을 `cache_control: {"type": "ephemeral"}` 으로 마킹한다.
- **REQ-CACHE-01-2 [U]**: 시스템은 캐시 가능 블록을 다음 우선순위로 구성한다.
  1. 시스템 프롬프트 (Jinja 템플릿 렌더 결과, ~1500~3000 tok)
  2. Static Context (`.md` 도메인 컨텍스트 파일들, ~3000~5000 tok)
  3. 활성 메모리 블록 (~500~1500 tok, SPEC-007 정의)
  4. 워치리스트 + 펀더멘털 표 (변동 적은 정적 데이터)
  당일 동적 데이터 (분 단위 시세, 신규 공시 등) 는 캐시 마킹하지 않는다.
- **REQ-CACHE-01-3 [E]**: WHEN 페르소나/리포트 호출 응답이 도착할 때, THEN 시스템은 `msg.usage.cache_read_input_tokens` 와 `msg.usage.cache_creation_input_tokens` 값을 `persona_runs.cache_read_tokens`, `persona_runs.cache_write_tokens` 컬럼에 저장한다.
- **REQ-CACHE-01-4 [U]**: 시스템은 캐시 적중률을 `cache_read_tokens / (input_tokens + cache_read_tokens)` 로 정의하고 일일 리포트에 한 줄을 추가한다 ("캐시 적중률: X%").
- **REQ-CACHE-01-5 [N]**: 시스템 프롬프트나 Static Context가 변경되면 첫 호출은 cache miss가 자연 발생한다. 이를 오류로 간주하지 않는다.

### Module 2 — Claude Code Subprocess Fallback (Future Scope, 본 SPEC v0.1에서 구현 X)

**박세훈 님 결정 (2026-05-04)**: 본 SPEC은 **Phase A (Prompt Caching)만 구현**한다. Phase B (subprocess) 는 다음 사유로 Future Scope로 이동:

1. **Anthropic ToS 회색지대** — Max 5x 구독의 OAuth 토큰을 자동화 시스템에서 사용하는 것이 Anthropic 약관의 *"reasonable use"* 조항 적합성 불명. 구독 한도(3시간당 메시지 제한) 도달 시 시스템 정지 위험.
2. **Phase A 만으로 50~70% 절감 충분** — 월 30~50만원 → 5~10만원. Phase B의 추가 5~10K 절감은 ToS·운영 위험 대비 효용 작음.
3. **Anthropic 정책 변경 모니터링 필요** — Phase B 도입 전 Anthropic의 Claude Code CLI 자동화 사용 정책이 명시화되어야 함.

본 SPEC은 Module 2의 모든 REQ-CCSUB-02-*, REQ-WRAP-04-*, REQ-PHASE-05-3/4 를 **삭제 (Future Scope로 이동)**. 운영 1년 후 또는 Anthropic 정책 명확화 후 SPEC-TRADING-008 v0.2 또는 별도 SPEC으로 재검토.

### Module 3 — Cost Monitoring (REQ-COSTM-03-*)

- **REQ-COSTM-03-1 [U]**: 시스템은 일일 리포트 비용 섹션에 다음 항목을 포함한다. 오늘 합계 (KRW), 이번 주 누적, 이번 달 누적, 캐시 적중률, subprocess 호출 건수.
- **REQ-COSTM-03-2 [E]**: WHEN 월 누적 `cost_krw` 합계가 200,000원을 처음으로 초과 THEN 시스템은 텔레그램에 비용 경고를 발송한다 (silent_mode 무시, 월 1회만).
- **REQ-COSTM-03-3 [U]**: 시스템은 신규 CLI 명령 `trading cost-report [--month YYYY-MM]` 를 제공한다. 출력은 페르소나별 호출 건수, 입력/출력/캐시 토큰 합계, 캐시 적중률, subprocess 사용 비율, 월 합계 KRW를 표 형식으로 표시한다.

### Module 4 — Subprocess Wrapper Module (REQ-WRAP-04-*)

- **REQ-WRAP-04-1 [U]**: 시스템은 `src/trading/utils/claude_cli.py` 모듈을 신설한다. 공개 함수 시그니처: `call_claude_cli(prompt: str, *, system: str | None = None, expect_json: bool = False, timeout: int = 180, fallback_to_api: bool = True) -> str`.
- **REQ-WRAP-04-2 [E]**: WHEN subprocess가 timeout(`timeout` 인자) 에 도달 THEN 시스템은 SIGTERM을 보내고 5초 대기 후 SIGKILL을 보낸다. 자원 누수 (orphan process, zombie) 를 방지한다.
- **REQ-WRAP-04-3 [U]**: 시스템은 모든 `call_claude_cli` 호출을 `audit_log` 에 기록한다. 항목: `subject='claude_cli'`, `actor='subprocess'`, `details={prompt_hash, prompt_bytes, exit_code, duration_ms, output_bytes, fallback_used}`. 평문 prompt는 저장하지 않고 SHA-256 hash만 저장한다.
- **REQ-WRAP-04-4 [O]**: WHERE `expect_json=True` 인 호출은 stdout을 `json.loads` 로 파싱하고 실패 시 `JSONDecodeError`를 발생시킨다 (호출자가 fallback 처리).

### Module 5 — Migration & Phasing (REQ-PHASE-05-*)

- **REQ-PHASE-05-1 [U]**: Phase A (즉시 시행) 는 Module 1 (Prompt Caching) 을 6개 페르소나 + Daily Report 호출 경로에 적용한다. Phase A는 1주간 운영 검증을 거친다.
- **REQ-PHASE-05-2 [E]**: WHEN Phase A 운영 1주가 경과하고 캐시 적중률이 50% 이상 THEN 시스템은 Phase B (subprocess 전환) 진입을 허용한다. IF 캐시 적중률 < 50% THEN 박세훈 님에게 텔레그램 알림을 보내고 Phase B를 차단한다.
- **REQ-PHASE-05-3 [U]**: Phase B는 Daily Report + Macro News + Retrospective 작업을 subprocess 모드로 전환한다. Phase B도 1주간 운영 검증을 거친다.
- **REQ-PHASE-05-4 [E]**: WHEN Phase B 1주 경과 + subprocess 실패율(실패/총호출) ≤ 5% THEN 시스템은 영구 적용 상태로 전환한다. IF 실패율 > 5% THEN 자동으로 API 모드로 rollback하고 박세훈 님에게 텔레그램 알림을 보낸다.

### Future Scope (본 SPEC 범위 외)

- Macro 페르소나 Opus 4.7 → Sonnet 4.6 다운그레이드 (월 ~2.5만원 절감, 매크로 깊이 trade-off — 박세훈 님 별도 의사결정 필요)
- 장중 정기 호출 4회 → 2회 축소 (운영 결과 본 후 SPEC-001 변경)
- Anthropic Batch API 도입 (백테스트 보조용 비실시간 LLM)
- Anthropic 정책 변경 모니터링 자동화 (prompt cache TTL/가격 변경 시 알림)

## Specifications

### 데이터 스키마 변경

`persona_runs` 테이블 (M4 도입, SPEC-001) 에 컬럼 추가 — DB 마이그레이션 v9.

| 컬럼 | 타입 | 비고 |
|---|---|---|
| `cache_read_tokens` | `INTEGER` NULL | `msg.usage.cache_read_input_tokens` |
| `cache_write_tokens` | `INTEGER` NULL | `msg.usage.cache_creation_input_tokens` |
| `source` | `TEXT NOT NULL DEFAULT 'api'` | `'api' | 'subprocess'` |

기존 행은 NULL 허용. subprocess 호출 행은 `cost_krw=0`, `source='subprocess'`, `model='claude-code-cli'`.

### 의존 SPEC

본 SPEC은 SPEC-TRADING-001 v0.2.0 (5-페르소나 + persona_runs 스키마) 와 SPEC-TRADING-007 (Static Context + memory_ops) 의 구현이 완료된 이후 적용된다. 두 SPEC 미완료 상태에서 본 SPEC을 우선 적용하지 않는다.

### Traceability

| 모듈 | 코드 위치 (예정) | 참조 SPEC |
|---|---|---|
| Module 1 (Prompt Caching) | `src/trading/personas/base.py`, `src/trading/personas/orchestrator.py` | SPEC-001 M4, SPEC-007 |
| Module 2 (Subprocess) | `src/trading/reports/daily_report.py`, `src/trading/data/news_adapter.py`, `src/trading/personas/retrospective.py` | SPEC-001 M5 |
| Module 3 (Cost Monitoring) | `src/trading/reports/daily_report.py`, `src/trading/cli.py` | SPEC-001 M5 |
| Module 4 (Wrapper) | `src/trading/utils/claude_cli.py` (신규) | — |
| Module 5 (Phasing) | `src/trading/config.py` (`COST_OPTIMIZATION_PHASE` 플래그) | — |

## 비목표

- 페르소나 시스템 프롬프트 변경 (별도 SPEC 또는 박세훈 페르소나 정밀화 워크에서 다룸)
- 매매 로직 변경
- 신규 데이터 소스 추가
- 페르소나 모델 변경 (Opus → Sonnet 등은 Future Scope)
- 비-Anthropic LLM 도입 (단일 벤더 일관성 유지, tech.md 거버넌스)
