# SPEC-TRADING-034 — Implementation Plan

휴면 포트폴리오 페르소나 사이클 연결. 완전 구현되었으나 호출되지 않는 포트폴리오 페르소나를
decision→portfolio→risk→execute 체인에 삽입하는 작고 additive 한 사이징 규율 레이어 —
세 결정 사이클(pre_market/intraday/event)에서 buy 시그널에 한해 구속력 있는 포트폴리오 조정을
적용하며, 기존 decision/risk/execute·sig_ids 의미·사이클 동작은 무회귀.

## Technical Approach

공유 헬퍼 `_apply_portfolio_adjustment(signals, sig_ids, *, holdings, holdings_count,
total_assets, cash_pct, today, cycle_kind) -> tuple[list, list]` 를 orchestrator.py(또는 신규
`src/trading/personas/portfolio_gate.py`)에 도입한다. 헬퍼는 (1) `portfolio.is_active(holdings_count)`
가 False(=holdings<5)면 입력을 그대로 반환, (2) buy / non-buy 시그널을 분리(non-buy 는 sig_id 와 함께
무변경 보존), (3) buy 가 없으면 그대로 반환, (4) `portfolio.run({today, decision_signals:<buy 시그널>,
holdings, holdings_count, total_assets, cash_pct}, cycle_kind)` 호출, (5) 예외/`response_json=None`/
키 누락이면 **미조정 폴백**(로그+텔레그램), (6) `adjusted_signals` 의 `qty_adjusted` 를 ticker 매핑으로
buy 시그널 qty 에 반영(`qty_adjusted==0` ⇒ 드롭), `rejected` ticker 의 buy 를 sig_id 와 함께 드롭
(`res.rejected` + audit), (7) 조정된 buy + 무변경 non-buy 를 **시그널↔sig_id 정렬 보존**하여 재조립,
(8) 비자명 조정 시 텔레그램 + audit 후 `(new_signals, new_sig_ids)` 반환. 세 사이클에서 `signals =
(dec_res.response_json or {}).get("signals", [])` 직후·`for sig, decision_id in zip(...)` 직전에 이
헬퍼를 호출하여 실행 루프가 조정된 리스트를 순회하게 한다. `assets`(=`balance()`)가 이미 스코프에
있으므로(orchestrator 817/1142/1278) 중복 KIS 호출 없이 holdings/total_assets/cash_pct 를 파생한다.

### 핵심 결정 (사용자 승인)

- **구속력(binding) 조정.** 포트폴리오 출력은 advisory 가 아니라 적용된다 — `qty_adjusted` 를 buy 의
  qty 로 설정(`==0` ⇒ 드롭), `rejected` buy 는 실행에서 제거. 이후 기존 risk 한도가 최종 hard gate.
- **buy-only 범위.** buy 시그널만 조정/거부 대상. sell(및 hold)은 무조정 통과 — exit 청산은 포트폴리오
  레이어가 절대 막지 않는다(SPEC-033 정합). 페르소나에는 buy 만 `decision_signals` 로 전달.
- **활성화 holdings_count ≥ 5**(기존 `is_active`, ACTIVATION_THRESHOLD=5). 5 미만이면 portfolio 호출
  자체를 스킵(Sonnet 비용 0, 시그널 무변경).
- **세 사이클 모두**(pre_market/intraday/event)의 risk+execute 루프 직전에 적용.
- **fail-safe.** 페르소나 실패(LLM/CLI 오류, 타임아웃, 잘못된 JSON, 키 누락) → 미조정 시그널 폴백
  (거래 차단 금지) + 로그 + 텔레그램. enhancement 레이어는 사이클을 절대 중단시키지 않는다.
- **투명성.** 조정/거부를 텔레그램 `system_briefing("포트폴리오 조정", ...)` + `audit_log`
  (`PORTFOLIO_ADJUSTMENT`, details={cycle, adjusted:[...], rejected:[...]})로 기록. 포트폴리오로
  거부된 buy 는 `res.rejected` 에도 기록(미실행).
