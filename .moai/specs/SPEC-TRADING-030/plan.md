# SPEC-TRADING-030 — Implementation Plan

_methodology: TDD (RED → GREEN → REFACTOR), brownfield enhancement_

## 0. OPEN QUESTION — run 에이전트가 코드 작성 전 반드시 해소

**`call_persona_via_cli` 자유 텍스트 계약 확정.** `expect_json=False` 일 때 텍스트가
`PersonaResult.response_text` 로 반환됨은 코드상 확인됨(base.py:735). 그러나 내부의
`build_cli_prompt(persona_name=..., ...)`(base.py:602)·`call_persona_cli(...)`(base.py:611)가
**등록되지 않은 새 `persona_name="daily_report"`** 에 대해 동작하는지 확인 필요:
- (a) `build_cli_prompt`/CLI 브리지가 persona_name 별 **등록 Jinja 템플릿/레지스트리**를 요구하는가?
  요구한다면 → `daily_report` 전용 페르소나 등록 또는 인라인 system_prompt 전용 경로 필요.
  요구하지 않는다면 → macro.py 패턴대로 인라인 `system_prompt` 문자열만으로 충분.
- (b) CLI 브리지 export/wait 라운드트립이 `expect_json=False` 산문을 손실 없이 `response_text` 로 돌려주는지
  통합 1건으로 실증.

→ 결론에 따라 (a) 인라인 system_prompt 만 사용, 또는 (b) `prompts/daily_report.jinja` 추가 중 택1.
**이 질문 해소 전 GREEN 단계 진입 금지.**

## 1. 변경 대상 파일

| 파일 | 변경 종류 | 내용 |
| --- | --- | --- |
| `src/trading/reports/daily_report.py` | **수정(주 변경)** | `_gather_today()` 확장(REQ-030-1/2), 신규 `_narrative_text()`(REQ-030-3/4), `generate_and_send()` 합성·degrade(REQ-030-5/6), 다이제스트 헬퍼. |
| `src/trading/reports/_intelligence.py` (가칭) | **신규(선택)** | intelligence_*.md 파싱·Impact 정렬·top-N 다이제스트 헬퍼. `context.py:_read_md` 를 공용화하거나 reports 전용 소형 reader 도입. ※ 단일 함수로 충분하면 daily_report.py 내부 함수로 둘 수도 있음(REFACTOR 판단). |
| `src/trading/personas/context.py` | **수정(선택)** | `_read_md` 를 공용 함수로 승격(예: `read_context_md`)하여 reports 에서 재사용. 비공개 유지 시 reports 측에 동등 헬퍼 작성. |
| `tests/reports/test_daily_report_narrative.py` (신규) | **신규** | REQ-030-1~9 단위/통합 테스트. |
| `prompts/daily_report.jinja` | **조건부 신규** | OPEN QUESTION (a) 결과가 "템플릿 필요" 일 때만. |

> 3개 이상 파일 변경 가능 → TDD 단위로 분해(아래 Phase). 동일 파일(daily_report.py) 집중 변경이므로
> 병렬 worktree 불필요(sequential).

## 2. TDD Phases

### Phase R0 — 계약 확인 (OPEN QUESTION 해소)
- `build_cli_prompt`/`call_persona_cli` 소스 정독 + 등록되지 않은 persona_name 동작 실증(스파이크 테스트 1건).
- 산출: 인라인 system_prompt vs 템플릿 경로 결정.

### Phase 1 — 인텔리전스 다이제스트 (REQ-030-1)
- RED: `intelligence_macro.md`/`micro.md` 샘플 fixture 로 top-N(Impact 내림차순) 선별 + 잘림 표기 테스트.
- GREEN: 파서/다이제스트 헬퍼 구현. 미존재/stale 시 자리표시자(REQ-030-9a).
- REFACTOR: `_read_md` 재사용 정리, 상수 N_MACRO/N_MICRO 추출.

### Phase 2 — 보유자산 수집 (REQ-030-2)
- RED: `balance()` mock 으로 `_gather_today()` 가 `portfolio` 키(holdings+요약)를 담는지, 기존 키 유지 검증.
- RED(엣지): `balance()` 가 `KisError` raise 시 `portfolio` 가 안전 자리표시자가 되고 예외 미전파(REQ-030-9b).
- GREEN/REFACTOR: KIS client 획득 경로는 기존 코드 관례 따름(주입/팩토리 mock 가능하게).

### Phase 3 — CLI 내러티브 생성 (REQ-030-3/4)
- RED: `call_persona_via_cli` mock → `PersonaResult(response_text="...")` 반환 시 `_narrative_text()` 가
  그 텍스트를 그대로 돌려주는지. 호출 인자에 `expect_json=False`, `apply_memory_ops=False`,
  `persona_name="daily_report"`, `model="cli-claude-max"` 포함 검증.
