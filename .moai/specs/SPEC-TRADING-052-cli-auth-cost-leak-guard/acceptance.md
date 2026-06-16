# SPEC-TRADING-052 인수 기준 (Acceptance)

[HARD] 모든 인수 기준은 **재현 우선(reproduction-first)**이다. 호스트 CLI 실패는 exit=0 빈출력/CLICallError를 주입하는 mock으로, 쿨다운은 주입 `now_provider`(`Callable[[], datetime]`, SPEC-031 `maybe_notify_halt`/SPEC-051 FakeClock 패턴)으로 재현하며 실제 호스트 claude·wall-clock·텔레그램에 의존하지 않는다.

### AC↔REQ 매핑 요약 (0.2.0 split 반영)

| AC | REQ |
|----|-----|
| AC-1 | REQ-052-A1 (연속 빈출력/CLICallError → degraded) |
| AC-1b | REQ-052-A1b (워처 stale → degraded) |
| AC-1c | REQ-052-A5 (latch ↔ 자동전환 카운터 독립, [HARD] D4) |
| AC-2 | REQ-052-A2 (성공/하트비트 복귀 → 해제·리셋) |
| AC-3 | REQ-052-A4 (DB 실패 graceful) |
| AC-4 | REQ-052-B1/B2 (조기경고 + 쿨다운 throttle) |
| AC-4b | REQ-052-B3 (L541 무쿨다운 대체 + L557/L558 자동전환 알림 보존) |
| AC-5 | REQ-052-C1 (strict ON → 폴백 차단·defer) |
| AC-5b | REQ-052-C2 (strict OFF → SPEC-016 폴백 불변; `test_fallback_consistency.py` GREEN) |
| AC-6 | REQ-052-A3/A3b + C (3경로 단일소스 strict 차단) |
| AC-7 | REQ-052-D1 (3경로 동일 구조화 로그 스키마) |

## 핵심 시나리오 (Given-When-Then)

### AC-1 (REQ-052-A1): 연속 빈출력 실패 → degraded 영속 마킹
- **Given**: 호스트 CLI가 연속 N회(임계, 기본 3) exit=0 빈출력/`CLICallError`로 실패하는 mock.
- **When**: 페르소나 호출을 N회 수행한다.
- **Then**: N회째에 system_state의 degraded 상태가 영속 기록되고(전이시각·연속실패횟수 포함), 이후 조회에서 degraded=True가 관측된다.

### AC-1b (REQ-052-A1b — 직접경로 사각지대): 워처 stale → degraded 마킹
- **Given**: 워처 하트비트가 stale(`HEARTBEAT_STALE_SECONDS` 초과)인 상태 + `decision.py` 직접 API 분기 진입.
- **When**: 결정 사이클이 직접 `call_persona` 분기로 빠진다.
- **Then**: 직접경로 진입이 degraded로 마킹되고 REQ-052-B 경고 대상이 된다. (현재 코드: 직접경로는 감지·알림 전무 → RED로 고정)

### AC-1c (REQ-052-A5 — [HARD] D4: latch ↔ 자동전환 카운터 독립): degraded flap 방지
- **Given**: 연속 실패가 자동전환 임계(`_CLI_AUTO_DISABLE_THRESHOLD=3`)에 도달하여 `_record_cli_failure`가 `cli_personas_enabled=False` 자동전환 + **base.py L564에서 in-process `_cli_failure_count`를 0으로 리셋**하는 상황.
- **When**: 자동전환 직후(in-process 카운터=0) 영속 degraded 상태를 조회한다.
- **Then**: 영속 `cli_degraded`는 **True를 유지**한다 — in-process 카운터가 0으로 리셋되어도 degraded가 healthy로 **flap(토글)하지 않음**을 단언. degraded latch는 오직 CLI 성공/하트비트 신선(REQ-052-A2)에서만 False가 됨을 추가 단언. (영속 degraded가 in-process 카운터에서 파생되면 이 테스트는 RED)