- **CLI 전환(REQ-034-9, 필수).** `portfolio.py` 의 `run()` 을 decision.py 패턴
  (`if is_cli_mode_active(): call_persona_via_cli(...) else: call_persona(...)`)으로 전환하여
  cli_only_mode 에서 비용 0 으로 동작하게 한다. 오늘날 `portfolio.run` 은 항상 `call_persona`(유료
  API)를 타므로, 이 전환이 없으면 zero-cost CLI 레짐에서 비용 발생 또는 키 미설정 시 예외가 난다.
- **스키마 무변경.** decision 행은 이미 존재, 거부는 실행 시점. 신규 컬럼/마이그레이션 불필요.

## Milestones (우선순위 기반, 시간 추정 없음)

### Primary Goal (P-High) — portfolio.py CLI 전환 (REQ-034-9, 선행 또는 동반)

- `src/trading/personas/portfolio.py` 의 `run()` 을 decision.py CLI 분기 패턴으로 전환(MODIFIED 파일):
  - import 확장: `from trading.personas.base import call_persona, call_persona_via_cli,
    is_cli_mode_active, render_prompt`(현재 `call_persona, render_prompt` 만, portfolio.py:11).
  - `render_prompt(...)` 후 `if is_cli_mode_active(): res = call_persona_via_cli(... expect_json=True)
    else: res = call_persona(... expect_json=True)` 로 분기하여 `res` 반환. 템플릿 = decision.py
    라인 16/49/59/79. SPEC-030 daily_report 의 `call_persona_via_cli(expect_json=...)` 선례 참조.
  - 이 전환은 wiring 보다 먼저(또는 함께) 수행 — 없으면 cli_only_mode 에서 포트폴리오 호출이 비용
    발생/예외로 실질 미작동.
- 테스트는 **CLI 브리지(`call_persona_via_cli`)·`is_cli_mode_active` 를 mock** 하여 네트워크/실제
  `claude -p` 를 호출하지 않는다.
- 대상 REQ: REQ-034-9 / 검증: AC-12

### Primary Goal (P-High) — 공유 조정 헬퍼 (순수 매핑 + portfolio.run 연동)

- 순수 매핑 로직: buy/non-buy 분리 → `portfolio.run` 출력의 `adjusted_signals`(ticker→qty_adjusted)
  반영 + `rejected`(ticker) 드롭 + `qty_adjusted==0` 드롭 + 미매칭 ticker 무시 → 조정 buy + 무변경
  non-buy 재조립(시그널↔sig_id 정렬 보존).
- `is_active` 게이트(holdings<5 → no-op) + buy 없음 → no-op.
- 대상 REQ: REQ-034-1, REQ-034-2, REQ-034-4, REQ-034-5 / 검증: AC-1, AC-3, AC-4, AC-6, AC-7

### Secondary Goal (P-High) — 거부 기록 + fail-safe + 투명성

- rejected/qty_adjusted==0 드롭 시 `res.rejected.append(sid)` + audit 기록, 실행 제외(REQ-034-3).
- fail-safe: `portfolio.run` 예외(특히 `ANTHROPIC_API_KEY missing` RuntimeError base.py:234~235;
  JSON 실패 → `response_json=None`)를 try/except 로 흡수 → 미조정 입력 반환 + 로그 + 텔레그램
  (텔레그램 실패도 swallow)(REQ-034-6).
- 비자명 조정 시 `system_briefing("포트폴리오 조정", ...)` + `audit("PORTFOLIO_ADJUSTMENT", ...)`
  (REQ-034-7).
- 대상 REQ: REQ-034-3, REQ-034-6, REQ-034-7 / 검증: AC-2, AC-5, AC-9, AC-10

### Tertiary Goal (P-High) — 세 사이클 삽입 (orchestrator.py)

