---
id: SPEC-TRADING-053
version: 0.5.0
status: draft
created: 2026-06-18
created_at: 2026-06-18
updated: 2026-06-19
author: manager-spec
priority: high
issue_number: null
labels: ["trading", "cli", "cost", "api-zero-leak", "news", "personas"]
---

# SPEC-TRADING-053 — 유료 Anthropic API 비용 누수 완전 차단 (CLI-only 강제, CLI 실패 시 폴백 금지·스킵)

## HISTORY

- 2026-06-19 v0.5.0 (draft): plan-auditor iteration 4 — D-CRIT-1 RESOLVED 확정. 문서 정확성 4건 표면 교정(surgical). **D1:** spec-compact.md "6개 유료 호출 지점"→"5개"(실제 messages.create=analyzer.py:235·base.py:280/353/408·daily_report.py:436=5; "6"은 디스패처 6개만 지칭). **D2:** graceful-skip catch 경계 정정 — 디스패처는 로컬 try/except 없음. `call_persona` 진입점 raise는 디스패처를 지나 scheduler `_wrap`(runner.py:158 `except Exception`→"failed")과 orchestrator per-persona except(orchestrator.py:1123-1125, 재-raise)에서 흡수 → cost-0 사이클 스킵(REQ-053-G2·AC-2c 재기술). **D3:** REQ-053-G1에 배치 제약 명시 — 진입점 가드는 `call_persona`의 `try:`(base.py:263) **이전**·280/353/408 도달 전 실행(안 그러면 내부 `except`(393)이 RuntimeError를 삼켜 `PersonaResult(error=...)`로 변환→디스패처가 빈 결과를 유효 결정으로 오인). **D4:** `CLI_DEGRADED_DEFER` 차단 audit emit 지점 명시(데코레이터 base.py:132-138은 raise만·audit 없음; 진입점 가드 raise 직전 emit).
- 2026-06-19 v0.4.0 (draft): plan-auditor iteration 3 FAIL(0.58) — **D-CRIT-1 진짜 페르소나 누수 봉인(통과 잔여 결함)**. 6개 페르소나 디스패처(decision/micro/portfolio/macro/risk/retrospective)가 `if is_cli_mode_active(): call_persona_via_cli else: call_persona`로 분기하는데, `is_cli_mode_active()`(base.py:949)는 `is_cli_only_mode()`와 **다른 함수**로 `cli_personas_enabled=False` **또는 워처 heartbeat stale**이면 False·**strict 미참조**. else `call_persona`(224)는 가드 없이 messages.create(280/353/408) 직접 호출 → strict ON + CLI/워처 死(이 SPEC이 막으려는 시나리오)에서 유료 누수. C/D로 안 막힘. **신규 REQ-053-G:** `call_persona` 진입점에서 should_defer_paid_call 가드(주 방어, 라우팅 무관 3지점 원천 봉인) + graceful skip 계약. **REQ-053-D 보강:** `is_cli_mode_active()` strict 인지화(보조 방어). **거짓 전제 교정:** "call_persona는 call_persona_via_cli 폴백으로만"(REQ-053-B5·spec.md 본문·§6 #2/#3/#4) → 디스패처 else가 직접 호출함을 정정. AC-2c 재작성(824가 else 경로 미차단). frontmatter created_at(D-min-1)·raise 인용 132→133(D-min-2). EARS 6→7(G 추가).
- 2026-06-18 v0.3.0 (draft): plan-auditor iteration 2 FAIL(0.68) — iteration 2가 만든 3개 신규 모순 해소(최종 반복). **D-new2:** ADR-004 모순 해소 — "본문 graceful 반환" 폐기, **"데코레이터 raise + 호출자 catch = graceful skip"** 정식 채택(소스: analyzer.py:631/637 호출자가 이미 `except Exception`으로 흡수·pending 보존). `is_cli_only_mode()` strict 인지화는 단일 SSOT 유지, raise는 cli_only와 동일 계약(모순 없음). ADR-002 재작성. **D-new1:** AC-1b 허위 흐름 제거 — `_llm_text`는 production 호출자 0(휴면), `generate_and_send`는 `_narrative_text→call_persona_via_cli`(REQ-053-C) 경유. daily-report live 커버리지를 narrative 경로로 명시, `_llm_text`는 단위 테스트(직접 호출 시 strict ON raise·유료 0)로 단언. **D-new3:** 카운터 리셋 위치 교정 — auto-disable 부수효과만 strict 게이트, **리셋(`_cli_failure_count=0`)은 strict 무관 항상** 수행(무한 증가 방지, E5 일치). **D-new4:** 콜드스타트 빈 캐시 시 fail-OPEN(strict-OFF 불변 HARD 우선), fail-closed는 last-known ON 관측 시만. **D-new5:** scheduler.py:204 4번째 호출자 blast-radius 추가(REQ-053-B6).
- 2026-06-18 v0.2.0 (draft): plan-auditor iteration 1 FAIL(0.55) 8개 결함 반영. ① labels 추가(D1). ② 다섯 번째 유료 호출 지점 `daily_report.py:436` `_llm_text` 발견·포함 — 공유 가드 `block_if_cli_only_mode`/`is_cli_only_mode`를 strict 인지로 만들어 analyzer.py:235와 daily_report.py:436을 **한 번에** 차단(D2, ADR-004). ③ 5개 유료 호출 지점 전부에 PAID_CALL 사전 계측 요구 신규 REQ-053-F(D3). ④ §5 게이트 #2를 관측 가능 PAID_CALL 0건 + cost=0 audit로 재서술(D3). ⑤ §1 zero-leak 불변식을 strict fail-closed로 강화(ADR-005, D5). ⑥ strict-ON 디스패처 라우팅 분석·차단 요구(D6, REQ-053-B5). EARS 라벨 오기 교정(A4 State→Ubiquitous).
- 2026-06-18 v0.1.0 (draft): 최초 작성. SPEC-052(strict_cost_zero_mode latch/조기경고)와 SPEC-016(CLI 라우팅·폴백)을 폐기하지 않고 보완·강화. 어제/오늘 로그·소스에서 직접 확인된 4개 근본원인을 코드 한정으로 봉인. REQ-053-A~E 5개 모듈.

---

## 1. 개요 (Overview)

운영자의 절대 요구는 단순하다: **"유료 Anthropic API는 절대 쓰지 마라. 이미 CLI로 처리하게 만들었는데 왜 자꾸 유료 호출이 발생하나."**

이 SPEC의 목표는 다음 두 가지 불변식을 코드로 보장하는 것이다.

1. **(Zero-leak 불변식)** `strict_cost_zero_mode`가 ON인 동안, 알려진 5개 유료 호출 지점 어디에서도 `client.messages.create()`가 **단 한 건도** 발생하지 않는다. **이 불변식은 절대적이며 fail-open 예외를 두지 않는다** — DB 가용성이 불확실해도 strict ON으로 알려진 마지막 상태가 유지되면 차단(스킵) 쪽으로 결정한다(strict fail-closed, ADR-005). CLI 경로가 정상인 경우에도 strict OFF이면 유료 호출은 SPEC-016의 허용된 예외로 남는다(아래 carve-out 참조).
2. **(Graceful skip 불변식)** CLI 경로가 죽으면 유료 API로 **폴백하지 않고**, 해당 사이클/작업을 graceful 스킵(비용 0)한다. pending 파일은 다음 CLI 슬롯을 위해 보존한다.

**Carve-out (정직한 한정):** 위 zero-leak 불변식은 `strict_cost_zero_mode`가 ON일 때만 절대적이다. strict OFF(기본)에서는 SPEC-016 폴백 유료 경로가 허용된 예외로 남는다(REQ-052-C2). 즉 "유료 0건"은 **strict ON 게이트 하에서의 불변식**이며, 운영자는 현재 strict ON을 적용 중이다(§1.2).

이는 SPEC-052(REQ-052)와 SPEC-016(REQ-016)을 **보완·강화**하는 것이지 폐기가 아니다. `strict_cost_zero_mode` 기본 OFF 동작(SPEC-016 폴백)은 바이트 단위로 보존한다.

### 1.1 검증된 근본원인 (Ground Truth — 재조사 불필요)

어제(2026-06-17)·오늘(2026-06-18) 로그와 소스에서 직접 확인된 5개 누수 경로:

1. **[SCRIPT PATH ROT]** `scripts/analyze_news.sh:16` 및 `scripts/daily_screen.sh:16`이
   `CLAUDE="/home/onigunsow/.nvm/versions/node/v24.13.0/bin/claude"`를 가리키지만 이 바이너리는 더 이상 존재하지 않는다(`logs/analyze_news.log`에 "그런 파일이나 디렉터리가 없습니다" 반복).
   작동하는 바이너리는 `/home/onigunsow/.local/bin/claude`(심볼릭 링크 확인됨)이며 `scripts/persona_watcher.sh`는 commit b37ae52에서 이미 교정되었다.
   결과: 뉴스 CLI 분석이 매 실행마다 실패 → 직접 유료 API 경로로 흘러 들어감 → 2026-06-18 22:16 컨테이너 로그 `trading.news.intelligence.analyzer Haiku batch ... 'Your credit balance is too low to access the Anthropic API'`. 이것이 **주요 누수**이자 뉴스 기사가 사라진 원인이기도 하다.

2. **[SHARED GUARD MISMATCH + RAISE-ON-BLOCK]** 두 개의 직접 유료 호출 지점이 **같은** `@block_if_cli_only_mode` 데코레이터를 공유한다:
   - `src/trading/news/intelligence/analyzer.py:213` `_call_haiku` (Haiku, analyzer.py:235 `messages.create`)
   - `src/trading/reports/daily_report.py:408` `_llm_text` (Sonnet, daily_report.py:436 `messages.create`) — 현재 휴면(DEPRECATED, no caller per REQ-030-7)이나 깨진 가드에 "단 한 건도" 불변식을 맡길 수 없다.

   이 공유 가드의 실제 결함(근본원인 #2a, 단일):
   - 데코레이터 판정 함수 `is_cli_only_mode()`(base.py:82-103)는 `cli_only_mode OR cli_personas_enabled`만 본다. `strict_cost_zero_mode`는 **전혀 참조하지 않는다.** 따라서 strict ON + `cli_only_mode=False` + `cli_personas_enabled=False`(근본원인 #3의 자동전환 결과) 조합에서 가드가 통과되어 **두 지점 모두** 유료 호출이 나간다. → 가장 깔끔한 수정은 **공유 술어를 strict 인지로 만들어 한 번의 수정으로 두 지점(및 scheduler.py:204)을 동시에 차단**하는 것이다(ADR-004).
   - 참고: 데코레이터는 차단 시 `RuntimeError`를 raise(base.py:133, `if`는 132)한다. iteration 3 결정에 따라 이 raise는 **결함이 아니라 채택된 차단 메커니즘**이다 — `_call_haiku` 호출자(analyzer.py:631/637)가 이미 `except Exception`(632/638)으로 raise를 catch해 비용 0·pending 보존으로 처리하기 때문이다. 따라서 "본문이 graceful 반환"을 요구하지 않고 "raise + 호출자 catch = graceful skip" 패턴을 정식 채택한다(ADR-002 재작성).

5. **[PERSONA DISPATCHER DIRECT LEAK — CRITICAL, D-CRIT-1]** 6개 페르소나(decision.py:104/134, micro.py:78/93, portfolio.py:37/50, macro.py:65/78, risk.py:96/114, 그리고 분기 없이 항상 직접 호출하는 retrospective.py:60)는
   `if is_cli_mode_active(): call_persona_via_cli(...) else: call_persona(...)`로 분기한다.
   - `is_cli_mode_active()`(base.py:949-981)는 위 #2의 `is_cli_only_mode()`와 **완전히 다른 함수**다. `cli_personas_enabled=False`이거나 **워처 heartbeat가 stale이면**(`cli_personas_enabled=True`여도) **False**를 반환하고, **`strict_cost_zero_mode`를 전혀 참조하지 않는다.**
   - else 가지의 `call_persona`(base.py:224)는 **데코레이터도 `should_defer_paid_call`도 없이** 280/353/408에서 `messages.create`를 직접 호출한다. `should_defer_paid_call()`은 오직 `call_persona_via_cli`(824) **안에만** 있어 else 경로는 거기 들어가지 않는다.
   - **결과(진짜 누수):** strict ON + CLI/워처 死(이 SPEC이 막으려는 바로 그 시나리오) → `is_cli_mode_active()` False → 분기 else → 직접 `call_persona` → 유료 호출. REQ-053-C/D로 **안 막힌다.** → 신규 REQ-053-G(진입점 가드)가 봉인한다.
   - **[거짓 전제 교정]** v0.3.0까지의 "`call_persona`는 `call_persona_via_cli`의 폴백으로만 도달한다"는 **사실과 다르다.** 6개 디스패처가 `is_cli_mode_active()` False(워처 stale + `cli_personas_enabled=True` 포함)일 때 else로 **직접** 호출한다. `should_defer_paid_call()`(824)은 이 직접 경로를 덮지 못한다.

3. **[SELF-DEFEATING GUARD]** `src/trading/personas/base.py:577` `should_defer_paid_call()`는
   `strict_cost_zero_mode AND (cli_only_mode OR cli_personas_enabled)`일 때만 차단(True)을 반환한다.
   그런데 `_record_cli_failure()`(base.py:710-712)는 `_CLI_AUTO_DISABLE_THRESHOLD`(3) 연속 CLI 실패 후
   `cli_personas_enabled=False`로 자동전환한다. 즉 **CLI가 죽는 순간 — 가드가 가장 필요한 시점 —**
   `cli_personas_enabled`가 false로 뒤집혀 `should_defer_paid_call()`이 False를 반환하고 유료 폴백이 뚫린다.
   자기모순(self-defeating) 가드. (DB는 직전 auto_disable로 `cli_personas_enabled=false` 상태였으나 운영자가 이미 수동 리셋. 코드 버그는 잔존.)

4. **[WATCHDOG DUPLICATION]** `scripts/persona_watcher.sh`에 단일 인스턴스 가드(flock/lockfile)가 없다.
   systemd user unit `persona-watcher.service`로 관리되는데, 재시작 시 고아(orphan) 이전 인스턴스가 서비스 cgroup 밖에서 살아남아 두 워처가 동시에 같은 task 파일을 두 번 처리했다
   (`logs/persona_watcher.log` 2026-06-18 16:00에 `daily_report_daily_1781766000878.json` 중복 "Processing"/"Done"). 운영자가 고아를 이미 죽였으나 재발 방지가 필요.

### 1.2 운영 전제 (이미 적용됨 — 코드 범위 밖)

- 고아 워처 종료, DB `cli_personas_enabled=true` / `strict_cost_zero_mode=true` 설정은 **운영자가 이미 적용**했다.
- 호스트 claude CLI 재인증, 크레딧 충전은 운영자 책임(범위 밖).
- 이 SPEC은 **코드만** 다룬다.

---

## 2. EARS 요구사항 (Requirements)

### REQ-053-A: 셸 스크립트 CLI 경로 견고화 (CLI Path Resolution)

- **REQ-053-A1 (Ubiquitous):** `scripts/analyze_news.sh`, `scripts/daily_screen.sh`, 그리고 `grep -rln "nvm/versions/node" scripts/`로 발견되는 모든 `scripts/*.sh`는, claude CLI 바이너리를 다음 우선순위로 해소(resolve)해야 한다(shall): (1) `command -v claude`, (2) 폴백 `/home/onigunsow/.local/bin/claude`.
- **REQ-053-A2 (Unwanted/If-then):** **만약** 위 두 경로 모두 실행 가능한 바이너리로 해소되지 **않으면**, 스크립트는 명확한 ERROR를 로그에 남기고 non-zero로 종료해야 하며(shall), 어떤 유료 API 경로도 발동시키지 않아야 한다(shall not).
- **REQ-053-A3 (Event-Driven):** 교정 후 `analyze_news.sh`가 실행될 **때**, 해소된 바이너리로 호출하여 "Sending NNN lines to Claude CLI"를 로그에 남기고 "그런 파일이나 디렉터리가 없습니다"(no such file) 오류가 없어야 한다(shall).
- **REQ-053-A4 (Ubiquitous):** `daily_screen.sh`의 기존 기계적 폴백(mechanical top-20, daily_screen.sh:62-74)은 유료 API가 아닌 순수 로컬 폴백이므로 **보존**해야 한다(shall). 이 폴백은 비용 0이며 변경 대상이 아니다.

### REQ-053-B: 공유 strict-인지 가드 (raise + 호출자 catch = graceful skip)

설계 근거(ADR-004 재작성): `_call_haiku`(analyzer.py:235)와 `_llm_text`(daily_report.py:436)는 같은 `@block_if_cli_only_mode` 데코레이터를 공유하고, 그 데코레이터는 `if is_cli_only_mode(): raise RuntimeError`다(base.py:132). 따라서 **공유 술어 `is_cli_only_mode()`를 strict 인지로 확장**하면(단일 SSOT), strict ON에서도 데코레이터가 raise한다 — cli_only와 **동일한 계약**이므로 모순이 없다. graceful skip은 **함수 본문의 graceful 반환이 아니라 호출자가 예외를 catch**하여 비용 0·pending 보존으로 달성한다(기존 작동 패턴 채택). 소스 확인: `_call_haiku` 호출자(analyzer.py:631/637)는 이미 `except Exception`(632/638)으로 감싸 raise를 흡수하고 pending을 보존한다 → AC-1 관측 결과(유료 0건·pending 보존)는 그대로 성립한다.

- **REQ-053-B1 (State-Driven):** `strict_cost_zero_mode`가 ON이거나 (기존) `cli_only_mode`/`cli_personas_enabled`가 활성인 **동안**, 공유 술어 `is_cli_only_mode()`는 True를 반환해야 한다(shall). 즉 차단 조건이 `cli_only_mode OR cli_personas_enabled` **OR `strict_cost_zero_mode`**로 확장된다(단일 SSOT). 이로써 데코레이터가 보호하는 모든 직접-API 함수(`_call_haiku`, `_llm_text`)가 strict ON에서 유료 `client.messages.create()`에 도달하지 못한다(shall not).
- **REQ-053-B2 (Event-Driven, raise + 호출자 catch):** 위 차단 조건에서 보호 함수가 호출될 **때**, 데코레이터는 `RuntimeError`를 raise해야 하며(shall), **호출자가 그 예외를 catch하여** 비용 0으로 스킵해야 한다(shall) — `_call_haiku` 호출자(analyzer.py:631/637의 기존 `except Exception`)는 raise를 흡수하고 pending 파일을 다음 CLI 슬롯을 위해 보존한다. 이 흐름은 신규 코드가 아니라 기존 catch 경로 재사용이다(run 단계에서 두 catch 분기가 pending을 보존하는지 직접 확인).
- **REQ-053-B3 (Event-Driven, audit emit 지점 명시 — D4):** 차단(raise) 발생 **시**, `CLI_DEGRADED_DEFER` 스타일 audit가 emit되어야 한다(shall). **emit 위치:** 데코레이터(base.py:132-138)는 현재 raise만 하고 audit가 없으므로, audit는 **호출자 catch 분기**에 추가한다 — `_call_haiku`의 경우 호출자 analyzer.py가 이미 `NEWS_INTEL_HAIKU_FAIL` audit(analyzer.py:641)를 emit하므로, 그 catch 분기에서 차단 사유(strict/cli-only defer)를 `CLI_DEGRADED_DEFER`로 구분 emit하거나 기존 `NEWS_INTEL_HAIKU_FAIL` details에 `reason=cli_degraded_defer`를 부가한다(run에서 택일). 즉 "차단했음"이 관측 가능해야 한다(silent 금지).
- **REQ-053-B4 (Unwanted):** `strict_cost_zero_mode`가 OFF(기본)이고 `cli_only_mode`/`cli_personas_enabled`가 비활성인 경우의 동작은 SPEC-016 기존 동작과 동일해야 한다(shall) — 즉 데코레이터는 raise하지 않고 폴백 유료 경로가 허용된 예외로 보존된다(REQ-052-C2 불변). 기존 `cli_only_mode=True` raise 계약도 그대로 보존된다(SPEC-016 테스트 의존).
- **REQ-053-B5 (State-Driven, 디스패처 라우팅 — 거짓 전제 교정):** `strict_cost_zero_mode`가 ON인 **동안**, base.py의 직접 유료 경로 `call_persona`(messages.create 280/353/408)는 가드 없이 도달 가능해서는 안 된다(shall not). **[교정]** 소스 확인 결과 `call_persona`는 `call_persona_via_cli`의 폴백으로만 호출되는 것이 **아니다** — 6개 페르소나 디스패처가 `is_cli_mode_active()` False(워처 stale + `cli_personas_enabled=True` 포함)일 때 else로 **직접** 호출한다(근본원인 #5). `should_defer_paid_call()`(824)은 `call_persona_via_cli` 안에만 있어 이 직접 경로를 덮지 못한다. 따라서 봉인은 REQ-053-C가 아니라 **REQ-053-G(call_persona 진입점 가드)** 가 담당한다.
- **REQ-053-B6 (State-Driven, scheduler 4번째 호출자):** `is_cli_only_mode()` strict 인지화는 `scheduler.py:204`(`if is_cli_only_mode(): defer to next slot`)에도 영향을 준다. strict ON인 **동안**, 이 지점은 Haiku 폴백을 시도하지 않고 다음 슬롯으로 defer해야 한다(shall) — strict 의도와 부합하는 유익한 부수효과다(누수 방지 강화). blast-radius enumeration은 ADR-004 참조.

### REQ-053-G: call_persona 진입점 가드 + graceful skip (D-CRIT-1 주 방어)

근거(D-CRIT-1): 6개 페르소나 디스패처가 `is_cli_mode_active()` False일 때 else로 `call_persona`(base.py:224)를 직접 호출하며, 이 경로는 데코레이터도 `should_defer_paid_call`도 거치지 않는다(근본원인 #5). 디스패처 라우팅을 고치는 것(보조 방어, REQ-053-D)만으로는 라우팅 분기마다 누락 위험이 있으므로, **`call_persona` 진입점 자체에서 원천 봉인**한다(주 방어, 더 견고).

- **REQ-053-G1 (State-Driven, 주 방어 + 배치 제약):** `should_defer_paid_call()`이 True인 **동안**(strict ON 포함), `call_persona`(base.py:224)는 `messages.create`(280/353/408) 어느 지점에도 도달하기 전에, 진입점에서 `should_defer_paid_call()`을 호출해 유료 호출을 수행하지 않아야 한다(shall not). 디스패처 라우팅 결과와 **무관하게** 3개 유료 지점이 원천 봉인된다.
  - **[HARD] 배치 제약(D3):** 이 진입점 가드는 `call_persona`의 `try:` 블록(base.py:263) **이전**, 그리고 280/353/408 도달 **전**에 실행되어야 한다(shall). `try:` 블록 안에서 raise하면 내부 `except Exception`(base.py:393)이 그 `RuntimeError`를 삼켜 `PersonaResult(error=...)`로 변환하고, 디스패처가 이 빈 결과를 유효 결정으로 오인한다(REQ-053-G2가 막으려는 바로 그 "잘못된 결정"). 따라서 가드 raise는 `try:` 밖에서 전파되어야 한다(shall).
- **REQ-053-G2 (Event-Driven, graceful skip via 상위 경계):** 위 차단 조건에서 `call_persona`가 호출될 **때**, 기존 `call_persona_via_cli`(base.py:824/844)의 defer와 동일한 `RuntimeError` 계열 신호를 발생시켜야 한다(shall). **[정정 D2]** 6개 디스패처(decision.py:134, risk.py:114, macro.py:78, micro.py:93, portfolio.py:50, retrospective.py:60)는 `call_persona` 호출에 대한 **로컬 try/except가 없다.** 따라서 가드 raise는 디스패처를 그대로 통과해 상위 경계 — (a) 스케줄러 `_wrap`(`src/trading/scheduler/runner.py:158` `except Exception`이 "failed" 로깅) 또는 (b) 오케스트레이터 per-persona except(`src/trading/personas/orchestrator.py:1123-1125`, 텔레그램 후 재-raise→상위 `_wrap`) — 에서 흡수되어 **cost-0 사이클 스킵**으로 귀결된다(shall). 즉 디스패처는 catch하지 않으며, `res`(PersonaResult)가 생성되지 않으므로 잘못된 결정이 만들어지지 않는다. "유료 0 + 사이클 graceful 진행"이 SPEC 불변이다.
- **REQ-053-G3 (Ubiquitous, 계측):** `call_persona`의 3개 유료 지점(280/353/408)은 REQ-053-F의 PAID_CALL 사전 계측을 적용해야 한다(shall) — 실제 발동 시 PAID_CALL. **차단 시 `CLI_DEGRADED_DEFER` audit는 진입점 가드가 raise하기 직전에 emit한다(shall, D4)** — 데코레이터(base.py:132-138)는 현재 raise만 하고 audit가 없으므로, 차단 관측성(§5 게이트 5·AC)은 이 진입점 emit에 의존한다.
- **REQ-053-G4 (Unwanted):** `should_defer_paid_call()`이 False인 경우(strict OFF 등)의 동작은 SPEC-016 기존 직접-API 경로와 동일해야 한다(shall) — 진입점 가드는 strict ON 게이트에만 작동, strict OFF 폴백 유료 경로는 보존(REQ-052-C2 불변).

### REQ-053-F: 5개 유료 호출 지점 PAID_CALL 사전 계측 (관측 가능성)

근거(D3): 현행 `_log_paid_call`은 `_record_cli_failure`(폴백 경로)에서만 emit된다. 직접 경로(`call_persona` 280/353/408, `daily_report._llm_text` 436)와 analyzer(235)는 호출 직전 PAID_CALL을 로깅하지 않아, 실제 과금이 나가도 로그가 0줄 → §5 게이트가 거짓 PASS한다. 또 "messages.create 0건"은 로그로 관측 불가하다.

- **REQ-053-F1 (Ubiquitous):** 알려진 5개 유료 호출 지점 — analyzer.py:235, base.py:280, base.py:353, base.py:408, daily_report.py:436 — 각각은 실제 `client.messages.create()` 호출 직전에 `_log_paid_call`(또는 동등한 PAID_CALL 구조화 로그: persona/path/model/reason 스키마)을 emit해야 한다(shall). 그래야 "strict ON 동안 PAID_CALL 0건"이 진짜 증명이 된다.
- **REQ-053-F2 (Ubiquitous, emit 지점):** 계측은 차단(defer)된 경우에도 관측 가능해야 한다 — 차단 시 `CLI_DEGRADED_DEFER`, 실제 발동 시 `PAID_CALL`. emit 위치(D4): analyzer 경로는 호출자 catch(analyzer.py:641 `NEWS_INTEL_HAIKU_FAIL` 인접, REQ-053-B3)에, `call_persona` 경로는 진입점 가드 raise 직전(REQ-053-G3)에 emit. 두 사건은 구분 가능한 로그/audit로 남아야 한다(shall).
- **REQ-053-F3 (Unwanted):** 계측 추가는 strict OFF 동작의 호출 횟수·반환값·예외 거동을 변경해서는 안 된다(shall not) — 로깅 사이드이펙트만 추가(REQ-052-C2 불변).

### REQ-053-C: should_defer_paid_call를 cli_personas_enabled에서 분리 (Decoupling)

- **REQ-053-C1 (State-Driven):** `strict_cost_zero_mode`가 ON인 **동안**, `should_defer_paid_call()`은 `cli_personas_enabled` / `cli_only_mode` 값과 **무관하게** True(차단)를 반환해야 한다(shall). 근거: strict 모드는 "절대 유료 호출 안 함"을 의미한다.
- **REQ-053-C2 (Unwanted):** `strict_cost_zero_mode`가 OFF인 경우 `should_defer_paid_call()`은 False를 반환해야 한다(shall) — SPEC-016 동작 불변(REQ-052-C2와 동일).
- **REQ-053-C3 (State-Driven, strict fail-closed — ADR-005):** `get_system_state()`가 DB 예외로 실패하는 **동안**, `should_defer_paid_call()`은 다음 규칙을 따라야 한다(shall):
  - **직전 알려진(last-known) strict 상태가 ON이면 True(차단=fail-closed).** strict ON으로 실제 관측된 캐시가 있을 때만 fail-closed가 적용된다.
  - **직전 strict 상태를 알 수 없으면(빈 캐시 = 콜드스타트, 부팅 직후 첫 호출 전 + DB 예외) False(fail-open).** [D-new4 교정] 빈 캐시에서 True를 반환하면 실제 strict-OFF 시스템의 정당한 SPEC-016 폴백을 막아 "strict OFF 바이트 단위 불변"(REQ-052-C2)을 위반한다. strict-OFF 불변식이 HARD 우선이므로 콜드스타트는 fail-open으로 기본한다. (strict ON 시스템에서 "부팅 첫 호출 + DB 다운"이 겹치는 창은 극히 드물고, 다음 호출에서 캐시가 채워지면 즉시 fail-closed로 전환된다.)
  - **strict가 직전에 OFF로 알려진 경우 False**(SPEC-016 동작 보존).
  - last-known strict 상태는 추가 DB 컬럼 없이 in-process 캐시(가장 최근 성공한 `get_system_state()`의 `strict_cost_zero_mode` 값)로 보존한다 — 마이그레이션 불필요(Exclusion #2 준수).
  - 트레이드오프(last-known ON + DB 장애 시 전 사이클 스킵 가능)는 plan.md Risks R1에 명시한다.

### REQ-053-D: strict 모드에서 _record_cli_failure가 cli_personas_enabled를 끄지 않음

- **REQ-053-D1 (State-Driven):** `strict_cost_zero_mode`가 ON인 **동안**, `_record_cli_failure()`는 `_CLI_AUTO_DISABLE_THRESHOLD` 연속 실패에 도달해도 `cli_personas_enabled=False` 자동전환을 수행하지 않아야 한다(shall not). CLI 라우팅 의도를 유지하여 가드가 무장(armed) 상태로 남게 한다.
- **REQ-053-D2 (Unwanted):** `strict_cost_zero_mode`가 OFF인 경우 `_record_cli_failure()`의 기존 자동전환 동작(3연속 → `cli_personas_enabled=False`, audit "CLI_AUTO_DISABLED", 텔레그램 알림)은 변경 없이 보존해야 한다(shall).
- **REQ-053-D3 (Ubiquitous):** REQ-053-C와 REQ-053-D는 방어심층(defense-in-depth) 관계다 — C는 가드를 플래그에서 분리(주), D는 플래그 churn을 방지(보조). 둘 다 구현하되 strict OFF 동작은 불변이어야 한다(shall).
- **REQ-053-D4 (State-Driven, is_cli_mode_active strict 인지화 — D-CRIT-1 보조 방어):** `strict_cost_zero_mode`가 ON인 **동안**, `is_cli_mode_active()`(base.py:949)는 (워처 stale 등으로) False를 반환해 디스패처를 else(직접 `call_persona`)로 빠지게 해서는 안 된다(shall not) — strict ON이면 디스패처가 `call_persona_via_cli`로 가도록 strict 인지를 추가한다(거기서 defer). 단 이는 **보조(라우팅 안전망)**이며 주 방어는 REQ-053-G(진입점 가드)다. strict OFF 동작은 불변(shall).

### REQ-053-E: persona_watcher.sh flock 단일 인스턴스 가드

- **REQ-053-E1 (Event-Driven):** `persona_watcher.sh`가 시작될 **때**, flock 기반 단일 인스턴스 가드를 스크립트 최상단에서 획득해야 한다(shall) (예: `exec 200>/tmp/persona_watcher.lock; flock -n 200 || exit 0`).
- **REQ-053-E2 (Unwanted/If-then):** **만약** 두 번째 인스턴스가 시작되어 락 획득에 실패하면(이미 실행 중), 메시지를 로그/출력 후 즉시 `exit 0`으로 깨끗하게 종료해야 하며(shall), 어떤 task 파일도 처리하지 않아야 한다(shall not).
- **REQ-053-E3 (Ubiquitous):** flock 가드는 systemd `Restart=` 의미와 호환되어야 한다(shall) — 정상 재시작 시 이전 인스턴스가 종료되면 새 인스턴스가 락을 획득할 수 있어야 한다.

---

## 3. Exclusions (What NOT to Build)

1. **`anthropic` import 또는 API 경로 자체를 제거하지 않는다.** API 경로(analyzer `_call_haiku`, `call_persona`, `_llm_text` 포함)는 strict OFF 모드의 명시적 폴백으로 남는다(REQ-052-C2). 우리는 오직 게이팅(gate)·계측(instrument)만 한다. `daily_report._llm_text`는 휴면이지만 삭제하지 않고 strict 가드로 덮는다(D2) — SPEC-016 테스트·non-cli-only 배포의 직접-API 경로 보존.
2. **DB 스키마 / 마이그레이션을 변경하지 않는다.** 새 컬럼 없음 — `strict_cost_zero_mode`, `cli_personas_enabled`, `cli_only_mode`는 이미 존재한다.
3. **trading / sizing / decision 로직을 변경하지 않는다.** 시장 중립 순수함수 코어 보존.
4. **billing / 크레딧 충전을 건드리지 않는다** (운영자 책임).
5. **호스트 claude CLI 재인증을 자동화하지 않는다** (운영자 수동, 범위 밖).
6. **`scripts/persona_watcher.sh`의 CLI 경로는 이미 b37ae52에서 교정됨** — 본 SPEC은 거기에 flock만 추가한다(CLI 경로 재변경 안 함).

---

## 4. Constraints

- 시장 중립 순수함수 코어 보존. 기존 테스트 스위트 **회귀 0**(SPEC-052 기준 ~1702 passed).
- `strict_cost_zero_mode` 기본 OFF 동작(REQ-052-C2: OFF → SPEC-016 폴백 보존)은 **바이트 단위로 불변**. 모든 신규 차단 동작은 strict ON에 게이트된다.
- 가드 내 DB 접근 실패 시: **strict 직전 상태가 ON이면 fail-closed(차단)**, OFF로 알려졌으면 기존 fail-open(False)(ADR-005, REQ-053-C3). strict 모드의 "절대 API 금지" 의미를 DB 장애 중에도 보존하되, strict OFF는 불변(plan.md Risks R1에 트레이드오프 명시).
- Python 3.13+, ruff/pytest, 타입힌트 준수. 셸은 `set -euo pipefail` 호환.

---

## 5. Verification Gate (검증 게이트 — run+redeploy 후 기록)

다음을 모두 관측해야 통과로 본다:

1. 다음 뉴스 슬롯(08:10/11:10/14:40/22:10)에서 `analyze_news.log`에 "Sending NNN lines to Claude CLI" 성공 로그가 찍히고 "그런 파일이나 디렉터리가 없습니다"(no such file) 오류가 **0건**.
2. `strict_cost_zero_mode`가 ON인 동안 컨테이너 로그에 **관측 가능한 `PAID_CALL` 0건**(5개 유료 호출 지점 전부 REQ-053-F로 계측됨 — 즉 실제 과금이 나갔다면 반드시 PAID_CALL 로그가 남으므로 "0건"이 진짜 증명이 됨). 추가로 `persona_runs.cost_krw`/뉴스 토큰 회계의 신규 비용 기록이 **cost=0**임을 audit로 확인. ("messages.create 0건" 같은 로그 불가 표현은 사용하지 않는다 — REQ-053-F가 messages.create 직전 PAID_CALL을 계측하므로 PAID_CALL 0 = messages.create 0의 관측 가능한 대리지표다.)
3. persona_watcher.service 재시작 후에도 `persona_watcher.log`에 동일 task 중복 "Processing"/"Done" **0건**(단일 인스턴스).
4. 뉴스 기사가 다시 대시보드/DB에 나타남(CLI 분석 경로 복원).
5. (strict ON 동안) `CLI_DEGRADED_DEFER` audit/로그가 차단 발생 시 관측됨 — 차단이 silent가 아님을 확인.

---

## 6. Files in Scope (Brownfield — [DELTA] MODIFY)

| 파일 | [DELTA] | 변경 의도 |
|------|---------|-----------|
| `scripts/analyze_news.sh` | MODIFY | CLI 경로 해소(REQ-053-A) |
| `scripts/daily_screen.sh` | MODIFY | CLI 경로 해소(REQ-053-A), 기계 폴백 보존 |
| `scripts/persona_watcher.sh` | MODIFY | flock 단일 인스턴스 가드(REQ-053-E) |
| `src/trading/news/intelligence/analyzer.py` | MODIFY | analyzer.py:235 PAID_CALL 계측(REQ-053-F). 차단은 공유 가드(base.py)의 raise가 담당하고, 기존 호출자 catch(631/637)가 비용 0·pending 보존으로 흡수(REQ-053-B2) — 신규 catch 코드 불필요, run서 확인 |
| `src/trading/reports/daily_report.py` | MODIFY | daily_report.py:436 `_llm_text` PAID_CALL 계측(REQ-053-F) + 공유 가드 strict 인지화로 raise 보존. **`_llm_text`는 production 호출자 0(휴면, `_narrative_text`가 live 경로) — raise는 실제 도달하지 않으나 가드+계측은 "재활성 시 누수 방지"용으로 유지(D2/D-new1)** |
| `src/trading/personas/base.py` | MODIFY | 공유 가드 `is_cli_only_mode`/`block_if_cli_only_mode` strict 인지화(B, ADR-004) + should_defer_paid_call strict fail-closed 분리(C, ADR-005) + _record_cli_failure strict 보존(D) + **`call_persona`(224) 진입점 should_defer 가드 + graceful skip(G, 주 방어)** + **`is_cli_mode_active`(949) strict 인지화(D4, 보조 방어)** + call_persona 280/353/408 PAID_CALL 계측(F/G3) |
| `src/trading/personas/{decision,micro,portfolio,macro,risk,retrospective}.py` | (검토만, 신규 코드 없음 예상) | 6개 디스패처는 `call_persona`에 로컬 try/except가 없어 G raise를 catch하지 않음 — raise가 상위 경계(scheduler `_wrap` runner.py:158 / orchestrator 1123-1125)서 흡수되어 cost-0 스킵(REQ-053-G2/D2). run에서 흡수가 사이클을 깨지 않는지 확인 후 미흡 시에만 수정 |
| `src/trading/scheduler/runner.py` · `src/trading/personas/orchestrator.py` | (경계 확인만) | G raise 흡수 경계(runner.py:158 `_wrap`, orchestrator.py:1123-1125). cost-0 사이클 스킵 귀결 확인. 신규 코드 없음 예상 |
| `grep "nvm/versions/node" scripts/`가 추가로 드러내는 `scripts/*.sh` | MODIFY | CLI 경로 해소(REQ-053-A) |

(현재 grep 결과: `analyze_news.sh`, `daily_screen.sh` 2개만 dead path 보유. 구현 시 재확인 필수.)

### 알려진 5개 유료 호출 지점 (REQ-053-F 계측 대상)

| # | 위치 | 함수 | 모델 | 차단 경로 |
|---|------|------|------|-----------|
| 1 | `analyzer.py:235` | `_call_haiku` | Haiku | 공유 가드(B1) strict 인지화 |
| 2 | `base.py:280` | `call_persona` (본호출) | persona 모델 | **REQ-053-G 진입점 가드(주)**. 디스패처 else가 직접 도달하므로 824 게이트로는 불충분 |
| 3 | `base.py:353` | `call_persona` (재시도) | persona 모델 | 동상(G) |
| 4 | `base.py:408` | `call_persona` (툴 재시도) | persona 모델 | 동상(G) |
| 5 | `daily_report.py:436` | `_llm_text` | Sonnet | 공유 가드(B1) strict 인지화. 현재 휴면(REQ-030-7) |
