# SPEC-TRADING-030 — Acceptance Criteria

Given-When-Then 형식. 외부 의존(Anthropic, KIS `balance`, CLI 브리지, DB, 파일시스템)은
monkeypatch/mock 으로 격리. methodology: TDD.

---

## AC-1 — 인텔리전스 다이제스트 (REQ-030-1)
- **Given** `intelligence_macro.md`(전문) 와 `intelligence_micro.md`(17개 story) fixture
- **When** `_gather_today()` 가 실행되면
- **Then** 반환 dict 에 `intelligence` 키가 존재하고, 매크로는 상위 N_MACRO(기본 10)·마이크로는 상위
  N_MICRO(기본 12) story 가 **Impact 점수 내림차순**으로 담긴다
- **And** 각 story 는 title·impact·keywords·strategy(→) 를 보존한다
- **And** 마이크로가 잘린 경우 "(+M 건 저영향 생략)" 류 표기가 포함된다

## AC-2 — 보유자산/P&L 수집 (REQ-030-2)
- **Given** `balance(client)` 가 holdings 2건 + 요약을 반환하도록 mock
- **When** `_gather_today()` 실행
- **Then** 반환 dict 에 `portfolio` 키가 존재하며 holdings(ticker/name/qty/avg_cost/current_price/
  eval_amount/pnl_amount/pnl_pct) 와 요약(total_assets/cash_d2/stock_eval/invest_basis/pnl_total)을 포함
- **And** 기존 운영 지표 키(`orders, runs, risk, cost, cumulative, tool_stats, reflection_stats,
  model_breakdown, auto_expansion_tickers`)가 **모두 유지**된다

## AC-3 — CLI 구독 경로 호출 (REQ-030-3)
- **Given** `call_persona_via_cli` 가 `PersonaResult(response_text="<3섹션 총평>", response_json=None)` 반환하도록 mock
- **When** 내러티브 생성기(`_narrative_text(data)`)가 호출되면
- **Then** 반환값은 mock 의 `response_text` 와 동일하다
- **And** 호출 인자에 `persona_name="daily_report"`, `model="cli-claude-max"`, `expect_json=False`,
  `apply_memory_ops=False` 가 포함된다

## AC-4 — 3-섹션 총평 프롬프트 (REQ-030-4)
- **Given** 내러티브 생성기 호출
- **When** system 프롬프트가 구성되면
- **Then** 프롬프트는 매크로 총평·마이크로 총평·보유자산 리뷰·종합 4개 산출을 지시한다
- **And** KRW-only(원/₩만, USD$ 금지)·환각 금지·새로운 분석/추측 금지·"이미 일어난 일/제공 데이터만"
  가드레일 문구를 포함한다(기존 `_llm_text` system 프롬프트 재사용)

## AC-5 — 출력 합성 순서 (REQ-030-5)
- **Given** 내러티브 생성 성공
- **When** `generate_and_send()` 가 본문을 합성하면
- **Then** 본문은 **정성 총평이 상단**, **운영 지표 블록이 하단** 순서로 배치된다
- **And** 운영 지표 블록 내용은 기존 `_fallback_text` 의 지표 출력과 동등하다

## AC-6 — Graceful degrade (REQ-030-6)
- **Given** `call_persona_via_cli` 가 예외(RuntimeError: double failure 등)를 던지도록 mock
- **When** `generate_and_send()` 실행
- **Then** 예외가 전파되지 않고, 본문은 운영 지표 전용 `_fallback_text(skip_reason=...)` 가 된다
- **And** `skip_reason` 메시지가 사람이 읽을 수 있는 사유로 채워진다(`_llm_skip_reason` 동작 유지)
- **And** DB UPSERT 와 `system_briefing` 텔레그램 전송은 정상 수행된다(cron 미중단)

## AC-7 — cli_only_mode 불변 (REQ-030-7)
- **Given** 변경된 daily_report 경로
- **When** 정적 검사(grep/AST)를 수행하면
- **Then** 일일 리포트 경로에 신규 `Anthropic(...).messages.create(...)` **직접 호출이 추가되지 않는다**
- **And** cli_only_mode 를 끄거나 우회하는 코드가 없다

## AC-8 — 인텔리전스 재생성 금지 (REQ-030-8)
- **Given** 일일 리포트 실행
- **When** 파일시스템 쓰기를 모니터링하면
- **Then** `intelligence_macro.md`/`intelligence_micro.md` 에 대한 **쓰기가 발생하지 않는다**(읽기 전용)
- **And** 뉴스 재수집/재분석/인텔리전스 재생성 파이프라인이 호출되지 않는다

## AC-9 — 소스별 부분 결손 (REQ-030-9, 엣지 케이스)
- **AC-9a (인텔리전스 결손/stale):** `intelligence_*.md` 미존재(또는 stale)면 해당 총평 섹션이
  "_(인텔리전스 미생성/오래됨)_" 자리표시자로 대체되고 나머지 섹션·지표는 정상 진행
- **AC-9b (balance 실패):** `balance()` 가 `KisError` 를 던지면 `portfolio` 가 안전 자리표시자가 되고
  예외가 전파되지 않으며, 보유자산 리뷰가 "_(잔고 조회 실패)_" 로 표기
- **AC-9c (빈 보유):** `holdings=[]` 이면 보유자산 리뷰가 "_(보유 종목 없음)_"
- **AC-9d (무거래):** `orders=[]` 이면 운영 지표 블록은 0건으로 정상 출력, 총평은 시장 코멘트 위주로 생성
- **AC-9e (CLI 불가→Haiku 폴백):** CLI 가 실패하고 Haiku 폴백이 성공하면 내러티브가 정상 생성되고
  cli_only_mode 정책 위반이 아니다(허용된 폴백). 폴백까지 실패하면 AC-6 의 degrade 적용

---

## 추가 엣지 케이스 (참고)
- intelligence 파일은 존재하나 `[투자 주목]` story 가 0개 → 다이제스트 빈 목록, 총평은 데이터 부족 명시.
- holdings 일부 항목의 숫자 필드 결측/0 → KRW 포맷 안전 처리(예외 없음).
- 매크로는 stale, 마이크로는 정상 같은 **혼합 결손** → 섹션별 독립 처리.

## Definition of Done
- REQ-030-1 ~ REQ-030-9 전부 대응 테스트 통과
- `tests/reports/` 신규 테스트 + 기존 테스트(`test_daily_report_llm_skip.py` 갱신 포함) 통과
- 라인 커버리지 ≥ 85% (`src/trading/reports/daily_report.py` 변경분)
- ruff/black 통과, TRUST 5 게이트 통과
- 16:00 cron 경로가 어떤 단일 소스 실패에도 크래시하지 않음(AC-6, AC-9 입증)
- OPEN QUESTION(plan.md §0) 해소 기록 — persona_name 계약 확정 방식 명시
