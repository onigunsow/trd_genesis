---
id: SPEC-TRADING-034
version: 0.1.1
status: draft
created: 2026-05-28
updated: 2026-05-28
author: onigunsow
priority: medium
issue_number: 0
domain: TRADING
title: "휴면 포트폴리오 페르소나 사이클 연결 — decision→portfolio→risk→execute 사이징 규율"
related_specs:
  - SPEC-TRADING-001   # 포트폴리오 페르소나 정의 SPEC (REQ-PERSONA-04-1 6-persona, REQ-PERSONA-05-7 holdings≥5 게이트, 07:55 Portfolio 배치, portfolio_adjustments 테이블)
  - SPEC-TRADING-016   # 5-persona 시스템 / 자본 보전 원칙 / cli_only_mode 모델 가드(block_if_cli_only_mode)
  - SPEC-TRADING-029   # balance()/compute_balance_pcts — cash_pct·holdings·total_assets source
  - SPEC-TRADING-033   # 매도(청산)를 막지 않는다는 일관성 — buy-only 조정 범위
  - SPEC-TRADING-015   # All Personas to Claude Code CLI (zero API cost) — REQ-034-9 CLI 전환 근거
  - SPEC-TRADING-030   # daily_report 의 call_persona_via_cli(expect_json) 선례 — CLI 분기 참조
---

# SPEC-TRADING-034 — 휴면 포트폴리오 페르소나 사이클 연결

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-28 | 0.1.0 | Initial draft. 포트폴리오 페르소나(`src/trading/personas/portfolio.py`)는 **완전 구현되어 있으나 어디서도 호출되지 않는다**(grep 검증: `portfolio.run`/`personas.portfolio`/`run_portfolio` 가 모듈 자신 외부에서 0건). 결과적으로 섹터 편중 회피·상관관계·신규매수 vs 리밸런스 우선순위 같은 **포트폴리오 차원의 사이징 규율이 실거래 흐름에서 전혀 작동하지 않는다.** 본 SPEC 은 이 페르소나를 decision→portfolio→risk→execute 체인에 연결하여, 매 결정 사이클(pre_market/intraday/event)에서 **buy 시그널에 한해** 포트폴리오 조정을 **구속력 있게(binding)** 적용한다(qty_adjusted 반영, rejected 드롭). 보유 종목 ≥ 5 에서만 활성, sell 시그널은 무조정 통과(SPEC-033 정합), 페르소나 실패 시 미조정 시그널로 fail-safe, 모든 조정은 텔레그램 + audit_log. 사용자 정책 결정 반영 — 2026-05-28 | onigunsow |
| 2026-05-28 | 0.1.1 | 승인 전 개정. CLI 전환을 정식 요구사항(REQ-034-9)으로 승격 — `portfolio.py` 가 cli_only_mode 에서 `call_persona_via_cli` 로 비용 0 동작하도록(decision.py 패턴). Q-1(섹터 데이터) 해소=LLM 지식 기반 best-effort, 섹터맵 향후 SPEC defer. Q-2 해소=REQ-034-9. 사용자 결정 2026-05-28 | onigunsow |
| 2026-05-28 | 0.1.2 | 승인(approved) — run 진입. 개정본(REQ-034-9/AC-12/Q-1·Q-2 RESOLVED) 검토 통과. 사용자 위임 승인 2026-05-28 | onigunsow |

---

## Scope Summary

본 SPEC 은 **이미 만들어졌으나 죽어 있는(dormant) 포트폴리오 페르소나를 살려** 실거래 사이클에
연결한다. 오늘날 매매 시그널은 decision 페르소나 → (halt 게이트) → risk 검토 → 실행으로 흐르며,
그 사이에 **포트폴리오 차원의 사이징 규율(섹터 편중, 상관관계, 신규매수/리밸런스 우선순위)이 끼어들
지점이 없다.** 포트폴리오 페르소나는 `run(...)`·`is_active(...)`·전용 프롬프트까지 완비되어 있으나
호출 경로가 없어 휴면 상태다. 본 SPEC 은 세 결정 사이클 모두에서 decision 산출 후·risk/execute 전에
이 페르소나를 삽입하여, **buy 시그널에 한해** 조정을 구속력 있게 적용한다.

### 문제 (검증된 휴면 코드)

- **포트폴리오 페르소나는 호출되지 않는다.** `src/trading/personas/portfolio.py`(35줄)는
  `run(input_data: dict, cycle_kind="pre_market") -> PersonaResult`(라인 22~35, Sonnet
  `claude-sonnet-4-6` 라인 13, `expect_json=True` 라인 34, `max_tokens=2000` 라인 33)과
  `is_active(holdings_count) -> bool`(라인 18~19, `ACTIVATION_THRESHOLD = 5` 라인 15)을 정의한다.
  그러나 grep `portfolio.run`/`personas.portfolio`/`run_portfolio`/`_apply_portfolio`/`portfolio_gate`
  는 모듈 자신 외부에서 **0건**(검증). 즉 어떤 사이클도 이 페르소나를 부르지 않는다.
