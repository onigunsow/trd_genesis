# SPEC-TRADING-009 Acceptance Criteria

## Module 1 — Tool Registry & Function Calling Infrastructure

### Scenario M1-1: Tool Registry 등록 및 스키마 유효성

**Given** `src/trading/tools/registry.py`가 구현되어 있을 때
**When** `get_all_tool_definitions()`를 호출하면
**Then** Anthropic API `tools` parameter 형식에 맞는 JSON Schema 목록을 반환한다:
- 최소 10개 tool 정의가 포함됨
- 각 tool은 `name`, `description`, `input_schema` 필드를 가짐
- `input_schema`는 유효한 JSON Schema (Draft 7+)
- `description`은 한국어로 50자 이내

### Scenario M1-2: Tool 실행 성공 케이스

**Given** `get_ticker_technicals` tool이 등록되어 있고 DB에 삼성전자(005930) OHLCV 150일치가 캐시되어 있을 때
**When** `execute_tool("get_ticker_technicals", {"ticker": "005930", "lookback_days": 150})`을 호출하면
**Then** 5초 이내에 `{"close": ..., "ma20": ..., "ma60": ..., "rsi14": ..., "vs_ma20_pct": ...}` 형태의 dict를 반환한다
**And** `tool_call_log` 테이블에 `success=true`, `execution_ms < 5000` 행이 기록된다

### Scenario M1-3: Tool 타임아웃 처리

**Given** `get_macro_indicators` tool이 등록되어 있고 DB 연결이 지연(> 5초)될 때
**When** tool 호출이 5초 타임아웃에 도달하면
**Then** `{"error": "timeout", "tool": "get_macro_indicators"}` 형태의 error response가 반환된다
**And** `tool_call_log` 테이블에 `success=false`, `error='timeout'` 행이 기록된다
**And** 전체 persona invocation은 crash하지 않고 계속 진행된다

### Scenario M1-4: Tool 예외 처리

**Given** `get_recent_disclosures` tool 호출 중 DB connection error가 발생할 때
**When** 예외가 raise되면
**Then** `{"error": "ConnectionError", "message": "..."}` 형태의 error response가 LLM에게 반환된다
**And** LLM은 해당 데이터 없이 분석을 계속 진행한다

### Scenario M1-5: Tool 호출 audit 기록

**Given** 임의의 tool이 호출될 때
**When** 호출이 완료되면 (성공/실패 무관)
**Then** `tool_call_log` 테이블에 행이 INSERT된다:
- `persona_run_id`: 현재 페르소나 호출 ID
- `tool_name`: 호출된 tool 이름
- `input_hash`: 입력 파라미터의 SHA-256 해시
- `execution_ms`: 실행 시간
- `success`: true/false
- `result_bytes`: 결과 크기 (성공 시)

### Scenario M1-6: Tool에서 LLM 호출 금지

**Given** tool 함수 구현체가 있을 때
**When** 코드를 정적 분석하면
**Then** 어떤 tool 함수도 `anthropic.Anthropic`, `call_persona`, 또는 다른 LLM 호출을 포함하지 않는다

---

## Module 2 — Persona Tool-calling Integration

### Scenario M2-1: call_persona Tool-use Loop 정상 동작

**Given** `TOOL_CALLING_ENABLED=true`이고 Micro 페르소나가 `get_ticker_technicals`, `get_watchlist` tools를 가지고 있을 때
**When** Micro 페르소나를 호출하면
**Then** Anthropic API 응답에서 `stop_reason="tool_use"` 시 해당 tool을 실행하고 `tool_result`를 append하여 재전송한다
**And** 최종 `stop_reason="end_turn"` 시 정상 응답을 반환한다
**And** `persona_runs.tool_calls_count`에 실제 호출 횟수가 기록된다

### Scenario M2-2: Tool round 제한 (8회 초과 방지)

**Given** 페르소나가 tool을 반복 호출하여 8회를 초과할 때
**When** 9번째 tool round에 진입하려 하면
**Then** 루프를 강제 종료하고 마지막 가용 text response를 사용한다
**And** `audit_log` event `TOOL_LOOP_EXCEEDED`가 기록된다
**And** Telegram에 경고 알림이 발송된다

### Scenario M2-3: Macro 페르소나 Tool 할당

