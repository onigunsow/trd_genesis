# SPEC-TRADING-009 Research — AI/LLM Architecture Upgrade

## 연구 근거 요약

### 1. AlphaQuanter Paper — Acquire-Reason-Act Loop

**핵심 아이디어**: 단일 LLM 에이전트가 "정보 공백(information gap)"을 스스로 식별하고, Tool을 호출하여 능동적으로 필요한 데이터만 수집한 후 추론에 집중하는 패턴.

**3단계 루프**:
1. **Acquire**: 현재 분석에 필요한 정보가 무엇인지 판단 → Tool call로 조회
2. **Reason**: 수집된 데이터로 분석 수행 (불필요한 정보 없이 집중)
3. **Act**: 분석 결과를 기반으로 최종 결정/시그널 출력

**본 시스템에의 적용**:
- 현재 상태: `context.py`가 모든 가능한 데이터를 bulk-load하여 ~5000+ token 주입
- 목표 상태: 페르소나가 "이 종목의 수급이 궁금하다" → `get_ticker_flows("005930", 5)` 호출 → 필요한 것만 조회
- 기대 효과: input token ~80% 절감 (5000 → 1000 base + 200~500 per tool call on-demand)

### 2. FinCon Paper — Multi-agent Debate & Verbal Reinforcement

**핵심 아이디어**: 금융 의사결정에서 다수 에이전트가 서로의 출력을 검토하고 반박(debate)하여 최종 결론을 도출. 단순 majority vote보다 "근거 기반 반박 + 재고" 패턴이 우수.

**Self-reflection 메커니즘**:
- Agent A가 결정 → Agent B가 반박(rejection with reason) → Agent A가 반박을 수용/반영하여 수정안 제출 → Agent B 재평가
- "Conceptual verbal reinforcement": 거부 사유를 자연어로 구체적으로 전달하면 수정 품질이 향상됨

**본 시스템에의 적용**:
- 현재 상태: Decision → Risk → REJECT → signal discarded (orchestrator.py line 253-254)
- 목표 상태: Decision → Risk → REJECT(reason) → Decision(revise with feedback) → Risk(re-evaluate)
- 기대 효과: 오판 REJECT 감소, 매매 품질 향상 (Risk의 우려를 반영한 수정 시그널)

### 3. TradingAgents Paper — Debate in Financial Multi-Agent Systems

**추가 근거**: TradingAgents 연구에서 "Bull Analyst vs Bear Analyst" 구조의 토론이 단일 에이전트 대비 일관되게 더 나은 의사결정을 산출. 단, 토론 라운드가 3회를 초과하면 convergence가 아닌 oscillation 발생 → 본 SPEC에서 최대 2회 제한의 근거.

---

## 코드베이스 분석 (Codebase Research)

### 1. 현재 Static Context Injection 패턴

**파일**: `src/trading/personas/context.py`

현재 `assemble_macro_input()` 및 `assemble_micro_input()` 함수가:
1. DB에서 모든 관련 데이터를 일괄 조회 (FRED/ECOS/yfinance/pykrx)
2. Static `.md` 파일을 통째로 읽기 (`_read_md()`)
3. Dynamic memory 행을 일괄 로드 (`_load_memory()`)
4. 모든 것을 하나의 dict로 조립하여 반환

이 dict가 Jinja 템플릿에 주입되어 system prompt의 일부가 됨 → 매번 ~5000+ token 사용.

**문제점**:
- Micro 페르소나가 워치리스트 5종목 분석 시 모든 종목의 모든 지표를 미리 로드하지만, 실제로 관심 있는 종목은 2~3개일 수 있음
- Macro 페르소나가 FRED 12개 시리즈 + ECOS + 글로벌 자산 7종을 모두 로드하지만, 해당 주에 변동이 큰 것만 중요
- 불필요한 데이터가 추론을 희석시킴 (attention budget 낭비)

### 2. 현재 Risk REJECT 처리

**파일**: `src/trading/personas/orchestrator.py` (lines 241-255)

```python
rk_res, review_id, verdict = risk_persona.run(
    rk_input, decision_id=decision_id, cycle_kind="pre_market"
)
# ...
if verdict != "APPROVE":
    res.rejected.append(decision_id)
    continue  # ← 즉시 다음 시그널로 넘어감, 피드백 없음
```

**문제점**:
- Risk가 REJECT하면 그 시그널은 영구 폐기됨
- Decision 페르소나는 자신의 시그널이 왜 거부됐는지 모름
- "수량을 줄이면 통과할 수 있었을" 케이스도 버려짐
- Risk의 `rationale`과 `concerns` 필드가 audit에만 저장되고 활용되지 않음

### 3. Risk Persona 응답 구조

**파일**: `src/trading/personas/prompts/risk.jinja`

Risk는 JSON으로 응답:
```json
{
  "verdict": "APPROVE|HOLD|REJECT",
  "rationale": "검증 결과 한국어 2~4줄",
  "concerns": ["우려1", "우려2"],
  "limit_compliance": { ... }
}
```

`rationale`과 `concerns` 필드가 이미 구조화되어 있어 Decision에게 피드백으로 전달하기 용이함.

### 4. Persona Base (call_persona) 확장 지점

**파일**: `src/trading/personas/base.py`

현재 `call_persona()` 함수 구조:
- Anthropic `client.messages.create()` 단일 호출
- system prompt에 `cache_control` 적용 (SPEC-008)
- 응답 파싱 → DB 저장 → memory_ops 실행

**Tool-use 확장 필요사항**:
- `tools` parameter 추가
- `stop_reason` 체크 루프 (tool_use → execute → tool_result → re-send)
- 다중 round 토큰 합산
- tool_call_log 기록