- **프롬프트는 완비되어 있다.** `src/trading/personas/prompts/portfolio.jinja` 입력:
  `today`(라인 29), `decision_signals`(라인 31), `holdings_count`(라인 10/34),
  `holdings`(라인 35), `total_assets`(라인 36), `cash_pct`(라인 37). 책임(라인 3~7): 섹터 편중
  회피(단일 섹터 60% 이상 → 같은 섹터 신규 매수 축소), 상관관계 검토, 신규매수 vs 리밸런스 우선순위.
  출력 JSON(라인 12~26): `{"adjusted_signals":[{"ticker","side","qty_original","qty_adjusted",
  "rationale"}], "rejected":[{"ticker","reason"}]}`.
- **사이클 구조는 세 곳 모두 동일하다.** `src/trading/personas/orchestrator.py`:
  - `run_pre_market_cycle`(라인 775): decision `dec_res, sig_ids = decision_persona.run(...)`
    (라인 864) → halt 게이트(`if state["halt_state"]:` 라인 891) → `signals =
    (dec_res.response_json or {}).get("signals", [])`(라인 907) → `for sig, decision_id in
    zip(signals, sig_ids, strict=False):`(라인 909) risk+execute 루프.
  - `run_event_trigger_cycle`(라인 1082): decision(라인 1162) → halt 게이트(라인 1189) →
    `signals = ...get("signals", [])`(라인 1206) → `zip(signals, sig_ids ...)`(라인 1207).
  - `run_intraday_cycle`(라인 1241): decision(라인 1322) → halt 게이트(라인 1347) →
    `signals = ...get("signals", [])`(라인 1364) → `zip(signals, sig_ids ...)`(라인 1366).
- **`sig_ids` 정렬.** `decision.run`(decision.py 라인 22~121)은 각 decision 시그널을
  `persona_decisions` 행으로 INSERT 하고 그 row id 를 `sig_ids` 에 **시그널 순서대로** 담아
  `(res, sig_ids)` 반환(decision.py 라인 96~121). 따라서 `signals[i] ↔ sig_ids[i]` 는 **위치 정렬**
  이며, 조정 시 ticker 기준으로 매핑하되 시그널↔sig_id 정렬을 보존해야 한다.

### In scope

- 공유 헬퍼(예: `_apply_portfolio_adjustment(signals, sig_ids, *, holdings, holdings_count,
  total_assets, cash_pct, today, cycle_kind) -> tuple[list, list]`) 1개를 orchestrator.py
  (또는 신규 `src/trading/personas/portfolio_gate.py`)에 도입.
- 세 결정 사이클(pre_market/intraday/event) **모두**에서, `signals`/`sig_ids` 가 준비되고 **halt
  게이트 통과 직후·`for sig, decision_id in zip(...)` 실행 루프 직전**에 이 헬퍼를 호출. 루프는
  이후 **조정된(adjusted)** 시그널/`sig_ids` 를 순회한다.
- **buy 시그널만 조정 대상.** sell(및 hold 등 non-buy)은 무조정 통과(REQ-034-4).
- **활성화: holdings_count ≥ 5**(기존 `is_active`). 5 미만이면 포트폴리오 호출을 **완전히 스킵**
  (Sonnet 비용 0, 시그널 무변경).
- **구속력(binding) 조정:** `adjusted_signals[ticker].qty_adjusted` 를 해당 buy 시그널의 `qty` 로
  설정(`qty_adjusted == 0` ⇒ 드롭=거부 취급). `rejected[ticker]` 는 해당 buy 시그널(과 그 sig_id)
  을 실행에서 제거. 이후 기존 risk 한도 검사 + execute 루프가 **조정된 집합**에 대해 실행(리스크
  한도는 변경 없이 최종 hard gate 로 유지).
- **fail-safe:** 페르소나 실패(LLM/CLI 오류, 타임아웃, 잘못된 JSON, 키 누락) 시 **미조정 시그널로
  폴백**(거래 차단 금지) + 로그 + 텔레그램.
- **투명성:** 조정/거부를 텔레그램 `system_briefing` + `audit_log`(`PORTFOLIO_ADJUSTMENT`)로 노출.
  포트폴리오로 거부된 buy 는 `res.rejected` 에도 기록(실행은 안 함).

### Non-goals (명시적 비목표)

- **decision/risk/execute 페르소나 로직, sig_ids 의미, 세 사이클의 기타 동작 무변경.** 본 SPEC 은
  *additive* 레이어이며 decision→portfolio→risk→execute 순서에 portfolio 단계를 *삽입*만 한다
  (REQ-034-8).
- **sell 청산 차단 금지.** 포트폴리오 레이어는 sell 을 절대 축소/드롭하지 않는다 — exit 은 막혀선 안
  됨(SPEC-033 정합).
- **리스크 한도(limits.py)·회로차단·halt 게이트 로직 변경 없음.** 포트폴리오 조정 *이후*에도 기존
  risk 한도 검사가 그대로 최종 게이트로 동작한다.
- **DB 스키마 변경/마이그레이션 없음.** decision 행은 이미 존재하고, 거부는 실행 시점 처리. 익절·체결
  같은 신규 컬럼 불필요. (선택: 기존 `portfolio_adjustments` 테이블(migration 005)에 조정을 기록할
  수 있으나 본 SPEC 의 필수 기록은 audit_log + res.rejected + telegram — Open Question Q-4.)