**Given** `TOOL_CALLING_ENABLED=true`이고 Macro 페르소나가 호출될 때
**When** API 요청의 `tools` 파라미터를 확인하면
**Then** 다음 tool만 포함되어 있다: `get_macro_indicators`, `get_global_assets`, `get_static_context`, `get_active_memory`
**And** Micro 전용 tool (`get_ticker_technicals`, `get_ticker_flows` 등)은 포함되지 않는다

### Scenario M2-4: Decision 페르소나 Tool 할당

**Given** `TOOL_CALLING_ENABLED=true`이고 Decision 페르소나가 호출될 때
**When** Decision이 특정 종목에 대한 추가 정보가 필요하여 `get_ticker_fundamentals`를 호출하면
**Then** 해당 종목의 펀더멘털 데이터가 tool_result로 전달된다
**And** Decision의 최종 시그널에 해당 데이터가 반영된다

### Scenario M2-5: Token 절감 검증

**Given** Micro 페르소나를 bulk-injection 모드와 tool-calling 모드로 각각 실행할 때
**When** 두 모드의 `persona_runs.input_tokens`를 비교하면
**Then** tool-calling 모드의 총 토큰 (input + tool_input + tool_output) ≤ bulk-injection 모드의 80%

### Scenario M2-6: Tool schema 캐싱 (SPEC-008 호환)

**Given** `TOOL_CALLING_ENABLED=true`이고 동일 페르소나를 5분 내 2회 호출할 때
**When** 두 번째 호출의 `persona_runs.cache_read_tokens`를 확인하면
**Then** system prompt + tool definitions 부분이 cache hit되어 `cache_read_tokens > 0`

### Scenario M2-7: 일일 리포트 Tool 사용량 포함

**Given** 영업일에 Tool-calling 모드로 전체 사이클이 실행된 후 16:00 일일 리포트가 생성될 때
**When** 리포트 내용을 확인하면
**Then** `"Tool 호출: 총 X회, 평균 Y회/페르소나, 실패 Z건"` 라인이 포함되어 있다

---

## Module 3 — Risk REJECT Reflection Loop

### Scenario M3-1: Reflection Loop 정상 동작 (1회 재시도 성공)

**Given** `REFLECTION_LOOP_ENABLED=true`이고 Decision이 시그널을 생성했을 때
**When** Risk가 `verdict="REJECT"`, `rationale="종목 집중도 초과"`, `concerns=["섹터 편중 60%"]`로 응답하면
**Then** orchestrator가 Decision을 재호출한다:
- `rejection_feedback.round = 1`
- `rejection_feedback.risk_rationale` = "종목 집중도 초과"
- `rejection_feedback.risk_concerns` = ["섹터 편중 60%"]
**And** Decision이 수량을 줄인 revised signal을 반환하면
**And** Risk가 revised signal에 `verdict="APPROVE"`로 응답하면
**Then** code-rule check → 매매 실행으로 진행된다
**And** `reflection_rounds` 테이블에 `round_number=1`, `final_verdict='APPROVE'` 행이 기록된다
**And** Telegram에 `"[Risk → REJECT → Reflection Round 1 → APPROVE]"` 브리핑이 발송된다

### Scenario M3-2: Reflection Loop 2회 재시도 후 최종 REJECT

**Given** `REFLECTION_LOOP_ENABLED=true`이고 Decision 시그널이 Risk에 의해 REJECT됐을 때
**When** Round 1 재시도: Decision revised → Risk REJECT (여전히 문제)
**And** Round 2 재시도: Decision revised-2 → Risk REJECT (최종 거부)
**Then** 해당 시그널은 최종 rejected 처리된다
**And** `reflection_rounds` 테이블에 2행이 기록된다 (round 1, round 2)
**And** Telegram에 최종 REJECT 브리핑이 발송된다
**And** 더 이상의 재시도는 없다 (3번째 Round 불가)

### Scenario M3-3: Decision이 Reflection 중 철회 (Withdrawal)

**Given** Reflection Round 1에서 Decision이 재호출됐을 때
**When** Decision이 `signals: []` (빈 배열) 또는 `withdraw: true`로 응답하면
**Then** 즉시 Reflection Loop가 종료된다
**And** `reflection_rounds.final_verdict = 'WITHDRAWN'`으로 기록된다
**And** `audit_log` event `REFLECTION_WITHDRAWN`이 기록된다
**And** Risk 재호출은 발생하지 않는다

