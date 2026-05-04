---
id: SPEC-TRADING-008
type: plan
version: 0.1.0
created: 2026-05-04
---

# SPEC-TRADING-008 — Implementation Plan

## 목표

박세훈 님 Claude Max 5x 구독을 활용하여 trading 시스템의 Anthropic API 월 비용을 30~50만원 → 5~10만원으로 절감한다. 절감 수단은 (1) Prompt Caching, (2) 단순 요약 작업의 Claude Code CLI subprocess 우회, (3) 사용량 가시화 3축이다.

## 사전 조건

- SPEC-TRADING-001 v0.2.0 구현 완료 (`persona_runs` 테이블 존재, `cost_krw` 컬럼 운영 중)
- SPEC-TRADING-007 구현 완료 (Static Context `.md` 파일 + memory_ops 운영 중)
- 박세훈 님 Claude Max 5x 구독 활성, `~/.claude/.credentials.json` 호스트에 존재
- Phase A 진입 전 박세훈 님이 prompt caching 영향 (시스템 프롬프트 변경 시 재캐싱 비용) 을 인지

## Implementation Phases

### Phase A — Prompt Caching (Primary Goal)

목표: API 호출 input 토큰 비용을 70~90% 할인. 캐시 적중률 50% 이상 달성.

**P1 — 페르소나 base 모듈 캐시 마킹**
- `src/trading/personas/base.py` 의 Anthropic message 빌드 부분 수정
- 시스템 프롬프트 블록에 `cache_control: {"type": "ephemeral"}` 추가
- Static Context (.md 로드 결과) 블록에 동일 마킹
- 활성 메모리 블록 (SPEC-007 산출물) 캐시 가능 여부 검토 후 적용
- 단위 테스트: 빌드된 message payload에 cache_control 키 존재 확인

**P2 — DB 스키마 v9 마이그레이션**
- `src/trading/db/migrations/V009__add_cache_columns.sql` 작성
- `persona_runs.cache_read_tokens` (INTEGER NULL), `cache_write_tokens` (INTEGER NULL), `source` (TEXT NOT NULL DEFAULT 'api') 추가
- 응답 처리 코드가 `msg.usage.cache_read_input_tokens` / `cache_creation_input_tokens` 를 신규 컬럼에 저장
- 기존 행은 NULL 유지

**P3 — 일일 리포트에 캐시 적중률 표시**
- `src/trading/reports/daily_report.py` 의 비용 섹션에 캐시 적중률 한 줄 추가
- 적중률 = sum(cache_read_tokens) / sum(input_tokens + cache_read_tokens)
- 적중률 50% 미만이면 경고 이모지 (텍스트로 "낮음")

**P3.5 — Phase A 검증**
- 1주 운영 후 박세훈 님과 비용/적중률 검토
- 적중률 50% 이상 + 절감액 실측 → Phase B 진입 승인

### Phase B — Claude Code Subprocess Fallback (Secondary Goal)

목표: Daily Report + Macro News + Retrospective 의 API 비용을 0원으로 (Max 구독 사용).

**P4 — claude_cli.py wrapper 모듈**
- `src/trading/utils/claude_cli.py` 신설
- 함수 `call_claude_cli(prompt, *, system, expect_json, timeout, fallback_to_api)` 구현
- subprocess.run 또는 asyncio subprocess 사용. timeout 도달 시 SIGTERM → 5초 → SIGKILL
- `expect_json=True` 시 stdout을 json.loads로 파싱
- audit_log 기록 (prompt SHA-256 hash, 평문 비저장)
- 단위 테스트: timeout, non-zero exit, JSON parse 실패, fallback 동작

**P5 — Dockerfile + compose.yaml 업데이트**
- Dockerfile에 Node.js 22 LTS 설치 단계 추가 (`apt-get install -y nodejs npm` 또는 NodeSource 공식 스크립트)
- `npm install -g @anthropic-ai/claude-code` 실행
- compose.yaml의 `app` 서비스에 volume 추가: `~/.claude:/home/app/.claude:ro`
- 컨테이너 user (1000:1000) 가 마운트된 credentials를 읽을 수 있는지 검증
- 컨테이너 안에서 `claude --version` 동작 확인

