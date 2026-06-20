# SPEC-TRADING-053 — 인수 기준 (acceptance.md)

Given-When-Then 시나리오. 모든 차단 동작은 `strict_cost_zero_mode` ON에 게이트되며, OFF 동작은 SPEC-016/052와 불변임을 회귀로 보증한다.

---

## AC-1 (REQ-053-B1/B2/B3) — strict ON + CLI 결손 시 뉴스 분석은 비용 0 스킵 (raise + 호출자 catch)

- **Given** `strict_cost_zero_mode`가 ON(또는 `cli_only_mode`/`cli_personas_enabled` 활성),
- **When** 뉴스 analyzer가 `_call_haiku`를 호출하면,
- **Then** 공유 술어 `is_cli_only_mode()`가 True를 반환해 데코레이터가 `RuntimeError`를 raise하고 `client.messages.create`가 단 한 번도 호출되지 않으며, **기존 호출자 catch(analyzer.py:631/637 `except Exception`)가 예외를 흡수**하여 pending 파일을 보존한다(비용 0). `CLI_DEGRADED_DEFER` 스타일 audit/구조화 로그가 emit된다. (graceful skip은 본문 반환이 아니라 호출자 catch로 달성 — ADR-002.)

## AC-1b (REQ-053-B1, daily_report 단위) — strict ON에서 _llm_text 직접 호출 시 raise·유료 0 (휴면)

- **Given** `strict_cost_zero_mode`가 ON,
- **When** `_llm_text`(daily_report.py:436)를 **단위 테스트에서 직접 호출**하면,
- **Then** 공유 술어가 True를 반환해 데코레이터가 `RuntimeError`를 raise하고 Sonnet `client.messages.create`가 호출되지 않는다(유료 0).
- **참고(D-new1):** `_llm_text`는 **production 호출자가 0**(휴면)이다 — `generate_and_send`는 `_narrative_text→call_persona_via_cli`(REQ-053-C로 커버)를 호출하지 `_llm_text`를 호출하지 않는다. 따라서 "generate_and_send→_llm_text→_fallback_text" 흐름은 존재하지 않으며, 이 AC는 재활성 대비 누수 방지를 단위 테스트로만 단언한다. daily-report의 **live 유료 차단 커버리지는 AC-2c(narrative→call_persona_via_cli→should_defer_paid_call)** 가 담당한다.

## AC-2a (REQ-053-C1 — 가드 분리, 독립 단언) — strict ON에서 should_defer_paid_call이 플래그 무관 True

- **Given** `strict_cost_zero_mode`가 ON이고 `cli_personas_enabled=False`, `cli_only_mode=False`(즉 모든 cli 플래그 false),
- **When** `should_defer_paid_call()`이 호출되면,
- **Then** 반환값이 True다(REQ-053-C1) — 가드가 플래그 값과 무관하게 차단. (이 단언은 D1 충족 여부와 독립적으로 성립해야 한다.)

## AC-2b (REQ-053-D1 — 플래그 churn 방지, 독립 단언) — strict ON에서 _record_cli_failure가 cli_personas_enabled를 끄지 않음

- **Given** `strict_cost_zero_mode`가 ON,
- **When** `_record_cli_failure`가 `_CLI_AUTO_DISABLE_THRESHOLD`(3) 연속 실패로 호출되면,
- **Then** `update_system_state(cli_personas_enabled=False)`가 호출되지 않고(또는 플래그가 True로 유지되고), "CLI_AUTO_DISABLED" audit·자동전환 텔레그램이 발송되지 않는다(REQ-053-D1). (AC-2a와 **AND** 조건 — 둘 다 독립 검증되어 방어심층 D3가 보장된다. D가 깨져도 C가 통과로 가려지지 않는다.)

## AC-2c (REQ-053-G — 진짜 누수 봉인, D-CRIT-1) — strict ON + 워처 stale에서 디스패처 직접 call_persona 차단