- pre_market(라인 907 직후 / 909 직전), event(1206 직후 / 1207 직전), intraday(1364 직후 / 1366
  직전)에 동일 호출 추가:
  ```
  signals, sig_ids = _apply_portfolio_adjustment(
      signals, sig_ids,
      holdings=assets["holdings"], holdings_count=len(assets["holdings"]),
      total_assets=assets["total_assets"],
      cash_pct=compute_balance_pcts(assets)[0],   # Q-3 권장 분모
      today=today, cycle_kind=res.cycle_kind,
  )
  ```
  `assets` 는 이미 스코프(817/1142/1278) — 중복 KIS 호출 없음. 이후 `zip(signals, sig_ids)` 가
  조정된 리스트 순회.
- 대상 REQ: REQ-034-1 / 검증: AC-8

### Final Goal (P-High) — 무회귀 + 테스트

- 단위 테스트 `tests/personas/test_portfolio_gate.py`(권장): 매핑 분기(축소/거부/qty_adjusted==0/
  미매칭/buy-only/holdings<5/폴백) + 정렬 보존 + 세 사이클 삽입(정적 또는 mock) + telegram 카테고리·
  audit 검증 + **CLI 분기(REQ-034-9, cli_only_mode 시 `call_persona_via_cli` 사용, 직접 API 미호출)**.
  `portfolio.run`/`call_persona_via_cli`/`is_cli_mode_active`/`system_briefing`/`audit`/balance
  (`assets`)는 mock — 네트워크/실제 `claude -p` 미사용.
- 무회귀: holdings<5·buy 없음·페르소나 실패 시 시그널/sig_ids 무변경 통과를 확인. 신규 헬퍼가
  decision/balance 를 호출·조회만 함을 정적·동적 확인(portfolio.py 는 REQ-034-9 로 MODIFIED).
- 대상 REQ: REQ-034-8, REQ-034-9 / 검증: AC-11, AC-12

### Optional Goal (P-Low) — portfolio_adjustments 기록 / 섹터맵 / throttle (Q 검토, defer)

- (Q-4) 기존 `portfolio_adjustments` 테이블(migration 005)에 qty_original/qty_adjusted/rationale
  추가 기록 — 관측 강화(선택, 마이그레이션 불필요).
- (Q-1, RESOLVED=defer) holdings 에 섹터 정보 보강(섹터 맵/pykrx·KIS 종목정보 조인) — 섹터 편중 판정
  정밀화. v0.1.0 은 LLM 지식 기반 best-effort 수용, 정밀 섹터맵은 향후 SPEC.
- (Q-5) intraday */15 빈도 throttle(조정 무변경 시 텔레그램 생략) — 비용/노이즈 완화, 향후 개선.

## Architecture / Design Direction

- **삽입 지점 = halt 게이트 통과 직후·execute 루프 직전.** 포트폴리오는 decision 의 산출물을 받아
  risk/execute 로 넘기기 전 마지막 사이징 단계 — SPEC-001 이 의도한 "07:55 Portfolio (Decision↔Risk
  사이)" 배치(REQ-PERSONA-04-1)와 정합. halt 상태에선 사이클이 이미 return 하므로 포트폴리오도 자연히
  스킵된다(워치독 SPEC-033 과 달리 본 SPEC 은 halt 를 우회하지 않음 — 매수 사이징은 halt 시 불필요).
- **입력 single source = 인-스코프 `assets`(=`balance()`).** holdings/total_assets/cash_pct 를
  `assets` 에서 파생해 중복 KIS 호출 회피. cash_pct 는 SPEC-029 `compute_balance_pcts`(invest_basis
  분모, 합 100%) 권장(Q-3).
- **buy-only + 정렬 보존이 핵심 불변식.** sell/hold 는 손대지 않고 sig_id 와 함께 보존하며, 조정된 buy
  와 합칠 때 `signals[i] ↔ sig_ids[i]` 정렬을 깨지 않는다(decision.py:96~121 의 위치 정렬 계약).