### Scenario M3-4: Reflection Loop 타임아웃

**Given** Reflection Round 진행 중 Decision 재호출 + Risk 재호출 합산이 30초를 초과할 때
**When** 타임아웃에 도달하면
**Then** Reflection Loop가 중단된다
**And** 원래 REJECT verdict가 유지된다 (시그널 거부)
**And** `audit_log` event `REFLECTION_TIMEOUT`이 기록된다
**And** Telegram 알림이 발송된다

### Scenario M3-5: Risk의 SoD 독립성 보존

**Given** Reflection Round에서 Risk가 revised signal을 평가할 때
**When** Risk에게 전달되는 input을 확인하면
**Then** input에 "이것은 수정된 시그널입니다" 또는 "reflection round입니다"와 같은 메타정보가 포함되어 있지 않다
**And** Risk는 기존과 동일한 입력 구조 (`decision_signals`, `assets`, `limits`)만 받는다

### Scenario M3-6: 동일 사이클 내 중복 Reflection 방지

**Given** Pre-market 사이클에서 Decision이 3개 시그널을 생성했을 때
**When** 시그널 A에 대해 Reflection Loop가 진행 중일 때
**Then** 시그널 B, C에 대한 Reflection은 시그널 A의 Loop 완료 후 순차적으로 처리된다
**And** 병렬 Reflection은 발생하지 않는다

### Scenario M3-7: Reflection 일일 리포트 통계

**Given** 영업일에 Reflection이 발생한 후 일일 리포트가 생성될 때
**When** 리포트 내용을 확인하면
**Then** `"Reflection: 시도 X건, 성공(APPROVE) Y건, 최종 REJECT Z건, 철회 W건"` 라인이 포함된다
**And** X = Y + Z + W (합산 일치)

### Scenario M3-8: Reflection 비활성 시 기존 동작 유지

**Given** `REFLECTION_LOOP_ENABLED=false` (기본값) 일 때
**When** Risk가 REJECT를 반환하면
**Then** 즉시 시그널이 rejected 처리된다 (기존 동작)
**And** Decision 재호출은 발생하지 않는다
**And** `reflection_rounds` 테이블에 아무 행도 INSERT되지 않는다

---

## Module 4 — Backward Compatibility & Migration

### Scenario M4-1: Feature Flag 기본값 확인

**Given** 신규 배포 후 DB migration v10이 적용됐을 때
**When** `system_state` 테이블을 조회하면
**Then** `tool_calling_enabled = false`
**And** `reflection_loop_enabled = false`
**And** 모든 기존 기능이 정상 동작한다 (bulk injection + 즉시 reject)

### Scenario M4-2: Tool-calling Feature Flag 전환

**Given** `tool_calling_enabled=false` 상태에서
**When** 박세훈이 Telegram에서 `/tool-calling on`을 전송하면
**Then** 5초 이내에 `system_state.tool_calling_enabled = true`로 변경된다
**And** `audit_log` event `TOOL_CALLING_ACTIVATED`가 기록된다
**And** Telegram 확인 메시지가 발송된다
**And** 이후 페르소나 호출은 Tool-calling 모드로 실행된다

### Scenario M4-3: Tool-calling Fallback 동작

**Given** `TOOL_CALLING_ENABLED=true`이고 페르소나 호출 중 tool이 3회 연속 실패할 때
**When** 3번째 tool 실패가 감지되면
**Then** 해당 페르소나 호출은 자동으로 bulk injection 모드로 fallback된다
**And** `audit_log` event `TOOL_FALLBACK_TRIGGERED`가 기록된다
**And** 전역 feature flag는 변경되지 않는다 (per-invocation fallback)
**And** 다음 페르소나 호출은 다시 tool-calling을 시도한다

### Scenario M4-4: context.py 함수 보존

**Given** Phase A~C (Tool Registry 배포 후 2주) 기간 동안
**When** `src/trading/personas/context.py`의 `assemble_macro_input()`, `assemble_micro_input()` 함수를 확인하면
**Then** 함수가 삭제되지 않고 존재한다
**And** fallback 경로에서 정상 호출 가능하다

### Scenario M4-5: Phase별 점진적 활성화

