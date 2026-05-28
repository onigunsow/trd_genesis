# SPEC-TRADING-032 — Implementation Plan

장 시작 전 자동 매매 재개. 작고 잘 정의된 additive 기능 — halt 원인을 audit_log 로 분류해
양성 자동 한도 정지에만 재개를 호출하며, 회로차단/리스크 내부 로직은 무회귀.

## Technical Approach

신규 모듈 `src/trading/risk/auto_resume.py` 가 (1) `system_state` 의 halt 여부를 읽고,
(2) audit_log 에서 활성 트립을 식별하고, (3) 분류기로 재개 여부를 판정하여,
(4) 양성이면 `circuit_breaker.reset(actor="auto_resume_premarket")` + "자동 재개" 알림,
그 외에는 "수동 검토 필요" 알림을 발송하고, 모든 결정을 `audit_log`(`AUTO_RESUME_PREMARKET`)에
기록한다. runner.py 에 07:25 KST 평일 cron 잡을 pre_market(07:30) 직전에 등록한다.
기존 DB 레이어(`session.py`)와 `circuit_breaker.reset()` 을 재사용하며 그 정의는 수정하지 않는다.

### 핵심 결정 (사용자 승인)
- 타이밍 = **07:25 KST mon-fri**, runner.py CronTrigger 코드 상수로 하드코딩(scheduler.yaml 미로드).
- 자동 재개 범위 = **"모든 자동 한도 위반 EXCEPT daily_loss"** (`reason=="pre-order limit breach"`
  AND breaches 에 `daily_loss` prefix 없음).
- **수동 `/halt` 절대 자동 재개 금지** (자본 보전 hard safety rule).
- **daily_loss 자동 재개 금지** (실손실), 향후 설정화는 Q-1.
- 원인 불명 ⇒ **재개 안 함**(defensive default).
- `halt_state=false` ⇒ **no-op**(로그만, 텔레그램 없음).
- 모든 결정(재개/보류)은 텔레그램 + audit_log 기록(미정지 no-op 제외 텔레그램).

## Milestones (우선순위 기반, 시간 추정 없음)

### Primary Goal (P-High) — 분류기 + 엔트리 함수 (auto_resume.py)
- `classify_halt(state, active_trip) -> (should_resume, cause, detail)` 작성. 분기:
  not_halted / undeterminable / manual / unknown_reason / daily_loss / (양성) resume.
  - daily_loss 판정: `any(b.startswith("daily_loss") for b in breaches)` (limits.py 포맷).
  - manual 판정: `reason.startswith("manual")`.
  - 양성 판정: `reason == "pre-order limit breach"` AND breaches 비어있지 않은 리스트 AND
    daily_loss 항목 없음.
- `run_premarket_auto_resume()` 엔트리: get_system_state → not halt 시 no-op return →
  active trip 조회 → classify → resume/hold 분기로 reset+알림+audit.
- 텔레그램 실패 swallow(circuit_breaker try/except 선례).
- 대상 REQ: REQ-032-2, REQ-032-3, REQ-032-4, REQ-032-5 / 검증: AC-1~AC-7

### Secondary Goal (P-High) — active trip 식별 (audit_log 조회)
- `session.py` 의 `connection()` 컨텍스트 매니저 재사용(news/intelligence/scheduler.py:280~287
  선례). raw SQL, 정렬 컬럼 = `ts`(audit_log_ts_idx 존재).
- 권장(방어) 방식: 최근 TRIP 1건 + 최근 RESET 1건 비교 — TRIP 없음 또는 `RESET.ts >= TRIP.ts`
  면 미확정(원인 불명). 단순 대안(최근 TRIP only)도 `halt_state=true` 불변식상 허용(Q-4).
- 대상 REQ: REQ-032-3c / 검증: AC-7

### Tertiary Goal (P-High) — 스케줄러 잡 등록 (runner.py)
- pre_market 잡(라인 252) **직전**에 `sched.add_job(lambda: _wrap("premarket_auto_resume",
  run_premarket_auto_resume), CronTrigger(day_of_week="mon-fri", hour=7, minute=25, timezone=KST),
  id="premarket_auto_resume", name="premarket_auto_resume 07:25")` 추가.