- **매핑 로직을 순수 함수로 격리**하여 테스트 용이성 확보. I/O(portfolio.run·telegram·audit)는 헬퍼
  엔트리에 모음.
- **additive 원칙(예외 = portfolio.py CLI 전환):** decision→risk 사이에 단계만 삽입하며 decision/
  balance 함수 정의는 호출/조회만 한다(REQ-034-8). 단 `portfolio.py` 의 `run()` 은 REQ-034-9 로
  CLI 분기 전환(MODIFIED) — 이는 wiring 이 zero-cost 레짐에서 실제 작동하기 위한 필수 변경이며,
  decision.py 의 검증된 패턴을 그대로 따른다.

## Risks and Mitigations

- R-1 (sig_id 정렬 깨짐으로 잘못된 시그널 거부/체결): buy/non-buy 분리 후 재조립 시 정렬 보존을 단위
  테스트로 강제(AC-3). ticker 매핑은 조정값 적용에만 쓰고 sig_id 짝은 위치로 유지.
- R-2 (포트폴리오 실패로 거래 중단): try/except 미조정 폴백 + 사이클 계속(AC-5). 특히
  `ANTHROPIC_API_KEY missing` RuntimeError 를 반드시 흡수.
- R-3 (sell 청산이 포트폴리오에 막힘): buy-only 입력·non-buy 무변경 보존으로 원천 차단(AC-3, SPEC-033
  정합).
- R-4 (zero-cost CLI 레짐에서 유료 API 호출/예외): **REQ-034-9 로 해소** — portfolio.run 을
  decision.py CLI 분기 패턴으로 전환하여 cli_only_mode 에서 `call_persona_via_cli`(비용 0·키 불요)
  사용. 잔여 위험은 CLI 브리지 자체 실패뿐이며 REQ-034-6 fail-safe 로 흡수.
- R-5 (섹터 데이터 부재로 편중 판정 부정확): holdings 에 섹터 없음(account.py:42~55) — **사용자 결정
  = LLM 지식 기반 best-effort 수용**(A-9). 정밀 섹터맵은 향후 SPEC(Q-1 RESOLVED=defer).
- R-6 (intraday */15 빈도 비용/노이즈): holdings≥5 + buy 존재 시에만 호출되므로 빈도 제한적. 과하면
  Q-5 throttle.
- R-7 (라인 이동): citation 라인은 작성 시점 기준이며 구현자는 `signals = (dec_res.response_json or
  {}).get("signals", [])` / `for sig, decision_id in zip(signals, sig_ids` 패턴으로 세 삽입 지점을
  재확인.

## Dependencies

- 선행 SPEC 무차단(additive). SPEC-001(포트폴리오 페르소나 정의·holdings≥5 게이트·07:55 배치·
  portfolio_adjustments 테이블)/016(자본 보전·cli_only_mode 가드)/029(balance·compute_balance_pcts)/
  033(매도 무차단 정합)/015(CLI zero-cost 레짐)/030(call_persona_via_cli expect_json 선례) 와 호환.
- 신규 마이그레이션 없음(스키마 무변경).
- portfolio.py CLI 전환(REQ-034-9)은 base.py 의 기존 `call_persona_via_cli`/`is_cli_mode_active` 를
  *사용*만 하므로 base.py 변경 불필요.

## Out of Scope

- decision/risk/execute 페르소나 로직·사이클 기타 동작·sig_ids 의미 변경, sell 청산 차단, 리스크
  한도/halt/회로차단 로직 변경, 정밀 섹터맵 보강(Q-1 — LLM best-effort 수용, 정밀화는 향후 SPEC),
  portfolio_adjustments 테이블 기록 강제(Q-4 — 선택), intraday throttle(Q-5), real 모드 notify-only
  전환. (참고: portfolio.run CLI 전환은 **in-scope** = REQ-034-9.)