- **Given** `strict_cost_zero_mode`가 ON, `cli_personas_enabled=True`, **워처 heartbeat가 stale**(따라서 `is_cli_mode_active()`가 False를 반환),
- **When** 페르소나 디스패처(decision/micro/portfolio/macro/risk/retrospective)가 분기 평가 후 else 가지의 `call_persona`(base.py:224)를 호출하면,
- **Then** `call_persona` 진입점 가드(REQ-053-G1)가 `should_defer_paid_call()`==True를 확인해 `try:`(base.py:263) **이전**에 `RuntimeError`를 raise하고, `client.messages.create`(base.py:280/353/408)에 **도달하지 않는다**(유료 0). 검증 가능한 사실: raise가 `call_persona` **밖으로 전파**되고(내부 except 393에 삼켜지지 않음), `res`(PersonaResult)가 **생성되지 않으며**, 824 게이트를 거치지 않으므로 824만으로는 막히지 않는다 — 진입점 가드(G)가 봉인한다.
- **AND (G2 graceful skip — 상위 경계 흡수)** 6개 디스패처(decision.py:134, risk.py:114, macro.py:78, micro.py:93, portfolio.py:50, retrospective.py:60)는 `call_persona`에 대한 **로컬 try/except가 없으므로** raise를 catch하지 않는다. raise는 디스패처를 통과해 스케줄러 `_wrap`(runner.py:158 `except Exception`이 "failed/skipped" 로깅) 또는 orchestrator per-persona except(orchestrator.py:1123-1125)에서 흡수되어 **cost-0 사이클 스킵**으로 귀결된다(크래시 없음, 잘못된 결정 없음). 검증: `_wrap`이 해당 페르소나를 "failed"로 로깅하고 사이클이 계속 진행됨.
- **AND (G3 계측)** 차단 시 진입점 가드가 raise 직전 `CLI_DEGRADED_DEFER` audit를 emit한다(데코레이터는 audit 없음, D4).

## AC-2d (REQ-053-D4 — 보조 라우팅 안전망) — strict ON에서 디스패처가 else로 빠지지 않음

- **Given** `strict_cost_zero_mode`가 ON, 워처 heartbeat stale,
- **When** 디스패처가 `is_cli_mode_active()`를 평가하면,
- **Then** strict 인지화로 인해 디스패처가 else(직접 `call_persona`)로 빠지지 않고 `call_persona_via_cli` 경로를 택한다(거기서 should_defer로 defer). 단 이는 보조이며 주 방어는 AC-2c(G)다.

## AC-2e (daily-report live narrative 커버리지) — _narrative_text 경로

- **Given** `strict_cost_zero_mode`가 ON,
- **When** daily-report가 `_narrative_text→call_persona_via_cli`(base.py:731)로 narrative를 생성하면,
- **Then** `call_persona_via_cli`의 `should_defer_paid_call()`(824) 게이트가 True를 반환해 폴백 유료 호출이 발생하지 않는다(daily-report의 live 유료 차단 커버리지 — AC-1b 휴면 단위 테스트와 구분).

## AC-3 (REQ-053-C2/D2 회귀 가드) — strict OFF 기본 동작 불변

- **Given** `strict_cost_zero_mode`가 OFF(기본),
- **When** CLI가 실패하면,
- **Then** 기존 SPEC-016 Haiku 폴백 동작이 변경 없이 수행된다 — `should_defer_paid_call()`은 False, `_record_cli_failure`의 3연속 자동전환(`cli_personas_enabled=False` + audit "CLI_AUTO_DISABLED" + 텔레그램)이 기존대로 발동.

## AC-4 (REQ-053-A) — dead nvm 경로 교정

- **Given** 기존 dead nvm 경로(`...nvm/versions/node/v24.13.0/bin/claude`),
- **When** 교정 후 `analyze_news.sh`(및 `daily_screen.sh`)가 실행되면,
- **Then** `command -v claude` 또는 `/home/onigunsow/.local/bin/claude`로 해소하여 "Sending NNN lines to Claude CLI" 로그를 남기고 "그런 파일이나 디렉터리가 없습니다"(no such file) 오류가 0건이다.

