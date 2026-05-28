# SPEC-TRADING-030 — Research (codebase analysis)

_작성: 2026-05-28 · author: onigunsow · methodology: TDD_

본 문서는 SPEC-030 구현 전 현행 코드/데이터 흐름을 file:line 근거와 함께 정리한다.
**새 코드를 작성하지 않고** 사실 검증만 수행했다.

---

## 1. 현행 일일 리포트 흐름 (daily_report.py)

파일: `src/trading/reports/daily_report.py` (304 lines). 16:00 KST 매 한국 거래일 cron으로 실행.

| 함수 | line | 역할 |
| --- | --- | --- |
| `_gather_today()` | 24 | **운영 지표만** 수집. `persona_runs`(cost/토큰/캐시), `orders`, `risk_reviews`, `tool_call_log`, `reflection_rounds`, `dynamic_tickers` 에 대한 SQL. 반환 dict 키: `today, orders, runs, risk, cost, cumulative, tool_stats, reflection_stats, model_breakdown, auto_expansion_tickers` (line 125-136). |
| `_fallback_text()` | 139 | LLM 없이 운영 지표를 출력하는 평문 템플릿. `skip_reason` 인자로 LLM 생략 사유를 꼬리에 붙임 (line 223). |
| `_llm_text()` | 228 | **직접 Anthropic API** 호출 (`Anthropic(api_key=...).messages.create(model="claude-sonnet-4-6", ...)`, line 247-262). `@block_if_cli_only_mode` 데코레이터(line 227)로 cli_only_mode 에서 **항상 RuntimeError 발생**. system 프롬프트에 한국어 5~8줄·KRW-only·환각금지·이미 일어난 일만 요약 가드레일 포함 (line 252-259). |
| `_llm_skip_reason()` | 266 | 예외 → 사람이 읽을 사유 문자열. `cli_only_mode` 포함 시 "CLI 전용 모드 — 직접 API 호출 차단(정상), 결정형 요약 사용" (line 276). |
| `generate_and_send()` | 282 | `_llm_text()` 시도 → 예외 시 `_fallback_text(skip_reason=...)` 로 degrade (line 284-289). 이후 `daily_reports` UPSERT 영속화(line 292-298) + `system_briefing()` 텔레그램 전송(line 301). |

**현재 운영 결과:** cli_only_mode 활성이므로 `_llm_text()` 는 항상 `RuntimeError("... cli_only_mode ...")` 를 던지고,
리포트는 **언제나 `_fallback_text()`(운영 지표만)** 만 발송한다. 오늘자 텔레그램 리포트 꼬리가
"(CLI 전용 모드 — 직접 API 호출 차단(정상), 결정형 요약 사용)" 인 것이 증거.

---

## 2. cli_only_mode 제약 (SPEC-015/016)

- `block_if_cli_only_mode` 데코레이터: `src/trading/personas/base.py:78`. `cli_only_mode=True` 이면
  데코레이트된 함수가 `RuntimeError` 를 던지도록 강제 (line 113-119). `_llm_text` 가 직접 Sonnet API 를
  태우는 것을 **의도적으로 차단**.
- 목적: 직접 Anthropic API 과금을 막고, 페르소나를 Claude CLI **구독(subscription)** 으로 실행.
- 따라서 SPEC-030 의 LLM 내러티브는 **cli_only_mode 를 완화하지 않고** CLI 구독 경로로 생성해야 한다 (사용자 확정 결정).

---

## 3. CLI 브리지 자유 텍스트(free-form) 가능성 — 핵심 검증

함수: `call_persona_via_cli(...)` — `src/trading/personas/base.py:555`

```
call_persona_via_cli(*, persona_name, model, cycle_kind, system_prompt, user_message,
    trigger_context=None, expect_json=False, apply_memory_ops=True,
    tickers=None, input_data=None, run_context=None) -> PersonaResult   # line 555-568
```

검증된 사실:
- `expect_json` 기본값 **False** (line 563). `expect_json=False` 이면 `response_json=None` 으로 두고
  `response_text` 만 채운다 (line 680-682, 733-737). → **자유 산문(prose) 반환 가능**.
- `PersonaResult` dataclass: `src/trading/personas/base.py:195-207`. 필드 `response_text: str`(line 198),
  `response_json: dict|None`(line 199). **자유 텍스트는 `result.response_text` 로 읽는다.**
- CLI 성공 시 `persona_runs` 에 `model='cli-claude-max'`, tokens=0/cost=0 으로 기록
  (REQ-BRIDGE-02-4/6, line 698 및 주석 573-574). → **구독 경로는 과금 0**.