**P6 — Daily Report / Macro News / Retrospective 전환**
- `src/trading/reports/daily_report.py`: 기존 Anthropic API 호출을 `call_claude_cli` 로 교체. fallback_to_api=True
- `src/trading/data/news_adapter.py` (Macro News, SPEC-007 REQ-CTX-01-5) 동일 교체
- `src/trading/personas/retrospective.py` 동일 교체. 단, Retrospective 출력이 시스템 개선 제안 JSON 구조라면 `expect_json=True`
- 각 호출은 `persona_runs` 또는 `report_runs` 에 `source='subprocess'`, `cost_krw=0`, `model='claude-code-cli'` 기록

**P6.5 — Phase B 검증**
- 1주 운영 후 subprocess 실패율 측정
- 실패율 ≤ 5% → 영구 적용
- 실패율 > 5% → config 플래그로 API 모드 자동 rollback + 텔레그램 알림

### Phase C — Cost Monitoring (Supporting)

Phase A와 병행 가능.

**P7 — 월 비용 임계 경고**
- `src/trading/reports/daily_report.py` (또는 별도 cost_monitor 모듈) 에 월 누적 cost_krw 체크 로직
- 200,000원 처음 초과 시 텔레그램 알림 (silent_mode 무시, 월 1회 boolean 플래그)
- DB에 `cost_alerts_sent` 테이블 또는 system_state 키-값으로 발송 이력 기록

**P8 — `trading cost-report` CLI 명령**
- `src/trading/cli.py` 에 신규 subcommand 추가
- 인자: `--month YYYY-MM` (기본: 현재 월)
- 출력: 페르소나별 호출 건수, input/output/cache 토큰, 캐시 적중률, subprocess 비율, 월 합계 KRW
- 표 형식 (rich 라이브러리 또는 plain text)

## Risk Analysis

| 리스크 | 영향 | 대응 |
|---|---|---|
| Anthropic prompt cache 정책 변경 (TTL 단축, 가격 변경) | 절감액 감소, 캐시 무효 | 일일 리포트에 적중률 노출로 조기 감지. Anthropic 공식 채널 모니터링. |
| Claude Code CLI OAuth 토큰 만료 (refresh 실패) | subprocess 호출 일제 실패 | API fallback이 자동 처리. 텔레그램 알림으로 박세훈 님이 호스트에서 `claude` 재로그인. |
| 컨테이너 내 Node.js + claude CLI 설치로 이미지 크기 증가 | 빌드/배포 시간 증가 (~50MB) | 수용. multi-stage build 또는 별도 컨테이너 분리는 Future Scope. |
| 호스트 `~/.claude/.credentials.json` 마운트 보안 우려 | 컨테이너 침해 시 토큰 유출 | read-only mount, 비-root user. 컨테이너 외부 노출 포트 없음 (structure.md 보안 경계). |
| Anthropic ToS 모호성 (Max 구독 토큰을 컨테이너에서 사용) | 정책 위반 가능성 | 박세훈 님 본인 사용 + 1대 호스트 + 개인 자본 운용 → 일반적 사용 범위로 판단. ToS 변경 모니터링. |
| subprocess JSON 파싱 실패 (claude CLI 출력 변동) | Daily Report 일시 누락 | API fallback 자동 동작. JSON 강제 출력은 시스템 프롬프트로 강하게 지시. |
| 시스템 프롬프트 변경 시 첫 호출 cache miss | 일시적 비용 증가 (수십원) | 정상 동작으로 간주. 일일 리포트 적중률에 자연 반영. |
| Phase A 적중률이 50% 미달 | Phase B 진입 차단 | 박세훈 님 검토 후 캐시 블록 재구성 또는 Phase B 강행 결정. |

## Reference

- Anthropic Prompt Caching: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- Claude Code CLI: https://docs.claude.com/en/docs/claude-code
- SPEC-TRADING-001 v0.2.0 (5-페르소나 매매 시스템)
- SPEC-TRADING-007 (Static Context + memory_ops)
- 박세훈 님 운영 메모: Max 5x 구독 활성, ANTHROPIC_API_KEY는 별도 청구

## 검증 책임

- Phase A → Phase B 전환 결정: 박세훈 님 + 운영자
- Phase B subprocess 실패율 측정: 자동화 (Phase B 1주 후 cost-report --month 출력 검토)
- 월 비용 임계 경고: 시스템 자동 (silent_mode 무시)
- ToS / 정책 변경 감지: 박세훈 님 수동 모니터링 (자동화는 Future Scope)
