---
id: SPEC-TRADING-030
version: 0.1.0
status: draft
created: 2026-05-28
updated: 2026-05-28
author: onigunsow
priority: medium
issue_number: 0
domain: TRADING
title: "일일 리포트 종합 리뷰 — CLI 구독 경로 매크로/마이크로 총평 + 보유자산 리뷰"
related_specs:
  - SPEC-TRADING-014   # News Intelligence Pipeline (intelligence_*.md 사전 계산)
  - SPEC-TRADING-015   # cli_only_mode / CLI 브리지
  - SPEC-TRADING-016   # block_if_cli_only_mode 데코레이터, Haiku 폴백 가드
  - SPEC-TRADING-029   # KIS balance() 기반 보유/P&L
methodology: TDD
changelog:
  - version: 0.1.0
    date: 2026-05-28
    summary: >-
      Initial draft. 일일 리포트에 정성 내러티브 리뷰(매크로 총평 / 마이크로 총평 /
      보유자산 리뷰 + 종합)를 추가. cli_only_mode 를 유지한 채 CLI 구독 경로
      (call_persona_via_cli, expect_json=False, apply_memory_ops=False)로 생성하여
      과금 0. 매크로/마이크로 재료는 사전 계산된 data/contexts/intelligence_*.md 를
      재사용(재생성 금지). 실패 시 기존 운영 지표 전용 _fallback_text 로 graceful degrade.
---

# SPEC-TRADING-030 — 일일 리포트 종합 리뷰

## Overview (배경)

현재 일일 리포트(`src/trading/reports/daily_report.py`, 16:00 KST cron)는 **운영 지표만**
출력한다. LLM 요약 함수 `_llm_text()` 는 직접 Anthropic Sonnet API 를 호출하는데,
시스템이 **cli_only_mode**(SPEC-015/016)로 운영되어 `@block_if_cli_only_mode` 가 이 호출을
**항상 차단**한다. 결과적으로 리포트는 늘 운영 지표 전용 평문(`_fallback_text`)만 발송되며,
꼬리에 "(CLI 전용 모드 — 직접 API 호출 차단(정상), 결정형 요약 사용)" 이 붙는다.

사용자 요구: 일일 리포트에 **그날 수집한 뉴스와 보유자산을 종합한 정성 리뷰**를 추가한다 —
(1) 시장 **매크로 총평**, (2) 시장 **마이크로 총평**, (3) **보유자산 리뷰**(+ 종합).

핵심 사실 두 가지가 이 요구를 저비용으로 실현 가능하게 한다:
- **사전 계산된 인텔리전스:** `data/contexts/intelligence_macro.md`(~9.4KB) / `intelligence_micro.md`(~38KB)
  는 News Intelligence Pipeline(SPEC-014)이 매일 생성하며, 이미 per-story Impact 점수·키워드·
  한국어 대응전략(→)을 담고 있다. **총평 재료가 이미 존재**한다.
- **과금 0 CLI 구독 경로:** `call_persona_via_cli(expect_json=False)` 는 자유 산문을 반환하고
  `persona_runs` 에 `cli-claude-max`/cost=0 으로 기록한다. cli_only_mode 를 **완화하지 않고도**
  내러티브를 생성할 수 있다.

상세 file:line 근거는 `research.md` 참조.

## Scope (범위)

### In scope
- `_gather_today()` 확장: 운영 지표(유지) + 인텔리전스 다이제스트 + `balance()` 보유/P&L.
- CLI 구독 경로 내러티브 생성기(신규 함수 또는 `_llm_text` 리팩터)로 3-섹션 총평 생성.
- 출력 합성: **내러티브 총평(상단)** + **운영 지표(하단)**.
- 실패 시 기존 `_fallback_text` 로 graceful degrade(16:00 cron 절대 중단 금지).
- 소스별 부분 결손/stale 처리(주말·주간 캐던스, API 실패, 빈 보유, 무거래).