- **정밀 섹터맵 보강 미포함(섹터 편중은 LLM best-effort).** `balance()` holdings 는 섹터 필드를 담지
  않는다(account.py:42~55). 사용자 결정에 따라 섹터 편중 판정은 페르소나의 LLM 지식 기반 best-effort
  로 수용하며(A-9), 정밀 섹터 맵(pykrx/KIS 종목정보 조인 또는 holdings 섹터 필드)은 본 SPEC 범위 밖의
  향후 작업이다(Q-1 RESOLVED=defer).

---

## Environment

- 기존 SPEC-001 ~ SPEC-033 인프라 (Docker compose, Postgres 16-alpine, Telegram trading bot).
- `src/trading/personas/portfolio.py`:
  - `run(input_data: dict[str, Any], cycle_kind: str = "pre_market") -> PersonaResult`(라인 22~35).
    내부에서 `render_prompt("portfolio.jinja", ...)`(라인 24) 후 **`call_persona(...)`(라인 26~35,
    `model="claude-sonnet-4-6"`, `expect_json=True`, `max_tokens=2000`)** 호출. 반환은
    `PersonaResult`(base.py:195~207)이며 파싱된 JSON 은 `res.response_json`(dict 또는 None).
  - `is_active(holdings_count) -> bool`(라인 18~19): `holdings_count >= 5`(`ACTIVATION_THRESHOLD=5`).
  - **중요(검증된 결함 — 본 SPEC 이 수정):** `portfolio.run` 은 decision.run 처럼
    `is_cli_mode_active()` 로 CLI 경로(`call_persona_via_cli`)에 **분기하지 않고** 항상 API 경로
    (`call_persona`)를 탄다. `call_persona` 는 `block_if_cli_only_mode` 데코레이터가 **붙어 있지
    않으므로**(base.py:210, 데코레이터 정의는 base.py:78) cli_only_mode 에서도 raise 하지 않지만,
    **실제 유료 Sonnet API 호출**을 시도하고 `ANTHROPIC_API_KEY` 미설정 시 `RuntimeError`
    (base.py:234~235)를 던진다. 즉 현재 zero-cost CLI 레짐(SPEC-015/016)에서는 (a) API 키가 있으면
    비용 발생, (b) 없으면 예외. **본 SPEC 은 REQ-034-9 로 `portfolio.run` 을 decision.py CLI 분기
    패턴으로 전환하여 cli_only_mode 에서 비용 0 으로 동작하게 한다(정식 요구사항, MODIFIED 파일).**
  - **CLI 분기 템플릿 = `src/trading/personas/decision.py`:** import(라인 16)
    `from trading.personas.base import call_persona, call_persona_via_cli, is_cli_mode_active,
    render_prompt`; 분기(라인 49) `if is_cli_mode_active():` → (라인 59) `res =
    call_persona_via_cli(...)` `else:`(라인 79) `res = call_persona(...)`. `expect_json=True` 유지.
    SPEC-030 daily_report 도 동일한 `call_persona_via_cli(expect_json=...)` 패턴 사용.
- `src/trading/personas/prompts/portfolio.jinja`:
  - 입력 키: `today`(29), `decision_signals`(31), `holdings_count`(10/34), `holdings`(35),
    `total_assets`(36), `cash_pct`(37). 출력 JSON: `adjusted_signals[{ticker,side,qty_original,
    qty_adjusted,rationale}]` + `rejected[{ticker,reason}]`(라인 12~26).
- `src/trading/personas/orchestrator.py`:
  - 세 사이클 decision→halt→signals→execute 위치(작성 시점 라인): pre_market 864/891/907/909,
    event 1162/1189/1206/1207, intraday 1322/1347/1364/1366.
  - **balance/assets 가 이미 스코프에 있다(중복 KIS 호출 불필요):**
    - pre_market: `assets = _gather_assets()`(라인 817), `cash_pct = ...`(라인 818).
    - event: `assets = _gather_assets()`(라인 1142), `cash_pct = ...`(라인 1143).
    - intraday: `assets = _gather_assets()`(라인 1278), `cash_pct = ...`(라인 1279).
    - `_gather_assets()`(라인 280~284)는 `balance(client)` 를 그대로 반환하므로 `assets` 는
      `holdings`(리스트)·`total_assets`·`cash_d2`·`stock_eval`·`invest_basis` 를 모두 담는다.
      → `holdings = assets["holdings"]`, `holdings_count = len(assets["holdings"])`,
      `total_assets = assets["total_assets"]`. 워치독처럼 별도 `balance()` 재호출 불필요.
  - `compute_balance_pcts(bal) -> (cash_pct, equity_pct)`(라인 64~73, SPEC-029): `invest_basis`
    (= `cash_d2 + stock_eval`)를 분모로 cash%/equity% 가 **합 100%** 가 되도록 산출. 단, 세 사이클의
    인-스코프 `cash_pct`(라인 818/1143/1279)는 `cash_d2 / total_assets * 100`(headline 분모)로
    계산된 *다른* 값이다(합 100% 보장 안 됨). 포트폴리오 프롬프트의 `cash_pct` 는
    `compute_balance_pcts(assets)[0]`(SPEC-029 통일 분모) 사용을 권장(Open Question Q-3).
  - `CycleResult`(라인 329~338): `rejected: list[int]`(라인 338) 는 거부된 decision/sig_id row id 의
    리스트. 기존 거부 사이트(라인 924/978/981/... 다수)가 `res.rejected.append(decision_id)` 패턴 사용.
  - import: `from trading.alerts import telegram as tg`(라인 26),
    `from trading.db.session import audit, connection, get_system_state, update_system_state`(라인 29).
