# SPEC-TRADING-053 — 구현 계획 (plan.md)

## 1. 기술적 접근 (Technical Approach)

### REQ-053-A — 셸 CLI 경로 해소

`analyze_news.sh`, `daily_screen.sh`의 `CLAUDE="...nvm/versions/node/v24.13.0/bin/claude"` 라인을 견고한 해소 블록으로 교체한다. 패턴(의도, 실코드는 run 단계):

```
CLAUDE="$(command -v claude || true)"
[ -x "$CLAUDE" ] || CLAUDE="/home/onigunsow/.local/bin/claude"
if [ ! -x "$CLAUDE" ]; then
    log "ERROR: claude CLI binary not found (command -v / .local/bin) — aborting, NO paid API"
    exit 1
fi
```

- `persona_watcher.sh`는 이미 `/home/onigunsow/.local/bin/claude`(b37ae52)이므로 동일 해소 블록을 적용하되 CLI 경로 의미는 불변. (선택: 일관성을 위해 동일 블록 적용 가능 — Exclusion #6 위배 아님, 경로 *값*은 유지.)
- `daily_screen.sh`의 기계 폴백(62-74행)은 유료 API가 아니라 순수 로컬 top-20이므로 손대지 않는다.
- 구현 직전 `grep -rln "nvm/versions/node" scripts/`를 다시 실행해 누락 스크립트가 없는지 확인한다.

### REQ-053-B / F — 공유 가드 strict 인지화 + graceful defer + 5지점 계측

핵심 통찰(ADR-004 재작성): `_call_haiku`(analyzer.py:235)와 `_llm_text`(daily_report.py:436)는 **같은** `@block_if_cli_only_mode` 데코레이터를 공유한다. 데코레이터는 `if is_cli_only_mode(): raise RuntimeError`(base.py:132)이고, `_call_haiku` 호출자(analyzer.py:631/637)는 이미 `except Exception`(632/638)으로 그 예외를 catch해 pending을 보존한다. 따라서 **단일 변경(공유 술어 strict 인지화)** 만으로 두 지점이 동시에 차단되며, graceful skip은 **함수 본문이 아니라 기존 호출자 catch가 달성**한다.

설계(의도, 실코드는 run):
1. **`is_cli_only_mode()`(base.py:82) strict 인지화 (단일 변경, SSOT):** 차단 판정을 `cli_only_mode OR cli_personas_enabled OR strict_cost_zero_mode`로 확장. 이로써 데코레이터(analyzer/daily_report)와 scheduler.py:204가 strict ON에서 모두 차단된다.
2. **raise + 호출자 catch = graceful skip (D-new2 결정):** 데코레이터의 raise를 제거하지 않는다. strict ON에서도 raise하며(cli_only와 동일 계약, SPEC-016 raise 테스트 보존), 호출자가 흡수한다:
   - `_call_haiku`: 호출자 analyzer.py:631/637의 **기존** `except Exception`이 raise를 catch → pending 보존(신규 catch 코드 불필요). run서 두 catch 분기가 pending을 유지하는지 직접 확인.
   - `_llm_text`: **production 호출자 0(휴면)** — `generate_and_send`는 `_narrative_text→call_persona_via_cli`를 호출하지 `_llm_text`를 호출하지 않는다. 따라서 raise는 실제로 도달하지 않는다. 가드+계측은 "재활성 시 누수 방지"용으로만 유지하며, 검증은 단위 테스트(직접 호출 시 strict ON raise·유료 0)로 한다. 허위 `generate_and_send→_fallback_text` 흐름 주장 제거(D-new1).
3. **REQ-053-F 계측:** 5개 지점(analyzer.py:235, base.py:280/353/408, daily_report.py:436) **각각 messages.create 직전**에 `_log_paid_call(persona/path/model/reason)`을 삽입. 차단(raise)된 경우는 `CLI_DEGRADED_DEFER`, 실제 발동은 `PAID_CALL`로 구분. strict OFF 동작(호출 횟수·반환·예외)은 불변(F3) — 로깅 사이드이펙트만 추가.

run 단계 확인: `_call_haiku` 호출자 catch(631/637)가 pending을 보존, daily-report live 경로(`_narrative_text→call_persona_via_cli→should_defer_paid_call`)가 REQ-053-C로 커버됨, `_llm_text` 우회 production 호출자 부재(R3).

### REQ-053-G / D4 — call_persona 진입점 가드(주) + is_cli_mode_active strict 인지화(보조) [D-CRIT-1]

진짜 페르소나 누수: 6개 디스패처(decision.py:104/134, micro.py:78/93, portfolio.py:37/50, macro.py:65/78, risk.py:96/114, retrospective.py:60 무조건)가 `if is_cli_mode_active(): call_persona_via_cli else: call_persona`로 분기. `is_cli_mode_active()`(base.py:949-981)는 `cli_personas_enabled=False` **또는 워처 stale**이면 False·**strict 미참조** → strict ON + 워처 死에서 else로 빠져 `call_persona`(224)가 무가드로 messages.create(280/353/408) 직접 호출. `should_defer_paid_call`은 `call_persona_via_cli`(824) 안에만 있어 안 막힘.

설계(의도, 실코드는 run):
1. **주 방어 — `call_persona` 진입점 가드(ADR-006):** `call_persona`(base.py:224) 함수 진입부, **`try:` 블록(base.py:263) 이전**·`Anthropic(...)`(250)·`messages.create`(280) 이전에 `if should_defer_paid_call():` 검사를 추가. True면 `call_persona_via_cli`(824/844)의 defer와 동일한 `RuntimeError` 계열 신호를 raise → 유료 호출 0. 디스패처 라우팅(if/else)과 **무관하게** 3개 유료 지점이 원천 봉인된다. 이 진입점에 REQ-053-F의 PAID_CALL 계측 + 차단 시 `CLI_DEGRADED_DEFER` audit를 raise **직전** emit(데코레이터엔 audit 없음, D4).
   - **[HARD] 배치 제약(D3):** 가드는 반드시 `try:`(263) **밖**에서 raise해야 한다. `try:` 안에서 raise하면 내부 `except Exception`(base.py:393)이 RuntimeError를 삼켜 `PersonaResult(error=...)`로 변환→디스패처가 빈 결과를 유효 결정으로 오인(G2가 막으려는 바로 그 "잘못된 결정"). 함수 본문 최상단(인자 검증 후, try 진입 전)에 배치.
2. **graceful skip 계약(정정 D2):** 6개 디스패처(decision.py:134, risk.py:114, macro.py:78, micro.py:93, portfolio.py:50, retrospective.py:60)는 `call_persona` 호출에 **로컬 try/except가 없다.** 따라서 가드 raise는 디스패처를 통과해 상위 경계 — 스케줄러 `_wrap`(runner.py:158 `except Exception`→"failed") 또는 orchestrator per-persona except(orchestrator.py:1123-1125, 재-raise→상위 `_wrap`) — 에서 흡수되어 cost-0 사이클 스킵으로 귀결. 디스패처에 신규 catch를 **추가하지 않는다**(기존 상위 경계가 이미 흡수). `res` 미생성으로 잘못된 결정 없음. run에서 `_wrap`/orchestrator 흡수가 사이클을 깨지 않고 진행시키는지 확인.
3. **보조 방어 — `is_cli_mode_active()`(949) strict 인지화(D4):** strict ON이면 워처 stale여도 False로 떨어지지 않고 `call_persona_via_cli` 경로를 택하게 한다(거기서 defer). 라우팅 안전망일 뿐 주 방어는 (1). strict OFF 동작 불변.

run 확인: 진입점 가드가 `try:`(263) 밖에서 raise하는지(D3), raise가 상위 `_wrap`/orchestrator에서 흡수되어 사이클 graceful 진행하는지(D2), retrospective.py:60이 G로 봉인되는지, D4 strict 인지화가 6개 라우팅 모두에 전파되는지.

### REQ-053-C — should_defer_paid_call strict fail-closed 분리

`base.py:577` 본문 마지막 줄
`return bool(state.get("cli_only_mode") or state.get("cli_personas_enabled"))`
을
`return True` (strict ON 도달 시점이면 무조건 차단)
로 바꾼다. 단 `if not state.get("strict_cost_zero_mode", False): return False`(591행) 가드는 유지 → strict OFF면 여전히 False(REQ-052-C2 불변).

**DB 예외 경로 변경(ADR-005, strict fail-closed):** 589행의 무조건 `return False`(fail-open)를 last-known strict 인지 fail-closed로 교체:
```
try:
    state = get_system_state()
    _LAST_KNOWN_STRICT = bool(state.get("strict_cost_zero_mode", False))  # in-process 캐시 갱신
except Exception:
    # DB 장애. [D-new4] 콜드스타트(빈 캐시)는 fail-OPEN(False) — strict-OFF 불변 HARD 우선.
    #   fail-closed는 직전 strict ON을 실제 관측했을 때만.
    if _LAST_KNOWN_STRICT is None:   # 빈 캐시 = 콜드스타트
        return False                  # fail-open (SPEC-016 불변 보호)
    return _LAST_KNOWN_STRICT          # ON 관측됨 → True(차단), OFF 관측됨 → False
if not state.get("strict_cost_zero_mode", False):
    return False  # strict OFF 명시 → SPEC-016 불변
return True
```
- `_LAST_KNOWN_STRICT`는 모듈 전역 in-process 캐시(초기값 `None`=미확정). 마이그레이션·DB 컬럼 불필요(Exclusion #2 준수).
- 콜드스타트(빈 캐시 + DB 예외)는 **fail-open(False)** — 실제 strict-OFF 시스템의 정당한 SPEC-016 폴백을 막지 않기 위함(REQ-052-C2 HARD 우선). 다음 성공 호출에서 캐시가 채워지면 fail-closed로 전환.
- strict ON이 직전에 관측된 캐시가 있을 때만 fail-closed(차단). OFF 관측 시 False → strict OFF 동작 불변.

### REQ-053-D — _record_cli_failure strict 보존

**[D-new3 교정]** base.py:710-728에서 `_cli_failure_count = 0`(728)은 `if count>=threshold:` 블록 **안**에 있다. 따라서 블록 전체를 `(not strict)`로 게이트하면 strict ON에서 리셋까지 건너뛰어 무한 증가한다(E5 모순). 해법: **auto-disable 부수효과만 strict로 게이트하고, 리셋은 strict와 무관하게 항상** 수행:

```
strict = False
try:
    strict = bool(get_system_state().get("strict_cost_zero_mode", False))
except Exception:
    strict = False  # fail-open: strict 판정 실패 시 기존 동작

if _cli_failure_count >= _CLI_AUTO_DISABLE_THRESHOLD:
    if not strict:
        # 기존 자동전환 부수효과 (strict ON에서만 생략)
        update_system_state(cli_personas_enabled=False, updated_by="auto_disable")
        audit("CLI_AUTO_DISABLED", ...)
        tg.system_briefing("CLI auto-disabled", ...)
    # [HARD] 리셋은 strict와 무관하게 항상 — 무한 누적 방지 (E5 일치)
    _cli_failure_count = 0
```

strict ON이면 `cli_personas_enabled=False` 자동전환·audit·텔레그램만 생략하고, 카운터는 임계 도달 시 0으로 리셋한다. strict OFF면 기존 동작 그대로(REQ-052-D2).

**in-process 카운터 리셋 정책(D7/E5):** 위 교정에 따라 `_cli_failure_count=0` 리셋은 **strict 여부와 무관하게 임계 도달 시 항상** 수행(무한 누적 방지). 영속 degraded latch는 `_persist_cli_degraded`로 별도 유지되므로 in-process 리셋이 degraded 상태를 풀지 않는다(SPEC-052 ADR-005 보존). strict ON→OFF 전이 시 다음 `_record_cli_failure`부터 기존 자동전환이 정상 작동(상태 누수 없음). 이 정책은 acceptance E5에 단언.

### REQ-053-E — persona_watcher.sh flock

`set -uo pipefail` 직후, 변수 정의 전에 삽입:

```
exec 200>/tmp/persona_watcher.lock
flock -n 200 || { echo "persona_watcher already running — exiting" >&2; exit 0; }
```

- `exit 0`으로 종료해 systemd가 실패로 보지 않게 한다(Restart 루프 방지).
- 락 FD 200은 프로세스 종료 시 자동 해제 → 정상 재시작 시 새 인스턴스가 즉시 획득.

---

## 2. ADR (Architecture Decision Records)

### ADR-001: SPEC-016 "허용된 폴백 예외" vs "비용 0 강제" 충돌

- **결정:** 모든 신규 차단은 `strict_cost_zero_mode` ON에 게이트한다. strict OFF면 SPEC-016 폴백 경로(base.py:110-111 의도)를 바이트 단위로 보존.
- **근거:** SPEC-052 ADR-001과 동형. 운영자는 현재 strict ON을 적용했으므로 실효성 있음. 트레이드오프(strict ON에서 CLI가 죽으면 뉴스/페르소나 분석이 비용 0이지만 결과도 0 = 사이클 스킵)는 운영자가 이미 수용한 선택.
- **대안 기각:** API 경로 완전 제거 → REQ-052-C2 위배, 비가역. 기각.

### ADR-002: raise + 호출자 catch = graceful skip (D-new2 재작성, 확정)

- **문제(iteration 2 모순):** 데코레이터는 `if is_cli_only_mode(): raise`다. strict를 `is_cli_only_mode()` 술어에 합치면 strict ON에서도 **raise**되어, 본문의 graceful `(None,0,0)` 반환에 도달하지 못한다. "본문 graceful 반환"과 "데코레이터 raise"가 충돌.
- **결정:** "본문이 `(None,0,0)`을 반환한다"는 요구를 **폐기**한다. 대신 이미 작동하는 패턴 — **데코레이터가 raise하고, 호출자가 그 예외를 catch해 비용 0·pending 보존으로 처리** — 을 정식 채택한다. 소스 확인: `_call_haiku` 호출자(analyzer.py:631/637)가 이미 `except Exception`(632/638)으로 raise를 흡수하고 pending을 보존한다 → AC-1 관측 결과(유료 0·pending 보존)는 그대로 성립.
- **근거:** raise는 cli_only와 동일한 차단 계약이므로 strict ON에서 raise해도 모순이 없다. SPEC-016의 `cli_only_mode=True` raise 테스트도 그대로 보존된다. 신규 본문 분기·신규 catch 코드가 필요 없어 회귀 표면이 최소.
- **대안:** (a) 데코레이터에서 raise 제거 후 본문 graceful 반환 → SPEC-016 raise 테스트 깨짐 + 함수별 반환형 차이로 데코레이터가 처리 불가. 기각. (b) 데코레이터 전면 제거 → cli_only 계약 약화. 기각.

### ADR-003: REQ-053-C(분리) + REQ-053-D(churn 방지) 동시 구현

- **결정:** 둘 다 구현(방어심층). C가 주(가드를 플래그에서 독립), D가 보조(플래그 안정화). C만으로도 누수는 막히지만, D가 없으면 `cli_personas_enabled`가 계속 false로 뒤집혀 다른 코드(대시보드/관측)가 CLI 비활성으로 오인할 수 있음.

### ADR-004: 공유 술어 strict 인지화로 직접-API 지점 동시 차단 (D2) + blast radius (D-new5)

- **결정:** 공유 술어 `is_cli_only_mode()`(base.py:82)를 strict 인지로 확장(`cli_only_mode OR cli_personas_enabled OR strict_cost_zero_mode`)한다. **한 번의 SSOT 수정**이 이 술어의 모든 호출자에 전파된다.
- **blast radius enumeration (`is_cli_only_mode()` 호출자 4곳):**
  1. `block_if_cli_only_mode` 데코레이터(base.py:132) → `_call_haiku`(analyzer.py:235): strict ON에서 raise → 호출자 catch(631/637)가 pending 보존. **유익.**
  2. 동 데코레이터 → `_llm_text`(daily_report.py:436): 휴면(production caller 0)이라 raise 미도달. 가드는 재활성 대비. **중립(안전).**
  3. `scheduler.py:204`(`if is_cli_only_mode(): defer to next slot`): strict ON에서 Haiku 폴백 대신 다음 슬롯 defer. **유익(누수 방지 강화, D-new5).**
  4. 기타 직접 `is_cli_only_mode()` 호출자: run 단계 grep로 전수 확인 후 영향 분석(회귀 표면 점검).
- **근거:** 지점별 본문 가드 중복 삽입보다 단일 진실 지점 수정이 회귀 표면이 작고 누락 위험이 없다. 4개 호출자 모두 strict 차단이 의미상 정합(유익 또는 안전).
- **graceful skip 메커니즘:** ADR-002 참조 — raise + 호출자 catch(본문 graceful 반환 아님).
- **`daily_report._llm_text` 휴면 처리:** production caller 0이지만 "단 한 건도" 불변식을 깨진 가드에 위임할 수 없으므로 공유 술어로 덮고 PAID_CALL 계측을 추가한다(재활성 시 누수 방지). 삭제하지 않음(Exclusion #1).

### ADR-005: strict 모드 fail-closed (DB 장애 시 차단) (D5)

- **결정:** `should_defer_paid_call()`의 DB 예외 경로를 last-known-strict 인지 fail-closed로 바꾼다. **직전 strict가 ON으로 실제 관측된 캐시가 있을 때만** DB 장애 중 차단(비용 0). **콜드스타트(빈 캐시) + DB 예외는 fail-OPEN(False)** [D-new4] — 실제 strict-OFF 시스템의 정당한 SPEC-016 폴백을 막지 않기 위함(REQ-052-C2 HARD 우선). strict OFF 관측 시 False → SPEC-016 불변.
- **근거:** §1 zero-leak 불변식을 "단 한 건도"로 선언하면서 fail-open으로 조용히 깨는 모순을 제거하되, 콜드스타트에서 strict-OFF 불변을 위반하지 않도록 fail-open 기본을 둔다. strict ON 시스템에서 "부팅 첫 호출 + DB 다운"이 겹치는 창은 극히 드물고, 다음 성공 호출에서 캐시가 채워지면 즉시 fail-closed.
- **트레이드오프(R1):** last-known ON + DB 장기 장애면 전 사이클이 비용 0으로 스킵됨(분석 결과 없음). 운영자가 strict ON을 택한 의미와 일치하므로 수용. strict OFF·콜드스타트는 영향 없음.

### ADR-006: call_persona 진입점 가드를 주 방어로 (D-CRIT-1)

- **결정:** 진짜 페르소나 누수(디스패처 else → 무가드 `call_persona`)를 **`call_persona`(base.py:224) 진입점 가드**(option b, 더 견고)로 원천 봉인한다. `is_cli_mode_active()` strict 인지화(option a, REQ-053-D4)는 **보조(라우팅 안전망)**로만 둔다.
- **근거:** 라우팅 분기는 6개(+미래 신규 페르소나)마다 누락 위험이 있다. 단일 진입점(call_persona)에 가드를 두면 디스패처 라우팅과 무관하게 3개 유료 지점이 한 곳에서 봉인된다. `call_persona_via_cli`(824)가 이미 쓰는 `should_defer_paid_call()`을 재사용하므로 차단 술어가 SSOT로 일관.
- **graceful skip(정정 D2):** `call_persona_via_cli`(844)의 defer와 동일한 RuntimeError 계열 신호. 6 디스패처는 로컬 try/except가 없어 catch하지 않으며, raise가 상위 경계(scheduler `_wrap` runner.py:158 / orchestrator 1123-1125)에서 흡수되어 cost-0 사이클 스킵. "유료 0 + 사이클 graceful 진행"이 SPEC 불변(REQ-053-G2). 가드는 `try:`(263) 밖에 배치(D3).
- **대안 기각:** D4(라우팅 강제)만 단독 채택 → 디스패처 누락·미래 신규 페르소나에서 재발. 보조로만 유지.

---

## 3. Milestones (우선순위 기반, 시간 추정 없음)

- **M1 (P1, 누수 본체):** REQ-053-A(셸 경로) + REQ-053-B/ADR-004(공유 술어 strict 인지화 → raise + 호출자 catch). 주요 뉴스 누수 즉시 차단.
- **M2 (P1, 진짜 페르소나 누수 + 자기모순 가드 + 관측):** **REQ-053-G/ADR-006(call_persona 진입점 가드, 주 방어) + REQ-053-D4(is_cli_mode_active strict, 보조)** + REQ-053-C(strict fail-closed 분리, 콜드스타트 fail-open) + REQ-053-D(churn 방지, 카운터 항상 리셋) + REQ-053-F(5지점 PAID_CALL 계측) + REQ-053-B6(scheduler 확인). strict ON에서 디스패처 직접 경로 봉인 + "0건" 진짜 증명.
- **M3 (P2, 중복 처리):** REQ-053-E(flock). 비용 누수와 무관하나 중복 LLM 발사·자원 낭비 방지.

순서(base.py가 공유 진실 지점): base.py(공유 술어 strict 인지화 B/ADR-004 → **C strict fail-closed → G call_persona 진입점 가드+계측 → D4 is_cli_mode_active strict → D 카운터 리셋 항상 → F 계측**) → analyzer.py(F 계측, 차단은 기존 catch 흡수) → daily_report.py(F 계측 + 휴면 가드) → 6 디스패처(신규 코드 없음, 상위 경계 흡수만 run서 확인) → 셸(A) → watcher(E). B6(scheduler) 확인은 base.py 작업 중.

---

## 4. Risks

- **R1 (strict fail-closed 트레이드오프 — ADR-005):** last-known strict ON + DB 장기 장애면 `should_defer_paid_call()`이 차단(True)을 유지해 전 사이클이 비용 0으로 스킵된다(분석 결과 없음). 의도된 보수적 동작이나 매매 분석 전면 중단으로 번질 수 있음. **완화:** fail-closed 차단 시 구조화 WARN 로그(`reason=db_unavailable_strict_failclosed`). 콜드스타트(빈 캐시)는 fail-open이라 strict-OFF 시스템 영향 없음. SPEC-047 대시보드가 DB 장애 조기 노출.
- **R2 (회귀):** base.py는 fan_in 높은 핵심(`should_defer_paid_call` fan_in≥3, base.py:531 ANCHOR). 공유 술어 `is_cli_only_mode()` strict 인지화는 **4개 호출자(데코레이터 2 + scheduler:204 + 직접)에 전파**되므로 blast radius가 넓다. **완화:** ADR-004 enumeration대로 4개 호출자 회귀 점검 + strict OFF 분기 + cli_only=True raise 계약을 명시 테스트(REQ-053-B4/C2/D2/F3). scheduler:204 defer 동작도 회귀 확인.
- **R3 (호출자 catch 흐름 보존):** `_call_haiku` 호출자(analyzer.py:631/637)의 `except Exception`이 strict raise를 흡수하고 pending을 보존하는지, daily-report live 경로(`_narrative_text→call_persona_via_cli`)가 should_defer로 커버되는지, `_llm_text` 우회 production 호출자가 없는지 미확인. **완화:** run 단계에서 직접 확인. (iteration 2의 허위 `generate_and_send→_llm_text→_fallback_text` 흐름은 D-new1로 제거됨.)
- **R5 (D-CRIT-1 graceful skip 계약 — 정정 D2):** `call_persona` 진입점 가드가 raise할 때, 디스패처는 로컬 try/except가 없으므로 raise가 상위 경계(scheduler `_wrap` runner.py:158 / orchestrator per-persona except 1123-1125)에서 흡수되어 cost-0 사이클 스킵으로 귀결된다(소스 확인됨). 잔여 검증: 이 흡수가 **사이클 전체를 깨지 않고** 해당 페르소나만 "failed/skipped" 처리하며 나머지 사이클이 진행되는지(특히 orchestrator 1123-1125의 재-raise가 전체 사이클 중단으로 번지지 않는지) run에서 확인. 가드 raise는 `try:`(263) 밖에 배치(D3) — 내부 except(393)에 삼켜지면 안 됨.
- **R4 (flock + systemd):** `/tmp/persona_watcher.lock`가 tmpfs 정리·권한 문제로 락 실패하면 정상 인스턴스도 못 뜸. **완화:** 락 경로를 systemd가 접근 가능한 위치로, `exit 0` 폴백으로 서비스 다운 회피.

---

## 5. Testing Strategy (reproduction-first)

- **repro-first:** 각 근본원인에 대해 실패하는 테스트를 먼저 작성.
  - B(analyzer, raise+catch): strict ON에서 `_call_haiku` 데코레이터가 raise하고 `messages.create` 미호출(0회), 호출자 catch(631/637)가 pending 보존. `CLI_DEGRADED_DEFER` 로그.
  - B(daily_report, 단위): strict ON에서 `_llm_text` 직접 호출 시 데코레이터 raise + `messages.create` 0회(D-new1: production 호출자 0이므로 단위 테스트만, generate_and_send 흐름 단언 금지).
  - B6(scheduler): strict ON에서 `scheduler.py:204`가 Haiku 폴백 대신 다음 슬롯 defer.
  - C: strict ON + `cli_personas_enabled=False`에서 `should_defer_paid_call()` == True.
  - C3(fail-closed + 콜드스타트): last-known ON + `get_system_state` 예외 → True. last-known OFF + 예외 → False. **빈 캐시(콜드스타트) + 예외 → False(fail-open, D-new4)**.
  - D(독립 단언, D4): strict ON + 3연속 실패 후 `update_system_state(cli_personas_enabled=False)` 미호출. **그리고 `_cli_failure_count`가 임계 도달 시 0으로 리셋됨(D-new3: 무한 증가 안 함)** — strict ON에서도 리셋 확인.
  - F: 5지점 각각 messages.create 직전 PAID_CALL 로그(strict OFF 실제 발동 시) emit. F3: 계측이 strict OFF 호출 횟수·반환 불변.
  - **G(D-CRIT-1 핵심): strict ON + 워처 stale(`is_cli_mode_active()` False) + `cli_personas_enabled=True` → 디스패처가 else로 `call_persona` 호출 시, 진입점 가드가 raise하고 `messages.create`(280/353/408)에 도달하지 않음(유료 0).** 6개 디스패처(retrospective 포함) 각각 graceful skip(잘못된 결정 없음) 단언.
  - D4(보조): strict ON이면 `is_cli_mode_active()`가 워처 stale에도 디스패처를 `call_persona_via_cli`로 보냄(else 미진입).
  - G4 회귀: strict OFF → `call_persona` 진입점 가드 통과(should_defer False), 기존 직접-API 경로 불변.
  - C2/D2 회귀: strict OFF → 기존 동작 불변(should_defer False, 자동전환 발동, cli_only raise 계약).
- **셸(A/E):** bats 또는 통합 테스트가 없으면 최소한 dead path가 사라졌는지 grep 게이트 + 수동 실행 로그 확인. A4: daily_screen 기계 폴백 보존(strict 무관 로컬 top-20). flock은 두 번째 인스턴스 즉시 exit 0 관측.
- **회귀 0:** 전체 pytest 1702 기준 유지 직접 검증.

---

## 6. Dependencies / Cross-SPEC

- SPEC-052: `strict_cost_zero_mode`, degraded latch, `_log_paid_call`, `should_defer_paid_call` 토대 제공. 본 SPEC이 그 가드의 결함(self-defeating)을 교정.
- SPEC-016: `block_if_cli_only_mode`, `is_cli_only_mode`, CLI 폴백 계약. strict OFF 동작 불변 유지.
- SPEC-043: `is_cli_only_mode` 단일 모드 소스(REQ-043-A5). 재사용.
- 마이그레이션: 불필요(Exclusion #2).
- **브랜치:** 현재 main(b37ae52). 전용 브랜치 권장(manager-git에 위임).