### AC-2 (REQ-052-A2): CLI 성공 복귀 → degraded 해제·카운터 리셋
- **Given**: degraded=True 상태에서 호스트 CLI가 다시 정상 응답(비어있지 않은 출력)을 반환.
- **When**: 페르소나 호출이 성공한다.
- **Then**: degraded=False로 해제되고 연속실패 카운터가 0으로 리셋되며, throttle 클럭(`cli_degraded_notified_at`)이 NULL로 리셋된다(다음 에피소드 첫 알림 즉시).

### AC-3 (REQ-052-A4): degraded 영속 DB 실패 시 graceful(fail-open 보존)
- **Given**: degraded 마킹용 `update_system_state`가 raise하는 상황.
- **When**: 페르소나 사이클이 진행된다.
- **Then**: 사이클이 wedge되지 않고(예외가 사이클을 막지 않음) graceful 로그가 남으며, 기존 fail-open 계약(`is_cli_only_mode` DB 실패 시 False)이 보존된다.

### AC-4 (REQ-052-B1/B2 — ADR-003): 조기경고 + 쿨다운 throttle
- **Given**: healthy→degraded 전이 + 이후 매 사이클 폴백/직접호출 발동(클럭 주입).
- **When**: 전이 직후 첫 발동, 그리고 쿨다운 내 여러 사이클이 연속 발동한다.
- **Then**: **첫 발동에 즉시** 텔레그램 경고("CLI 불건강 — 유료 비용 누수, 호스트 재인증 필요")가 1회 발사되고, 쿨다운 내 후속 발동은 **throttle(미발사)**. 쿨다운 경과 후 1회 재발사. degraded 해제 후 다음 에피소드 첫 발동은 다시 즉시 발사.

### AC-4b (REQ-052-B3 — D7): 무쿨다운 매실패 알림 대체 + 자동전환 알림 보존
- **Given**: 연속 실패로 폴백이 매 사이클 발동.
- **Then**: 기존 `_record_cli_failure`의 **base.py L541 `tg.system_briefing("CLI fallback", ...)`(per-failure)**가 **더 이상 매번 발사되지 않고**(throttle로 대체)되, 3연속 자동전환(`cli_personas_enabled=False`) 시 발사되는 **base.py L557/L558 `tg.system_briefing("CLI auto-disabled", ...)`(REQ-FALLBACK-06-4)는 그대로 발사**됨을 단언(L557/L558은 throttle 대상 아님).

### AC-5 (REQ-052-C1 — ADR-001): strict ON → 유료 폴백 차단·defer
- **Given**: `strict_cost_zero_mode=True` + cli_only_mode 활성 + 호스트 CLI 실패.
- **When**: 페르소나 폴백 분기가 유료 `call_persona`를 호출하려 한다.
- **Then**: 유료 호출이 **발생하지 않고**(call_persona 미호출 spy 단언) 해당 사이클이 defer(스킵)되며, 크레딧 소비 0. defer 사실이 알림+구조화 로그로 남는다(REQ-052-C3).

### AC-5b (REQ-052-C2 — [HARD] 기본 OFF 회귀): strict OFF → 기존 폴백 동작 불변
- **Given**: `strict_cost_zero_mode=False`(기본값) + 호스트 CLI 실패.
- **When**: 페르소나 폴백 분기가 진입한다.
- **Then**: 기존 SPEC-016 동작 그대로 — Haiku 폴백이 정상 호출됨. 본 SPEC 추가 코드가 기본 경로를 **바이트 단위로 변경하지 않음**을 단언. 구체적으로 기존 SPEC-016 REQ-016-1-3/1-4 폴백 테스트 모듈 **`tests/personas/test_fallback_consistency.py`**(`block_if_cli_only_mode` 가드 + `assert_fallback_model` 화이트리스트)가 전부 **GREEN**을 유지해야 한다(회귀 0).

### AC-6 (REQ-052-C 세 경로 통합): 직접경로·뉴스경로도 strict 차단
- **Given**: `strict_cost_zero_mode=True` + (a) `decision.py` 직접 API 분기, (b) 뉴스 `_call_haiku` 경로.
- **Then**: 두 경로 모두 유료 호출 대신 defer되고 단일 degraded 소스(REQ-052-A3)를 참조함을 단언(REQ-052-A3b 3경로 공유참조 통합 검증).