- RED: system 프롬프트에 3섹션(매크로/마이크로/보유자산)+종합 지시, KRW-only·환각금지 가드레일 포함 검증.
- GREEN: 기존 `_llm_text` 가드레일 문구를 재사용해 system 프롬프트 작성. user_message 에 다이제스트+portfolio+
  운영지표 직렬화 전달.
- REFACTOR: `_llm_text`(직접 API) 는 **제거 또는 명시적 deprecated** 처리(REQ-030-7 유지). `@block_if_cli_only_mode`
  데코레이터 의존 흐름이 깨지지 않도록 `generate_and_send` 분기 재정렬.

### Phase 4 — 출력 합성 + degrade (REQ-030-5/6/9)
- RED: 성공 경로 → 본문 = [총평(상단)] + [운영 지표(하단)] 순서. 지표 블록은 기존 `_fallback_text` 내용 재사용.
- RED: CLI+Haiku 모두 실패 → `_fallback_text(skip_reason=...)` 로만 발송, 예외 미전파, cron 미중단(REQ-030-6).
- RED(엣지): 무거래/빈 보유/인텔리전스 stale 조합에서 크래시 없음(REQ-030-9).
- GREEN/REFACTOR: `generate_and_send` 의 try/except 를 내러티브+합성 기준으로 재구성. DB UPSERT·텔레그램
  전송 흐름은 보존.

### Phase 5 — 정적 불변식 (REQ-030-7/8)
- 테스트: daily_report 경로에서 `Anthropic(...).messages.create` 직접 호출이 신규로 추가되지 않음(grep/AST).
- 테스트: 일일 리포트 경로가 intelligence_*.md 에 **쓰지 않음**(읽기 전용) — 파일 쓰기 모킹/검사.

## 3. 기술 접근 (technical approach)

- **다이제스트 직렬화:** 선별된 story 들을 `[{title, impact, keywords, strategy}]` 구조로 정규화 →
  user_message JSON 또는 마크다운 블록으로 전달. 운영 지표(`data`)도 함께 직렬화(기존 `_llm_text` 가
  `json.dumps(data)` 를 넘기던 방식 계승, 단 intelligence/portfolio 추가).
- **system 프롬프트:** 기존 line 252-259 문구(KRW-only, 환각/추측 금지, 이미 일어난 일만)를 baseline 으로,
  "다음 3개 섹션을 작성: ## 매크로 총평 / ## 마이크로 총평 / ## 보유자산 리뷰 / ## 종합" 지시 추가.
- **모델 인자:** `model="cli-claude-max"`(브리지가 감사용으로 기록; 실제 CLI 가 사용, base.py:573-574).
- **Haiku 폴백:** 별도 처리 불요 — `call_persona_via_cli` 내부가 CLI 실패 시 자동 Haiku 폴백(허용됨).
  단 폴백도 실패(double failure → RuntimeError)면 Phase 4 의 except 가 `_fallback_text` 로 degrade.

## 4. Milestones (우선순위, 시간 추정 없음)

- **Primary Goal:** R0(계약 확인) → Phase 1·2(데이터 수집) → Phase 3(CLI 내러티브). 핵심 가치(총평 생성) 완성.
- **Secondary Goal:** Phase 4(합성 + graceful degrade). 16:00 cron 안정성 보장.
- **Final Goal:** Phase 5(정적 불변식) + 85% 커버리지 + 엣지 케이스 전부.
- **Optional Goal:** intelligence 헬퍼 공용화 리팩터(`context._read_md` 승격), stale 표기 mtime 정밀화.

## 5. Risks & 대응

| 리스크 | 영향 | 대응 |
| --- | --- | --- |
| `build_cli_prompt` 가 등록 persona 요구 | Phase 3 막힘 | R0 에서 선확인; 필요 시 daily_report.jinja 추가 또는 인라인 경로 |
| CLI 브리지 export/wait 지연으로 16:00 리포트 지연 | cron 지연 | Phase 4 degrade 가 안전망; 타임아웃 시 Haiku→fallback_text |
| 다이제스트 과대 → 프롬프트 비대 | 포커스 저하 | top-N 캡 + 잘림 표기(REQ-030-1) |
| `balance()` 라이브 호출이 16:00(장 마감 후) 신뢰성 | 보유 리뷰 결손 | REQ-030-9b 가드 + positions 테이블 대안 검토(Optional) |
| `_llm_text` 제거 시 기존 테스트(`test_daily_report_llm_skip.py`) 깨짐 | 회귀 | 해당 테스트 갱신(deprecated 동작 반영) |

## 6. @MX 후보

- `_narrative_text()` — 신규 공개 경로, fan_in 가능 → `@MX:NOTE`(SPEC-030, CLI 구독 경로 의도 명시).
- `generate_and_send()` — degrade 분기 다중 → 변경 시 `@MX:NOTE` 갱신.