### 5. Scheduler 및 Timing 영향

**파일**: `src/trading/scheduler/runner.py`

APScheduler의 cron trigger로 실행. 현재 Pre-market 07:30 → 09:00 사이에 Micro + Decision + Risk 완료해야 함 (90분 여유). Reflection Loop (최대 +10초) 및 Tool-calling (최대 +5초/persona) 추가해도 충분한 여유.

장중 정기 호출(09:30, 11:00 등)은 event trigger 60초 요건이 적용되나, Reflection 포함해도 총 ~40초 이내 완료 가능.

---

## Token Savings 추정

### 현재 (Bulk Injection)

| 페르소나 | System Prompt | User Message (context) | 합계 Input |
|---|---|---|---|
| Macro | ~2000 tok | ~4000 tok (FRED+ECOS+assets+.md+memory) | ~6000 |
| Micro | ~1500 tok | ~5000 tok (워치리스트 전체 데이터) | ~6500 |
| Decision | ~2000 tok | ~3000 tok (macro+micro summary+assets) | ~5000 |
| Risk | ~1000 tok | ~2000 tok (signal+assets+limits) | ~3000 |

### Tool-calling 예상

| 페르소나 | System+Tools Schema | Base User Msg | Tool Calls (avg) | Tool Results | 합계 |
|---|---|---|---|---|---|
| Macro | ~2500 tok | ~500 tok | 3~4 | ~800 tok | ~3800 (-37%) |
| Micro | ~2000 tok | ~300 tok | 5~8 | ~1200 tok | ~3500 (-46%) |
| Decision | ~2500 tok | ~800 tok | 2~3 | ~600 tok | ~3900 (-22%) |
| Risk | ~1200 tok | ~800 tok | 1~2 | ~400 tok | ~2400 (-20%) |

**참고**: Tool schema definitions 자체가 ~500 tok 추가되지만, 이는 SPEC-008 캐싱으로 cache_read (0.1x 비용)됨.

**예상 월 절감**:
- 현재 월 ~18~30만원 (SPEC-008 캐싱 후)
- Tool-calling 적용 후: ~12~20만원 (추가 ~30% 절감)
- Reflection Loop 추가 비용: +3~5만원/월
- **순 절감**: ~2~8만원/월 (tool-calling savings - reflection cost)

---

## Risk Assessment

### Technical Risks

| 리스크 | 영향 | 완화 |
|---|---|---|
| Anthropic tool-use API 변경 | Tool schema 호환 깨짐 | version pinning + adapter layer |
| Tool 호출 latency 누적 | 60초 event trigger 요건 미충족 | 5초 timeout + fallback + parallel call (future) |
| Persona가 불필요한 tool 남용 | 토큰 절감 효과 감소 | max 8 rounds 제한 + prompt instruction |
| Reflection 무한 oscillation | 비용 폭발 + 지연 | max 2 rounds 하드코딩 |
| Fallback path 미검증 | Tool 장애 시 시스템 정지 | fallback은 기존 bulk injection = 이미 검증됨 |

### Operational Risks

| 리스크 | 영향 | 완화 |
|---|---|---|
| Feature flag 전환 실수 | 의도치 않은 동작 | Telegram 확인 + audit_log |
| Reflection으로 인한 매매 지연 | 시가 매매 시점 이탈 | Pre-market은 09:00까지 여유 있음 |
| Risk SoD 위반 (Reflection이 Risk 독립성 훼손) | 거버넌스 위반 | Risk는 revised signal을 "새 시그널"로 독립 평가 |

---

## Design Decisions (ADR)

### ADR-1: Tool-calling vs RAG

**결정**: Tool-calling (Function Calling) 채택, RAG 미채택.

**근거**:
- 데이터가 이미 Postgres에 구조화되어 있음 → vector search 불필요
- 정확한 수치(PER 12.3, RSI 67)가 필요함 → embedding similarity보다 exact query가 적합
- Anthropic native tool-use가 RAG pipeline보다 구현 단순
- tech.md ADR: "RAG 미채택 — 시스템 프롬프트 + 데이터 어댑터로 충분"

### ADR-2: Reflection 대상을 REJECT로 한정 (HOLD 제외)

**결정**: REJECT만 Reflection Loop 대상. HOLD는 대상 아님.

**근거**:
- REJECT은 명확한 거부 사유가 있음 (limit breach, concentration, contradiction)
- HOLD는 "판단 보류" — 사유가 모호하고 수정 방향이 불분명
- HOLD에 대한 재시도는 Decision이 "근거를 보강"해야 하는데, 이는 hallucination 위험
- 비용 제어: REJECT-only로 reflection 빈도를 일일 ~5~10건으로 제한

### ADR-3: Risk는 Reflection 사실을 모름

**결정**: Risk persona에게 "이것이 수정된 시그널입니다"를 알리지 않음.

**근거**:
- SoD 원칙: 검증자는 독립적으로 판단해야 함
- "수정됐으니 관대하게 봐라"는 bias 유발
- Risk는 항상 동일한 기준으로 시그널을 평가 (original이든 revised이든)
- 만약 revised signal이 진짜 문제없다면 Risk는 APPROVE할 것

### ADR-4: Tool result를 캐시하지 않음

**결정**: Tool result는 Anthropic prompt cache에 포함하지 않음.

**근거**:
- Tool result는 매번 다름 (실시간 시세, 최신 수급 등)
- 캐시하면 stale data를 사용할 위험
- System prompt + Tool schema는 안정적이므로 캐시 (SPEC-008 호환)
- Tool result input tokens는 on-demand이므로 전체적으로 적은 양