- `src/trading/personas/decision.py`:
  - `run(...) -> (PersonaResult, list[int])`(라인 22~121). `sig_ids` 는 `signals` 와 위치 정렬된
    persona_decisions row id (라인 96~121).
- `src/trading/personas/base.py`:
  - `call_persona(*, persona_name, model, cycle_kind, system_prompt, user_message, ...,
    expect_json=False, ...) -> PersonaResult`(라인 210~). API 키 없으면 RuntimeError(라인 234~235).
  - `call_persona_via_cli(...)`(라인 555~) — zero-cost CLI 경로(decision.run 이 분기 사용).
  - `is_cli_mode_active() -> bool`(라인 747~775): `cli_personas_enabled` + watcher heartbeat 검사.
  - `block_if_cli_only_mode`(라인 78~125) — `call_persona` 에는 **미적용**.
- `src/trading/alerts/telegram.py`: `system_briefing(category: str, message: str)`(라인 70, 위치
  인자 2개), `system_error(component, error, *, context="")`(라인 182).
- `src/trading/db/session.py`: `audit(event_type, actor, details=None)`(라인 47). **신규 DB 레이어
  불필요.**
- `src/trading/db/migrations/005_m5_observability.sql`: 이미 `portfolio_adjustments` 테이블 존재
  (라인 15~24: `persona_run_id`, `decision_id`, `qty_original`, `qty_adjusted`, `rationale`, `raw`).
  SPEC-001 이 의도한 포트폴리오 조정 영속 위치 — 본 SPEC 의 필수는 아니나 선택적 기록 대상(Q-4).
- 마이그레이션 디렉터리 현재 최고 번호 = `023_halt_notify_cooldown.sql`(SPEC-031). **본 SPEC 은 신규
  마이그레이션 불필요**(스키마 무변경).

---

## Assumptions

- A-1: `assets`(= `balance()`)는 세 사이클 모두에서 decision 호출 이전에 이미 적재되어 있으며
  `holdings`/`total_assets`/`cash_d2`/`stock_eval`/`invest_basis` 를 담는다(orchestrator
  817/1142/1278, account.py:76~87). 따라서 포트폴리오 입력은 **재호출 없이** `assets` 에서 파생한다.
- A-2: `signals[i] ↔ sig_ids[i]` 는 위치 정렬(decision.py:96~121). 조정 시 ticker 로 매핑하되
  조정 후 두 리스트의 정렬을 보존한다(같은 인덱스가 같은 시그널을 가리킴).
- A-3: 포트폴리오 출력 `adjusted_signals`/`rejected` 의 ticker 는 **입력 buy 시그널의 부분집합**일
  것으로 기대하나, 입력에 없는 ticker 가 섞여 올 수 있다 → 매칭되지 않는 ticker 는 무시(REQ-034-2/3
  방어). buy 시그널에 없는 ticker 의 조정/거부는 no-op.
- A-4: `qty_adjusted` 가 음수/비정수/누락이면 방어적으로 처리(누락=조정 없음, ≤0=드롭). 구현자는
  타입/키 존재를 방어 확인.
- A-5: holdings_count < 5 면 `is_active` 가 False → 포트폴리오 호출 자체를 스킵하므로 Sonnet 비용 0.
- A-6: 포트폴리오 조정은 **buy 시그널의 qty 만 감소(또는 0=드롭)** 시킨다 — 증액 시나리오는 본 SPEC
  의도가 아니며(리스크 축소·편중 회피 방향), `qty_adjusted` 가 원본보다 크면 그대로 반영하되 이후
  risk 한도 검사가 최종 게이트로 제한한다(과청산이 아니라 과매수 방지는 risk 단계 책임).
- A-7: 포트폴리오 페르소나 호출은 사이클당 1회(전체 buy 시그널을 한 번에 전달)이며, per-signal 호출이
  아니다(프롬프트가 `decision_signals` 리스트 전체를 받음, portfolio.jinja:30~31).
- A-8: 본 SPEC 은 현행 paper full-auto 를 가정한다(SPEC-024/033 선례). real 모드 notify-only 전환은
  범위 밖.