## AC-5 (REQ-053-A2) — CLI 완전 부재 시 유료 경로 미발동

- **Given** `command -v claude`도 `.local/bin/claude`도 실행 불가,
- **When** `analyze_news.sh`가 실행되면,
- **Then** 명확한 ERROR 로그 후 non-zero 종료하며, 어떤 유료 API 경로도 발동하지 않는다(pending 보존).

## AC-6 (REQ-053-E) — flock 단일 인스턴스

- **Given** `persona-watcher.service` 재시작으로 고아 인스턴스가 남은 상황,
- **When** 두 번째 `persona_watcher.sh`가 시작되면,
- **Then** flock이 즉시 `exit 0`을 유발하고 단 하나의 인스턴스만 task를 처리한다(`persona_watcher.log`에 동일 task 중복 "Processing"/"Done" 0건).

## AC-7 (REQ-053-A4) — daily_screen 기계적 폴백 보존

- **Given** `daily_screen.sh`가 CLI 해소에는 성공했으나 claude CLI 호출이 실패(exit≠0 또는 빈 응답),
- **When** 폴백 분기(daily_screen.sh:62-74)가 실행되면,
- **Then** 기계적 top-20 후보가 `screened_tickers.json`에 기록되어 스크리닝이 계속된다(유료 API 미발동, 비용 0). REQ-053-A 변경이 이 로컬 폴백을 보존함을 확인.

## AC-8 (REQ-053-F1/F2) — 5개 유료 호출 지점 PAID_CALL 계측

- **Given** `strict_cost_zero_mode`가 OFF(즉 유료 호출이 실제로 허용·발동되는 환경),
- **When** 5개 지점(analyzer.py:235, base.py:280/353/408, daily_report.py:436) 중 하나가 실제 `client.messages.create`를 발동하면,
- **Then** 호출 직전에 `PAID_CALL`(persona/path/model/reason) 구조화 로그가 emit된다 — 따라서 "strict ON 동안 PAID_CALL 0건"이 관측으로 증명 가능해진다(거짓 PASS 불가).
- **AND (F2)** 차단된 경우(strict ON)에는 `CLI_DEGRADED_DEFER`가, 발동된 경우(strict OFF)에는 `PAID_CALL`이 구분되어 남는다.
- **AND (F3)** 계측 추가가 strict OFF의 호출 횟수·반환값·예외 거동을 변경하지 않는다(로깅 사이드이펙트만).

## AC-9 (REQ-053-C3 — strict fail-closed + 콜드스타트 fail-open) — DB 장애 시 분기

- **Given** 직전 알려진(last-known) strict 상태가 ON이고 `get_system_state()`가 DB 예외로 실패,
- **When** `should_defer_paid_call()`이 호출되면,
- **Then** True(차단=fail-closed)를 반환하고 `reason=db_unavailable_strict_failclosed` WARN 로그를 남긴다.
- **AND** last-known strict가 OFF로 알려졌으면 예외 시 False(SPEC-016 fail-open 보존) — strict OFF 불변.
- **AND (D-new4 콜드스타트)** 캐시가 비어 있고(부팅 첫 호출 전) DB 예외이면 **False(fail-open)** 를 반환한다 — 실제 strict-OFF 시스템의 정당한 SPEC-016 폴백을 막지 않기 위함(REQ-052-C2 HARD 우선). 다음 성공 호출에서 캐시가 채워지면 fail-closed로 전환.

---

## Edge Cases