- `from trading.risk.auto_resume import run_premarket_auto_resume` import 추가.
- `_wrap` 사용으로 KRX 휴장일 자동 스킵(Q-3).
- 대상 REQ: REQ-032-1 / 검증: AC-8

### Final Goal (P-Medium) — 무회귀 검증 + 테스트
- 단위 테스트 `tests/risk/test_auto_resume.py`(권장): 7개 classify 분기 + active-trip 조회
  3케이스(TRIP-only / RESET-after-TRIP / no-TRIP) + reset 호출 인자(actor) + 텔레그램 카테고리/횟수.
- `circuit_breaker.reset` 와 `system_briefing` 은 mock. audit 기록 검증.
- trip()/reset()/limits 무회귀: 신규 모듈이 이들을 호출/조회만 함을 정적·동적으로 확인.
- 대상 REQ: REQ-032-6 / 검증: AC-9, 전체 AC 회귀

### Optional Goal (P-Low) — per-day guard / 설정화 (deferred)
- per-day guard(Q-2)나 daily_loss 설정화(Q-1)는 현 범위 외. per-day guard 채택 시 마이그레이션
  번호 024 가 다음 번호(현재 최고 023). 현 단계 미구현으로 충분.

## Architecture / Design Direction

- halt 원인은 **audit_log 가 single source of truth** — 신규 컬럼/테이블 불필요(결정도 audit_log 에
  기록). 따라서 본 SPEC 은 **마이그레이션 없음**(per-day guard 미채택 시).
- 분류 로직은 신규 모듈에 격리하여 순수 함수(`classify_halt`)로 테스트 용이성 확보. I/O(DB·텔레그램)
  는 엔트리 함수(`run_premarket_auto_resume`)에 모음.
- `circuit_breaker.reset()` 을 그대로 재사용 — halt 해제 + "회로차단 해제" 알림 + SPEC-031
  `halt_notified_at=NULL` 초기화가 자동 일관 처리됨(중복 구현 방지, 무회귀).
- "자동(양성) 한도 정지" vs "실손실/수동/불명" 의 경계를 명시적 prefix 매칭으로 — limits.py 의
  breach prefix 와 trip reason 리터럴에 결합(A-3~A-5 가정 검증 완료).

## Risks and Mitigations

- R-1 (트립 사이트 라인 이동): 라인 ~1051/1484 는 작성 시점 기준. 본 SPEC 은 트립 사이트를
  수정하지 않으므로 영향 작음. 분류기는 reason/breaches *값*에만 의존(라인 무관).
- R-2 (audit_log 정렬 컬럼 혼동): audit_log 는 `ts`(NOT `created_at`) 가 정렬 컬럼. 일부 모듈의
  `created_at` 정렬은 다른 테이블 대상 — 본 SPEC 은 `ts DESC` 사용(인덱스 보유).
- R-3 (수동/loss 오재개 = 자본 위험): hard safety. AC-3/AC-4/AC-5 가 manual·daily_loss·혼재를
  강제 검증. defensive default(불명 ⇒ HOLD)로 false-positive 재개를 차단.
- R-4 (07:25/07:30 경합): 본 잡 작업량은 DB 1~2 조회 + 선택적 reset 으로 수 초 — 07:30 과 무충돌.
- R-5 (breaches 형식 가정): A-4 의 prefix 가정에 의존. breaches 누락/비리스트 시 undeterminable
  로 안전 처리(AC-7).
- R-6 (텔레그램 장애로 잡 중단): send 실패 swallow(circuit_breaker 선례) — 재개 판정/실행은 계속.

## Dependencies

- 선행 SPEC 무차단(additive). SPEC-016(halt/limits 기원)/024(runner cron home, daily_count 폭증
  원인)/031(reset 의 halt_notified_at 초기화)/015(system_briefing) 와 호환.
- 신규 마이그레이션 없음(per-day guard 미채택 시). 채택 시 번호 024.

## Out of Scope

- 회로차단 트립 조건/리스크 한도(limits.py) 변경, trip()/reset()/`/halt`/`/resume` 내부 동작 변경,
  daily_loss 자동 재개, per-day guard, scheduler.yaml 런타임 로더 도입, 다른 cron/브리핑 변경.