- A-9: **섹터 편중 판정은 LLM 지식 기반 best-effort 다(사용자 결정).** `balance()` holdings 는 섹터
  필드를 담지 않으므로(account.py:42~55) 포트폴리오 페르소나는 Claude 자신의 KRX ticker→섹터 지식
  (주요 종목명 인지)에 의존해 섹터 편중을 추정한다. v0.1.0 에서는 이 best-effort 판정을 수용하며,
  정밀 섹터 맵(pykrx join / holdings 섹터 필드)은 향후 SPEC 으로 defer 한다(Q-1 RESOLVED).
- A-10: **`portfolio.run` 의 CLI 분기 전환은 본 SPEC 의 in-scope 다(REQ-034-9).** 따라서 "portfolio
  .run 이 이미 cli_only_mode 를 존중한다"는 가정은 **성립하지 않으며**(오늘날 항상 `call_persona`
  API 경로), 본 SPEC 이 `portfolio.py` 의 `run()` 을 decision.py CLI 분기 패턴으로 전환하여 그렇게
  만든다. 테스트는 CLI 브리지(`call_persona_via_cli`)를 mock 하며 네트워크를 타지 않는다.

---

## Requirements (EARS)

### REQ-034-1 (State-driven) — buy 시그널 + holdings≥5 시 portfolio 실행

**WHILE** 보유 종목 수(`holdings_count`)가 5 이상이고 결정 사이클이 1개 이상의 **buy** 시그널을
산출했으면, **THEN** 시스템은 risk/execute 루프 진입 **전에** 포트폴리오 페르소나를 그 buy 시그널들에
대해 실행해야 한다.
- (a) 세 사이클(pre_market/intraday/event) 모두에서, `signals`/`sig_ids` 준비 직후·**halt 게이트
  통과 직후**·`for sig, decision_id in zip(signals, sig_ids, ...)` 실행 루프 **직전**에 호출한다.
- (b) 포트폴리오 입력은 인-스코프 `assets` 에서 파생: `decision_signals`=(buy 시그널만),
  `holdings`=`assets["holdings"]`, `holdings_count`=`len(assets["holdings"])`,
  `total_assets`=`assets["total_assets"]`, `cash_pct`(SPEC-029 통일 분모 권장), `today`, `cycle_kind`.

### REQ-034-2 (Event-driven) — qty_adjusted 구속력 반영

**WHEN** 포트폴리오 출력 `adjusted_signals` 에 buy 시그널 ticker 의 항목이 있으면, **THEN** 시스템은
해당 buy 시그널의 `qty` 를 `qty_adjusted` 로 설정해야 하며, `qty_adjusted == 0`(또는 ≤0)이면 그
시그널을 **드롭(거부 취급)**해야 한다.
- (a) 매칭은 ticker 기준. 입력 buy 시그널에 없는 ticker 의 조정 항목은 무시(no-op).
- (b) 조정은 *advisory 가 아니라 binding* — 조정된 qty 가 그대로 risk/execute 단계로 전달된다.

### REQ-034-3 (Event-driven) — rejected buy 드롭 + 기록

**WHEN** 포트폴리오 출력 `rejected` 에 buy 시그널 ticker 가 있으면, **THEN** 시스템은 해당 buy 시그널
(과 위치 정렬된 `sig_id`)을 실행 집합에서 **제거**하고, 그 sig_id 를 `res.rejected` 에 추가하며,
`audit_log` 에 거부 사실을 남기되 그 시그널을 **실행하지 않아야** 한다.
- (a) 드롭된 시그널은 risk/execute 루프에 도달하지 않는다.
- (b) `qty_adjusted == 0`(REQ-034-2)에 의한 드롭도 동일하게 `res.rejected` + audit 기록.

### REQ-034-4 (Unwanted) — sell 시그널 무조정 통과

시스템은 sell(및 hold 등 non-buy) 시그널을 포트폴리오 레이어에서 **절대 축소/드롭하지 않아야** 하며,
포트폴리오 페르소나에는 buy 시그널만 `decision_signals` 로 전달해야 한다 — exit 청산이 포트폴리오
사이징에 의해 막혀선 안 된다(SPEC-033 정합).
- (a) non-buy 시그널과 그 `sig_id` 는 무변경으로 보존되어, 조정된 buy 시그널과 합쳐져(시그널↔sig_id
  정렬 유지) 실행 루프로 전달된다.

### REQ-034-5 (Unwanted / State-driven) — holdings<5 시 portfolio 완전 스킵

**WHILE** 보유 종목 수가 5 미만이면, 시스템은 포트폴리오 페르소나를 **호출하지 않아야** 하며(Sonnet
비용 0), 시그널·sig_ids 를 **무변경**으로 그대로 risk/execute 루프에 전달해야 한다.
- (a) `is_active(holdings_count)`(portfolio.py:18) 가 False 면 헬퍼는 입력을 그대로 반환(no-op).

### REQ-034-6 (Unwanted) — 페르소나 실패 시 fail-safe

**IF** 포트폴리오 페르소나가 실패하면(LLM/CLI 오류, 타임아웃, 잘못된/누락 JSON, 키 누락, API 키 미설정
등 예외), **THEN** 시스템은 **미조정(원본) 시그널/sig_ids 로 폴백**하여 사이클을 **계속**해야 하며,
오류를 로그 + 텔레그램으로 알려야 한다 — 포트폴리오는 enhancement 레이어이므로 사이클을 절대 중단시켜선
안 된다.
- (a) 예외/`response_json is None`/필수 키 누락 시 입력 시그널/sig_ids 를 그대로 반환.
- (b) 텔레그램 발송 실패도 swallow 하여 사이클을 죽이지 않는다.