### AC-7 (REQ-052-D1): 세 경로 구조화 로그 동일 스키마
- **Given**: 폴백/직접/뉴스 각 경로에서 유료 호출 발동(strict OFF).
- **Then**: 세 경로가 동일 구조화 로그 스키마(`persona`/`path`/`model`/`reason`)를 남겨 grep/집계 가능함을 단언.

## 엣지 케이스

### EC-1 (REQ-052-A): exit=0 비정상(빈출력) vs exit≠0 동일 처리
- **Given**: (a) exit=0인데 0바이트 응답(사고의 실제 형태), (b) exit≠0 + 에러.
- **Then**: 둘 다 동일하게 실패로 카운트되어 degraded 전이에 기여한다. (사고 핵심: exit=0 빈출력이 성공으로 오인되지 않음)

### EC-2 (REQ-052-B 영속): throttle 클럭이 컨테이너 재시작 생존
- **Given**: degraded throttle 발사 후 프로세스 재시작.
- **Then**: `cli_degraded_notified_at`가 system_state에 영속되어 재시작 후에도 쿨다운이 이어짐(SPEC-031 REQ-031-1c 동형). in-process 전역이었다면 깨질 시나리오.

### EC-3 (REQ-052-C defer 멱등): strict defer 후 다음 슬롯 재처리
- **Given**: strict ON으로 뉴스/페르소나 사이클이 defer됨.
- **Then**: 데이터가 손실되지 않고 다음 호스트-CLI 슬롯에서 멱등 재처리됨(SPEC-043 graceful defer 정합).

## 품질 게이트 (Quality Gates)

- [ ] 신규/수정 테스트 전부 GREEN, 전체 회귀 0(pre-existing 6 제외).
- [ ] ruff check 통과, 타입 힌트 완비.
- [ ] degraded 상태가 system_state에 영속, 세 경로(폴백/직접/뉴스)가 단일소스 참조(AC-1/1b/6).
- [ ] **[HARD·D4] degraded latch가 in-process 자동전환 카운터(L564 리셋)와 독립 — flap 없음(AC-1c, REQ-052-A5).**
- [ ] degraded 영속 DB 실패 graceful(fail-open 보존, AC-3).
- [ ] 조기경고가 SPEC-031 동형 쿨다운 throttle로 제한, 첫 발동 즉시·재시작 생존(AC-4/EC-2).
- [ ] 무쿨다운 매실패 알림(L541) 대체 + 자동전환 알림(L557/L558) 보존(AC-4b, D7).
- [ ] `strict_cost_zero_mode` 기본 OFF에서 기존 폴백 동작 바이트 불변(AC-5b) — [HARD] SPEC-016 회귀 0, `tests/personas/test_fallback_consistency.py` GREEN.
- [ ] strict ON에서 세 경로 유료 호출 차단·defer + 알림(AC-5/6).
- [ ] 세 경로 구조화 로그 동일 스키마(AC-7).
- [ ] 마이그레이션 034 idempotent(information_schema 가드) — 최신 033 다음.
- [ ] `@MX:SPEC SPEC-TRADING-052` 주석이 변경 지점에 부착.

## Definition of Done

1. REQ-052-A1·A1b·A2·A3·A3b·A4·A5, B1~B3, C1~C3, D1 전부 구현·테스트 GREEN. D2는 Optional(ADR-004).
2. `strict_cost_zero_mode` 기본 OFF — SPEC-016/030/034 회귀 0(AC-5b 핵심 가드).
3. ADR-001 정책 선택(strict 운영 기본값)이 OQ-1 운영자 답변으로 확정.
4. 호스트 재인증 자동화는 범위 제외(Exclusion #1) — SPEC은 감지·경고·비용차단까지.
5. 운영 검증 게이트: 배포 후, 호스트 CLI를 의도적으로 죽인 상태(또는 다음 실제 인증 만료 시)에서 (a) degraded 마킹 + (b) 첫 사이클 즉시 텔레그램 경고 1회(이후 throttle) + (c) strict ON이면 유료 호출 0(크레딧 미소비) + (d) 구조화 로그에서 경로별 폴백 발동 집계 관측.