### Non-goals (명시적 비목표)
- **cli_only_mode 완화 금지.** 직접 Sonnet/Anthropic API 호출 부활 금지.
- **인텔리전스 분석 재생성 금지.** `intelligence_*.md` 는 **읽기 전용 재사용**만; 뉴스 재수집·재분석·
  재요약 파이프라인을 새로 만들지 않는다.
- 메모리(macro_memory/micro_memory) 쓰기 금지(`apply_memory_ops=False`).
- 텔레그램 전송/영속화 로직(`generate_and_send` 의 DB UPSERT·`system_briefing`) 구조 변경 없음 —
  본문 텍스트 내용만 확장.
- 새 cron/스케줄 추가 없음(기존 16:00 일일 리포트에 한정).

## Assumptions (가정)

- A1. `data/contexts/intelligence_macro.md` / `intelligence_micro.md` 의 섹션 포맷은
  `### [투자 주목] <제목> (Impact: N/5)` → 메타라인 → `→ <대응전략>` 구조로 안정적이다.
  (검증: 2026-05-28 macro 15개·micro 17개 story.) Impact 점수는 1~5.
- A2. `call_persona_via_cli(expect_json=False)` 는 `PersonaResult.response_text` 에 자유 텍스트를
  담아 반환하며, cli_only_mode 에서 `block_if_cli_only_mode` 의 차단 대상이 **아니다**(이 데코레이터는
  `_llm_text` 의 직접 API 호출에만 적용). — research.md §3.
- A3. CLI 실패 시 Haiku API 폴백은 cli_only_mode 에서도 **의도적으로 허용**된다(base.py:84). 따라서
  "CLI 구독 불가 → Haiku 폴백" 은 정책 위반이 아니다.
- A4. `balance(client)` 는 라이브 KIS 호출이며 실패 시 `KisError` 를 던질 수 있다.
- A5. 16:00 리포트의 토큰/지연 예산은 CLI 구독이므로 비용이 아닌 **프롬프트 포커스/크기**가 제약이다.

## Requirements (EARS)

### REQ-030-1 — 인텔리전스 다이제스트 수집 (Ubiquitous)
시스템은 일일 리포트 데이터 수집 시 **항상** `intelligence_macro.md` 와 `intelligence_micro.md` 의
다이제스트를 함께 수집해야 한다.
- (a) **다이제스트 전략:** 매크로는 파일이 작으므로(~9.4KB) 전문 또는 상위 N(기본 N_MACRO=10)
  story 를 Impact 내림차순으로 포함한다. 마이크로는 파일이 크므로(~38KB) 상위 N(기본 N_MICRO=12)
  story 를 **Impact 점수 내림차순**으로 선별하고, 잘린 경우 "(+M 건 저영향 생략)" 표기를 붙인다.
- (b) 각 story 는 제목 · Impact · Keywords · 대응전략(→) 을 보존한다.
- (c) N_MACRO/N_MICRO 는 튜너블 상수로 둔다.
- (d) 수집 결과는 `_gather_today()` 반환 dict 의 신규 키(예: `intelligence`)에 담는다.

### REQ-030-2 — 보유자산/P&L 수집 (Ubiquitous)
시스템은 일일 리포트 데이터 수집 시 **항상** `account.balance(client)` 의 `holdings`
(ticker/name/qty/avg_cost/current_price/eval_amount/pnl_amount/pnl_pct) 와 요약
(total_assets/cash_d2/stock_eval/invest_basis/pnl_total)을 수집해 반환 dict 신규 키(예: `portfolio`)에
담아야 한다. 기존 운영 지표 키는 **그대로 유지**한다.

### REQ-030-3 — CLI 구독 내러티브 생성 (Event-driven)
**WHEN** 일일 리포트를 생성할 때 **THEN** 시스템은 `call_persona_via_cli(persona_name="daily_report",
model="cli-claude-max", expect_json=False, apply_memory_ops=False, ...)` 를 통해 정성 내러티브를
생성해야 하며, 결과 텍스트는 `PersonaResult.response_text` 에서 취한다.