- CLI 실패 시 Haiku API 폴백: `call_persona(..., model=_HAIKU_FALLBACK_MODEL, ...)` (line 636, 648-658).
  이 Haiku 폴백은 **cli_only_mode 에서도 의도적으로 허용**됨 (base.py:84 주석: "the intentional Haiku
  fallback in call_persona_via_cli — that path [is not blocked]"). → 안전망. SPEC-030 의
  "cli subscription unavailable → Haiku fallback" 엣지케이스가 이 메커니즘으로 자연 충족.
- `apply_memory_ops`: 메모리 쓰기 분기는 `if apply_memory_ops and response_json:` (line 719). `expect_json=False`
  이면 `response_json=None` 이므로 분기 미발화. 그래도 SPEC-030 은 **`apply_memory_ops=False` 명시** 권장
  (리포트는 메모리에 쓰지 않음).

**기존 `_llm_text` 주석의 반증:** line 237-238 주석은 산문이 "persona-pipeline contract 에 안 맞는다"
고 주장하나, `expect_json=False` 자유 텍스트 경로가 이를 처리하므로 **재평가 필요**. (구현 시 확인 항목 → 4절)

**호출 관례 (참고 패턴):** `src/trading/personas/macro.py:54-76` — `render_prompt(...)` 로 system_prompt 생성 후
`call_persona_via_cli(persona_name=..., system_prompt=..., user_message=..., expect_json=True, input_data=...)`.
`system_prompt` 은 **평문 str 인자**이므로 daily_report 는 Jinja 템플릿 없이 인라인 문자열을 넘길 수 있다.
`is_cli_mode_active()`: `src/trading/personas/base.py:747`.

---

## 4. 구현 단계에서 run 에이전트가 확인할 미해결 항목 (OPEN QUESTION)

`call_persona_via_cli` 내부의 `build_cli_prompt(persona_name, input_data, system_prompt, user_message, tickers)`
(base.py:602) 와 `call_persona_cli(...)` (base.py:611) 가 **등록되지 않은 새 `persona_name="daily_report"`**
에 대해 정상 동작하는지 확인 필요:
- (a) `build_cli_prompt` 가 `persona_name` 별 **등록된 Jinja 템플릿/페르소나 레지스트리**를 요구하는가,
  아니면 인라인 `system_prompt` 만으로 임의 persona_name 이 동작하는가?
- (b) `expect_json=False` 일 때 최종 텍스트가 `PersonaResult.response_text` 로 들어옴을 실호출로 재확인
  (코드상 line 735 로 확인됨, 단 CLI 브리지 export/wait 경로의 실제 동작은 통합 시 검증).

→ 이것이 **run 에이전트가 코드 작성 전 반드시 해소할 단 하나의 미해결 질문**이다.

---

## 5. 재사용 가능한 데이터 소스

### 5.1 News Intelligence (SPEC-014) — 사전 계산된 매크로/마이크로 분석

| 파일 | 크기 | 구조 |
| --- | --- | --- |
| `data/contexts/intelligence_macro.md` | ~9.4KB (~2.4K토큰) | `### [투자 주목] <제목> (Impact: N/5)` + `_M sources \| 날짜 \| Keywords: ...` + `→ <대응전략>`. 검증: 15개 story, 15× "투자 주목". |
| `data/contexts/intelligence_micro.md` | ~38KB (~9.5K토큰) | 동일 포맷. 17개 story, 17× "투자 주목". |

→ 사용자가 원하는 매크로/마이크로 총평이 **이미 per-story 로 사전 계산**되어 있음(Impact 점수·키워드·대응전략 포함).
SPEC-030 은 이를 **재생성하지 않고 다이제스트로 재사용**한다.

읽기 헬퍼: `src/trading/personas/context.py:126` `_read_md(name)` — `project_root()/data/contexts/{name}` 을
읽고 미존재 시 `"_({name} 미생성 — cron 미실행 또는 첫 운영)_"` 반환 (line 129-130). intelligence_*.md 는
동일 디렉터리에 있음. 단, 이 헬퍼는 `personas/context.py` 모듈 내부 함수이므로 reports 에서 재사용하려면
공용화 또는 reports 전용 소형 헬퍼 도입 검토 (plan.md 참조).

주의(엣지): `macro_news.md`/`intelligence_macro.md` 는 주말·주간 캐던스로 stale 가능. `_read_md` 는 미존재만
처리하고 **stale 여부(파일 mtime)는 판단하지 않음** → SPEC-030 에서 mtime 기반 stale 표기 고려.

### 5.2 보유 종목 + P&L

함수: `src/trading/kis/account.py:10` `balance(client)` — KIS `inquire-balance`(VTTC8434R paper / TTTC8434R live).
반환 dict (line 76-87):
- `holdings`: list — 각 항목 `ticker, name, qty, avg_cost, current_price, eval_amount, pnl_amount, pnl_pct`
  (line 42-55). `hldg_qty > 0` 만 포함.
- 요약: `cash_d2, buyable, buyable_effective, total_assets, stock_eval, invest_basis(=cash_d2+stock_eval, %분모), pnl_total`.
- `rt_cd != "0"` 시 `KisError` raise (line 33-34) → 호출부에서 예외 처리 필요(엣지: API 실패).

추가로 `positions` 테이블이 KIS 보유분을 미러링(SPEC-029 v0.2.0, 배포 완료) — DB 경로 대안 가능하나
헤드라인 P&L 은 `balance()` 라이브가 1차 소스.

---

## 6. 테스트 인프라

- 테스트 위치: `tests/reports/` (이미 존재). 기존: `test_daily_report_extensions.py`,
  `test_daily_report_llm_skip.py`, `test_daily_report_spec023.py`.
- pytest + 85% 커버리지(`.claude/rules/moai/languages/python.md`). 외부 의존(Anthropic, KIS, CLI 브리지, DB)은
  monkeypatch/mock 으로 격리.

---

## 7. 요약 (한 줄)

자유 텍스트 CLI 구독 경로(`call_persona_via_cli(expect_json=False)`)는 **코드상 실현 가능**하고 과금 0이며,
매크로/마이크로 총평 재료는 `intelligence_*.md` 에 **사전 계산되어 있다**. 남은 단 하나의 확인은
**임의 persona_name 에 대한 `build_cli_prompt`/CLI 브리지 동작**(4절)이다.