- **E1 (DB 예외 — strict fail-closed/콜드스타트 fail-open, ADR-005):** `get_system_state()` 예외 시 → last-known ON이면 True(차단), OFF이면 False, **빈 캐시(콜드스타트)면 False(fail-open, D-new4)**. 모두 WARN 로그로 관측 가능(REQ-053-C3). `_record_cli_failure`의 strict 판정 실패 시는 기존 동작(fail-open, strict=False)으로 폴백.
- **E2 (strict ON + cli_only_mode=True 동시):** 공유 술어가 True를 반환해 데코레이터가 raise하고, `_call_haiku` 호출자 catch(631/637)가 흡수해 pending 보존(비용 0). 본문 graceful 반환이 아니라 **raise + 호출자 catch**로 처리(ADR-002). 데코레이터 raise 계약은 cli_only·strict 공통.
- **E3 (flock 락 파일 권한/tmpfs 정리):** 락 획득 실패가 정상 인스턴스를 막지 않도록 `exit 0` 폴백 — 서비스 다운 회피(R4).
- **E4 (호출자 catch 흐름):** `_call_haiku` raise 시 호출자(analyzer.py:631/637 `except Exception`)가 pending 유지. daily-report live는 `_narrative_text→call_persona_via_cli`(AC-2c)가 커버, `_llm_text`는 휴면이라 raise 미도달(AC-1b 단위 테스트만) — run 단계 직접 확인(R3).
- **E5 (in-process 카운터 리셋 정책, D-new3):** `_cli_failure_count = 0` 리셋은 `if count>=threshold:` 블록 안에 있고, **strict 게이트는 auto-disable 부수효과(`cli_personas_enabled=False`·audit·텔레그램)에만 적용된다.** 따라서 리셋은 **strict ON/OFF 무관하게 임계 도달 시 항상** 수행되어 무한 누적이 발생하지 않는다. 영속 degraded latch(`_persist_cli_degraded`)는 별도 유지되어 리셋이 degraded를 풀지 않는다(SPEC-052 ADR-005 보존).

---

## Quality Gate / Definition of Done

- [ ] AC-1 ~ AC-9 (AC-1b, AC-2a/b/c/d/e 포함) 전부 통과(테스트 + 로그 증거).
- [ ] **[D-CRIT-1 핵심] AC-2c(REQ-053-G) 통과: strict ON + 워처 stale + cli_personas_enabled=True에서 디스패처 else `call_persona`가 messages.create(280/353/408)에 도달하지 않음(유료 0) + 6 디스패처 graceful skip.**
- [ ] reproduction-first: 각 근본원인(5개, 디스패처 직접 누수 포함)에 실패 테스트 선작성 후 GREEN.
- [ ] 전체 pytest 회귀 0(~1702 passed 기준 직접 검증, 출력 첨부).
- [ ] strict OFF 경로(AC-3, G4) + cli_only=True raise 계약 회귀 명시 검증.
- [ ] AC-2a(C)와 AC-2b(D)가 **독립 AND 단언**으로 각각 통과(방어심층, D 깨짐이 C로 가려지지 않음). AC-2c(G 주 방어)와 AC-2d(D4 보조)도 구분 검증.
- [ ] 5개 유료 호출 지점 전부 PAID_CALL 계측(AC-8, G3 포함) — "PAID_CALL 0건"이 관측으로 증명 가능.
- [ ] ruff check 통과.
- [ ] run+redeploy 후 spec.md §5 검증 게이트 5항목 관측:
  - [ ] 다음 뉴스 슬롯에서 "Sending NNN lines to Claude CLI" 성공 + no-such-file 0건.
  - [ ] strict ON 동안 관측 가능한 `PAID_CALL` 0건(5지점 계측됨) + 신규 비용 기록 cost=0 audit.
  - [ ] watcher 재시작 후 중복 처리 0.
  - [ ] 뉴스 기사 DB/대시보드 복원.
  - [ ] 차단 발생 시 `CLI_DEGRADED_DEFER` 관측(silent 아님).
- [ ] DB 스키마/마이그레이션 변경 0(Exclusion #2 준수 확인) — strict fail-closed는 in-process 캐시로 구현, 신규 컬럼 없음.
- [ ] trading/sizing/decision 로직 미변경 확인.