**Given** Phase B (Micro only) 단계에서
**When** Micro 페르소나가 Tool-calling으로 실행될 때
**Then** Macro, Decision, Risk 페르소나는 여전히 bulk injection으로 실행된다
**And** Micro의 tool_calls_count > 0, Macro/Decision/Risk의 tool_calls_count = 0

### Scenario M4-6: Latency 초과 시 자동 fallback

**Given** `TOOL_CALLING_ENABLED=true`이고 tool-calling 모드의 latency가 3회 연속 `현재 평균 + 5초`를 초과할 때
**When** 3번째 초과가 감지되면
**Then** 자동으로 해당 페르소나의 다음 호출은 bulk injection으로 fallback된다
**And** Telegram 경고가 발송된다

---

## Non-Functional Requirements 검증

### Scenario NFR-1: Token 절감 목표 달성

**Given** Tool-calling 모드로 1주간 운영 후
**When** Micro 페르소나의 평균 input token을 측정하면
**Then** bulk-injection 대비 ≤ 80% (기존 ~6500 tok → tool-calling ≤ 5200 tok 총합)

### Scenario NFR-2: Latency 목표 달성

**Given** Tool-calling 모드로 10회 이상 full cycle 실행 후
**When** 각 페르소나의 end-to-end latency를 측정하면
**Then** 기존 bulk-injection 대비 +5초 이내
**And** 전체 Pre-market cycle (Micro→Decision→Risk) ≤ 60초

### Scenario NFR-3: Reflection Loop 비용 제어

**Given** Reflection Loop 활성화 후 1개월 운영 후
**When** reflection 관련 `persona_runs`의 `cost_krw` 합계를 계산하면
**Then** ≤ 50,000원/월

### Scenario NFR-4: 일일 리포트 Observability

**Given** 영업일 16:00 일일 리포트 생성 시
**When** SPEC-008 비용 모니터링 섹션을 확인하면
**Then** 다음 항목이 추가되어 있다:
- `tool_calls_total`: 당일 전체 tool 호출 수
- `tool_failures`: 당일 tool 실패 수
- `reflection_rounds`: 당일 reflection 시도 수
- `reflection_success_rate`: reflection 성공률 (%)

---

## Quality Gates

### Coverage 요건

| 모듈 | 최소 coverage | 비고 |
|---|---|---|
| `src/trading/tools/registry.py` | 100% | Tool schema 정확성 critical |
| `src/trading/tools/executor.py` | 100% | Timeout/fallback logic critical |
| `src/trading/tools/market_tools.py` | 85% | 기존 context.py wrapper |
| `src/trading/personas/base.py` (tool loop) | 100% | Multi-round loop critical |
| `src/trading/personas/orchestrator.py` (reflection) | 100% | Reflection flow critical |
| `src/trading/tools/fallback.py` | 100% | Fallback 경로 critical |

### Integration Test 요건

- [ ] Full Pre-market cycle (Tool-calling ON + Reflection ON) → 매매 실행 성공
- [ ] Full Pre-market cycle (Tool-calling OFF + Reflection OFF) → 기존 동작 동일
- [ ] Tool 3회 연속 실패 → Fallback → bulk injection으로 정상 완료
- [ ] Reflection 2회 후 최종 REJECT → 시그널 폐기 + audit 완전
- [ ] Feature flag 전환 (Telegram 명령) → 즉시 반영 확인
- [ ] SPEC-008 Prompt Caching + Tool-calling 공존 → cache hit rate ≥ 50%

### Performance Benchmarks

- [ ] Tool 개별 실행: p95 ≤ 2초
- [ ] Persona with tool-calling: p95 ≤ 15초 (전체 multi-round)
- [ ] Reflection single round: p95 ≤ 12초
- [ ] Full cycle (Micro+Decision+Risk+Reflection): p95 ≤ 45초

---

## Definition of Done

1. 모든 Module 1~4의 REQ가 구현 완료
2. 위 acceptance criteria의 모든 시나리오가 PASS
3. Coverage 요건 충족 (critical modules 100%)
4. Integration tests 전체 PASS
5. Performance benchmarks 충족
6. Phase A (Tool Registry deploy, flags OFF) 배포 완료 + 1주 안정 운영
7. DB migration v10 적용 완료
8. 일일 리포트에 tool usage + reflection 통계 노출 확인
9. Telegram `/tool-calling`, `/reflection` 명령어 동작 확인
10. SPEC-008 Prompt Caching과 공존 확인 (cache hit rate 유지)