### REQ-030-4 — 3-섹션 총평 + 종합 (State-driven)
**IF** 내러티브 생성기가 호출되면 **THEN** system 프롬프트는 다음 3개 섹션과 종합을 한국어로
산출하도록 지시해야 한다: (1) **매크로 총평**, (2) **마이크로 총평**, (3) **보유자산 리뷰**, 그리고
이를 묶는 **종합 코멘트**.
- (a) 기존 `_llm_text` system 프롬프트의 가드레일을 재사용: 사실 기반·환각 금지·새로운 분석/추측 금지·
  이미 일어난 일과 제공된 데이터만 요약·**모든 금액 KRW(원/₩)만, USD($) 금지**.
- (b) 매크로/마이크로 총평은 제공된 intelligence 다이제스트를 근거로만 작성(외부 지식 추가 금지).
- (c) 보유자산 리뷰는 `portfolio` 데이터의 종목·평가손익을 근거로만 작성.

### REQ-030-5 — 출력 합성 (State-driven)
**IF** 내러티브 생성에 성공하면 **THEN** 최종 리포트 본문은 **정성 총평(상단)** → **운영 지표(하단)**
순서로 합성해야 한다. (총평이 사람이 읽는 헤드라인, 지표는 참고 상세.)

### REQ-030-6 — Graceful degrade (Unwanted)
시스템은 내러티브 생성(CLI + Haiku 폴백 모두) 실패 시에도 16:00 cron 을 **중단하지 않아야 한다**.
실패 시 기존 운영 지표 전용 `_fallback_text(skip_reason=...)` 로 degrade 하고, `_llm_skip_reason` 메시지가
사유를 사람이 읽을 수 있게 유지해야 한다.

### REQ-030-7 — cli_only_mode 불변 (Unwanted)
시스템은 본 기능을 위해 cli_only_mode 를 완화하거나 직접 Anthropic Sonnet API 호출을 부활시키지
**않아야 한다**. 내러티브는 CLI 구독 경로(필요 시 허용된 Haiku 폴백)로만 생성한다.

### REQ-030-8 — 인텔리전스 재생성 금지 (Unwanted)
시스템은 `intelligence_*.md` 를 **읽기 전용으로만 재사용**해야 하며, 일일 리포트 경로에서 뉴스
재수집·재분석·인텔리전스 파일 재생성을 **수행하지 않아야 한다**.

### REQ-030-9 — 소스별 부분 결손 처리 (State-driven)
**IF** 개별 소스가 결손/실패하면 **THEN** 해당 섹션만 안전한 자리표시자로 대체하고 나머지는 정상
진행해야 한다.
- (a) intelligence_*.md 미존재/stale(주말·주간 캐던스): 해당 총평을 "_(인텔리전스 미생성/오래됨)_" 로 표기.
- (b) `balance()` 실패(KisError 등): 보유자산 리뷰를 "_(잔고 조회 실패)_" 로 표기, 예외 전파 금지.
- (c) 빈 보유(holdings=[]): "_(보유 종목 없음)_".
- (d) 무거래(orders=[]): 운영 지표 블록은 0건으로 정상 출력, 총평은 시장 코멘트 위주.

## Traceability

| REQ | 구현 대상(예정) | 검증(acceptance.md) |
| --- | --- | --- |
| REQ-030-1 | `_gather_today()` + 다이제스트 헬퍼 | AC-1, AC-9a |
| REQ-030-2 | `_gather_today()` + `balance()` 통합 | AC-2, AC-9b/c |
| REQ-030-3 | 신규 `_narrative_text()` (CLI 경로) | AC-3 |
| REQ-030-4 | `_narrative_text()` system 프롬프트 | AC-4 |
| REQ-030-5 | `generate_and_send()` 합성 | AC-5 |
| REQ-030-6 | `generate_and_send()` try/except | AC-6 |
| REQ-030-7 | 직접 API 미사용(정적 검사) | AC-7 |
| REQ-030-8 | 읽기 전용 재사용(정적 검사) | AC-8 |
| REQ-030-9 | 소스별 가드 | AC-9 |