### REQ-034-7 (Ubiquitous) — 투명성: 텔레그램 + 감사, risk 한도는 최종 게이트 유지

시스템은 **비자명(non-trivial) 조정/거부가 발생할 때마다** 텔레그램 `system_briefing` 1회와
`audit_log` 1건을 남겨야 하며, 포트폴리오 조정 **이후에도** 기존 risk 한도 검사가 변경 없이 최종 hard
gate 로 동작해야 한다.
- (a) 텔레그램: 어떤 buy 가 얼마로 축소/드롭되었는지·사유를 운영자가 볼 수 있게 표기.
- (b) 감사: `audit("PORTFOLIO_ADJUSTMENT", actor="orchestrator"(또는 "portfolio_gate"),
  details={"cycle": cycle_kind, "adjusted": [...], "rejected": [...]})`.
- (c) 조정으로 qty 가 바뀐 시그널도 그대로 risk 단계로 흘러가 `check_pre_order`/risk 페르소나 검사를
  받는다 — 포트폴리오는 risk 게이트를 우회하지 않는다.

### REQ-034-8 (Unwanted) — 기존 흐름 무회귀

시스템은 본 레이어 도입으로 decision/risk/execute 의 동작·반환, `sig_ids` 의미, 세 사이클의 기타
동작(halt 게이트, qty=0 스킵, 3+ HOLD 차단, briefing 등)을 **변경하지 않아야** 한다. 본 SPEC 은
decision→risk 사이에 portfolio 단계를 **삽입**만 하며 기존 함수 정의를 *호출/조회*만 한다.
- (a) holdings<5 또는 buy 시그널 없음 또는 페르소나 실패 시 동작은 본 SPEC 이전과 **동일**해야 한다
  (시그널/sig_ids 무변경 통과).

### REQ-034-9 (Ubiquitous) — 포트폴리오 페르소나 CLI 전환(zero cost)

시스템은 포트폴리오 페르소나 호출을 `decision.py` 패턴(`if is_cli_mode_active():
call_persona_via_cli(...) else: call_persona(...)`)으로 수행하여 cli_only_mode 에서 **비용 0**
(Claude Code CLI 구독 경로)으로 동작해야 한다. 이를 위해 `src/trading/personas/portfolio.py` 의
`run()` 을 CLI 분기로 **전환**한다(SPEC-015/016/030 cli_only_mode 호환). `expect_json=True` 는 유지한다.
- (a) 템플릿: `src/trading/personas/decision.py` 라인 16(import `call_persona,
  call_persona_via_cli, is_cli_mode_active, render_prompt`) / 49(`if is_cli_mode_active():`) /
  59(`res = call_persona_via_cli(...)`) / 79(`else: res = call_persona(...)`). SPEC-030 daily_report
  도 동일한 `call_persona_via_cli(expect_json=...)` 패턴을 사용한다.
- (b) 전환 후 `portfolio.run` 은 cli_only_mode 가 활성이면 CLI 경로(비용 0·`ANTHROPIC_API_KEY`
  불요), 비활성이면 기존 API 경로를 탄다 — 이로써 REQ-034-1 의 포트폴리오 호출이 zero-cost CLI 레짐
  에서 실제로 작동한다.
- (c) 본 전환은 REQ-034-6 fail-safe 를 대체하지 않는다 — CLI/API 어느 경로든 실패 시 미조정 폴백은
  그대로 적용된다.

---

## Specifications

### 권장 메커니즘 (구현 가이드 — 구현자 재량 여지 있음)

대상 REQ: REQ-034-1 ~ REQ-034-9

- **공유 헬퍼** `_apply_portfolio_adjustment(signals, sig_ids, *, holdings, holdings_count,
  total_assets, cash_pct, today, cycle_kind) -> tuple[list, list]` — orchestrator.py 또는 신규
  `src/trading/personas/portfolio_gate.py`:
  1. `if not portfolio.is_active(holdings_count):` → `(signals, sig_ids)` 그대로 반환(REQ-034-5).
  2. buy / non-buy 분리: `buys = [(s, sid) for s, sid in zip(signals, sig_ids) if
     s.get("side") == "buy"]`, `others = [(s, sid) for ... if side != "buy"]`. non-buy 는 무변경
     보존(REQ-034-4).
  3. `if not buys:` → `(signals, sig_ids)` 그대로 반환(조정할 buy 없음).
  4. `try:` `res = portfolio.run({"today": today, "decision_signals": [b[0] for b in buys],
     "holdings": holdings, "holdings_count": holdings_count, "total_assets": total_assets,
     "cash_pct": cash_pct}, cycle_kind)`. `pj = res.response_json`; `pj is None` 이거나 필수 키
     누락이면 **미조정 폴백**. `except Exception:` → 로그 + `tg.system_error`/`system_briefing` +
     **미조정 폴백**(REQ-034-6).
  5. 조정 적용(ticker 매핑):
     - `adjusted = {a["ticker"]: a for a in pj.get("adjusted_signals", []) if "ticker" in a}`.
     - `rejected = {r["ticker"] for r in pj.get("rejected", []) if "ticker" in r}`.
     - 각 `(s, sid)` in `buys`:
       - `if s["ticker"] in rejected:` → 드롭(`res.rejected.append(sid)` + audit 기록), 실행 제외.
       - `elif s["ticker"] in adjusted:` → `q = int(adjusted[...]["qty_adjusted"])`;
         `if q <= 0:` 드롭(REQ-034-2: qty_adjusted==0 ⇒ 거부 취급) `else:` `s["qty"] = q` 유지.
       - `else:` 무변경 유지.
  6. 재조립: `kept_buys`(조정/유지된 buy) + `others`(non-buy)를 합쳐 **시그널↔sig_id 정렬 보존**한
     `(new_signals, new_sig_ids)` 생성. (정렬 순서는 기존 실행 순서와 호환되게 — 권장: buy 먼저 유지
     순서 + non-buy, 또는 원래 인덱스 순서 보존. 구현자 재량.)
  7. 비자명 조정/거부가 있으면 `tg.system_briefing("포트폴리오 조정", <message>)` +
     `audit("PORTFOLIO_ADJUSTMENT", actor=..., details={"cycle","adjusted":[...],"rejected":[...]})`
     (REQ-034-7).
  8. `(new_signals, new_sig_ids)` 반환.
  - 순수 매핑 로직(`adjusted`/`rejected` 적용)을 작은 헬퍼로 분리하면 단위 테스트 용이.

- **세 사이클 삽입 지점:** 각 사이클에서 `signals = (dec_res.response_json or {}).get("signals", [])`
  바로 다음(pre_market 라인 907 직후, event 1206 직후, intraday 1364 직후)·`for sig, decision_id
  in zip(...)` 직전(909/1207/1366 직전)에:
  ```
  signals, sig_ids = _apply_portfolio_adjustment(
      signals, sig_ids,
      holdings=assets["holdings"],
      holdings_count=len(assets["holdings"]),
      total_assets=assets["total_assets"],
      cash_pct=compute_balance_pcts(assets)[0],   # Q-3: SPEC-029 통일 분모 권장
      today=today, cycle_kind=res.cycle_kind,
  )
  ```
  이후 `for sig, decision_id in zip(signals, sig_ids, strict=False):` 가 **조정된** 두 리스트를
  순회. `assets` 는 이미 스코프(817/1142/1278) — 중복 KIS 호출 없음.

- **buy-only 입력(REQ-034-4):** 헬퍼가 내부에서 buy 만 페르소나에 전달하고, sell/hold 는 손대지 않고
  결과에 합류시킨다. 외부 호출부는 전체 `signals`/`sig_ids` 를 넘기기만 하면 된다.

- **fail-safe(REQ-034-6):** `portfolio.run` 의 예외(특히 `ANTHROPIC_API_KEY missing` RuntimeError,
  base.py:234~235; JSON 파싱 실패 → `response_json=None`)를 try/except 로 흡수하고 입력을 그대로
  반환. 텔레그램 실패도 swallow.

- **CLI 전환(REQ-034-9, 정식 요구사항):** `portfolio.py` 의 `run()` 을 decision.py 패턴으로 전환:
  - import 를 `from trading.personas.base import call_persona, call_persona_via_cli,
    is_cli_mode_active, render_prompt` 로 확장(현재는 `call_persona, render_prompt` 만, portfolio.py
    라인 11).
  - `run()` 본문에서 `system_prompt = render_prompt(...)` 후
    `if is_cli_mode_active(): res = call_persona_via_cli(persona_name=PERSONA, model=MODEL,
    cycle_kind=cycle_kind, system_prompt=..., user_message=..., trigger_context={...},
    expect_json=True) else: res = call_persona(... expect_json=True ...)` 로 분기하고 `res` 반환.
    decision.py 라인 49/59/79 와 동일 구조. (`call_persona_via_cli` 의 정확한 키워드 인자는 base.py:
    555~ 시그니처에 맞춰 구현 — `tickers`/`input_data` 등은 포트폴리오엔 불필요할 수 있음, 구현자
    재량.)
  - 이는 zero-cost CLI 레짐(SPEC-015/016/030)에서 포트폴리오 호출이 실제 작동하게 만드는 **필수**
    단계이며, 헬퍼 wiring 과 함께(또는 그 이전에) 수행한다. REQ-034-6 fail-safe 는 CLI/API 어느
    경로든 그대로 적용.

- **스키마:** 신규 마이그레이션 **불필요**(거부는 실행 시점·`res.rejected`/audit_log 로 처리). 선택적
  으로 기존 `portfolio_adjustments`(migration 005)에 `qty_original`/`qty_adjusted`/`rationale` 기록
  가능(Q-4).

### 영향 파일 (예정)

- `src/trading/personas/portfolio.py` (**MODIFIED** — REQ-034-9: `run()` 을 decision.py CLI 분기
  패턴으로 전환, import 확장)
- `src/trading/personas/orchestrator.py` (헬퍼 추가 또는 호출 + 세 사이클 삽입; 또는 호출만)
- (선택) `src/trading/personas/portfolio_gate.py` (신규 — 헬퍼를 별도 모듈로 둘 경우)
- `prompts/portfolio.jinja`, `decision.py`(CLI 분기 템플릿), `base.py`(call_persona_via_cli/
  is_cli_mode_active 참조), `kis/account.py` (변경 불필요 — 호출/조회/참조만)
- 테스트: `tests/personas/test_portfolio_gate.py`(권장; CLI 브리지 mock — 네트워크 미사용)

---

## Open Questions (Flag Only)

- **Q-1 (섹터 데이터 부재 — 편중 판정 한계) — RESOLVED**: portfolio.jinja(라인 5)는 "단일 섹터 60%
  이상 보유 시 같은 섹터 신규 매수 축소"를 책임으로 명시하지만, `balance()` holdings 는 **섹터 필드를
  담지 않는다**(account.py:42~55: ticker/name/qty/avg_cost/current_price/eval_amount/pnl_amount/
  pnl_pct 만). **사용자 결정 = LLM 지식 기반 best-effort 수용** — 포트폴리오 페르소나는 Claude 자신의
  KRX ticker→섹터 지식(주요 종목명 인지)으로 섹터 편중을 추정한다(A-9). 정밀 섹터 맵(pykrx join /
  holdings 섹터 필드)은 **향후 SPEC 으로 defer**. v0.1.0 의 한계로 명시. flag only.**
- **Q-2 (portfolio.run 의 CLI 미분기 — 비용/가용성) — RESOLVED**: `portfolio.run` 이 항상
  `call_persona`(유료 Sonnet API)를 타고 decision.run 식 `is_cli_mode_active()` 분기가 없던 결함은
  **본 SPEC 의 정식 요구사항 REQ-034-9 로 해소**된다 — `portfolio.py` 의 `run()` 을 decision.py CLI
  분기 패턴으로 전환하여 cli_only_mode 에서 비용 0 으로 동작하게 한다. flag only(요구사항으로 승격됨).**
- **Q-3 (cash_pct 분모 선택)**: 세 사이클의 인-스코프 `cash_pct`(817/1143/1279)는
  `cash_d2/total_assets`(headline 분모)이고, SPEC-029 `compute_balance_pcts`(64~73)는 invest_basis
  분모(합 100%)다. 프롬프트의 `cash_pct` 에 어느 쪽을 쓸지 — **SPEC-029 통일 분모
  (`compute_balance_pcts(assets)[0]`) 권장**(보유%와 합 100% 일관). flag only.
- **Q-4 (portfolio_adjustments 테이블 기록 여부)**: migration 005 의 `portfolio_adjustments` 테이블
  (persona_run_id/decision_id/qty_original/qty_adjusted/rationale/raw)이 이미 존재하며 SPEC-001 이
  의도한 영속 위치다. 본 SPEC 의 필수 기록은 audit_log + res.rejected + telegram 이나, 이 테이블에
  조정 상세를 추가 기록하면 분석/관측에 유리. **선택 — 채택 시 마이그레이션 불필요(테이블 기존).
  flag only.**
- **Q-5 (intraday */15 빈도 × 사이클당 1 Sonnet 호출 — 비용/노이즈)**: holdings≥5 일 때 매 intraday
  사이클(`*/15`)마다 포트폴리오 호출 1회가 발생한다(buy 시그널이 있을 때만). 비용/텔레그램 노이즈가
  과하면 향후 throttle(쿨다운/조정 무변경 시 텔레그램 생략)을 고려 — **향후 개선. flag only.**

---

## Acceptance & Traceability

자세한 acceptance criteria 는 `acceptance.md` 참조.
구현 계획 및 milestone 은 `plan.md` 참조.

| REQ | 구현 대상(예정) | 검증(acceptance.md) |
| --- | --- | --- |
| REQ-034-1 | holdings≥5 + buy 존재 시 세 사이클에서 portfolio 실행(삽입 지점) | AC-1, AC-8 |
| REQ-034-2 | qty_adjusted 구속 반영 + qty_adjusted==0 드롭 | AC-1, AC-6 |
| REQ-034-3 | rejected buy 드롭 + res.rejected + audit, 미실행 | AC-2 |
| REQ-034-4 | sell 무조정 통과(buy-only 입력) | AC-3 |
| REQ-034-5 | holdings<5 → portfolio 미호출, 시그널 무변경 | AC-4 |
| REQ-034-6 | 페르소나 실패 → 미조정 폴백 + 알림 | AC-5 |
| REQ-034-7 | 조정 시 telegram + audit; risk 한도 최종 게이트 유지 | AC-9, AC-10 |
| REQ-034-8 | 기존 decision/risk/execute/sig_ids/사이클 무회귀 | AC-11 |
| REQ-034-9 | portfolio.run CLI 분기 전환(cli_only_mode 비용 0) | AC-12 |
| (경계) | adjusted ticker 가 buy 시그널에 없음 → 무시 | AC-7 |
