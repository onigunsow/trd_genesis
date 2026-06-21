# SPEC-TRADING-055 — resolver 건강 자가점검 → 텔레그램 보고

작성: 2026-06-21 · 기준 origin/main: 04fa6f1 · 범위: 운영 가시성(읽기 위주 + 하트비트 1컬럼)

## 배경 / 운영자 요구
운영자는 평일 직장인이라 **장중 로그/대시보드 직접 리뷰 불가**([[feedback_no_daytime_review]]). SPEC-042 D3 resolver cron(평일 09:02~14:57 */5) 동작을 "장중에 직접 확인"하는 게이트는 무효. → 시스템이 **자가 점검 → 텔레그램 자동 보고**(장마감 후 요약 + 이상 시 즉시 경고). 운영자 결정 = **경고 + 일일요약 둘 다**.

## 핵심 설계 판단
- **parity는 장중 정상 드리프트**(매수 직후 orders_net>positions, 다음 reconcile까지)이므로 **장중 경고 대상 아님** → 장마감 후(일일리포트 시점)에만 평가. 장중 5분마다 parity 경고하면 스팸.
- **resolver 크론 자체는 "죽으면 자기 경고 불가"** → resolver-dead는 **외부 체커**(일일리포트)가 하트비트 신선도로 감지.
- 따라서: resolver 크론은 **하트비트만 기록**(소음 0), 모든 이상 평가는 **장마감 후 일일리포트 흐름**에서 1회.

## 근거 (코드 실측, Explore 2026-06-21)
| 대상 | 사실 | file:line |
|---|---|---|
| system_state | 싱글톤(id=1) 컬럼-퍼-플래그. `get_system_state()`/`update_system_state(**fields)`. 타임스탬프는 값 바인딩(예: `halt_notified_at`, _NOW_FIELDS 아님). | `db/session.py:81-115` |
| 스로틀 패턴 | `maybe_notify_halt`: `halt_notified_at` + cooldown, system_state 영속(재시작 생존). | `risk/circuit_breaker.py:35-70` |
| resolver 실행점 | `_run_resolver()`(지연임포트+KisClient+resolve_stuck_orders+LOG). | `scheduler/runner.py:93-105` |
| 일일리포트 | `generate_and_send()`(`_fallback_text(data)` 조립→`system_briefing("일일 리포트", text)` 전송). 섹션은 f-string. | `reports/daily_report.py:568-600`, `_fallback_text:290-379` |
| 텔레그램 전송 | `system_briefing(category, message)`. | `alerts/telegram.py:70-73` |
| parity | `orders_positions_divergence()→{"parity":bool,"by_ticker":{...}}`. | `kis/ghost_convergence.py:271-311` |
| stuck count | `SELECT COUNT(*) FROM orders WHERE status='submitted'`. | `cli.py`, `smoke_gate.py:71` |
| 거래일 | `is_trading_day(d=None)`(주말·공휴일·12/31 제외). 장중 시간 헬퍼는 **없음**. | `scheduler/calendar.py:25-40` |
| 테스트 | conftest `FakeCursor`/`FakeConnection`/`patch_db_connection`. **DB더블 거짓그린 위험**(컬럼 미검증). | `tests/conftest.py:15-87` |

## M1 — mig 039 + resolver 하트비트
1. **mig 039** `039_resolver_heartbeat.sql`:
   ```sql
   ALTER TABLE system_state ADD COLUMN IF NOT EXISTS last_resolver_run TIMESTAMPTZ;
   ALTER TABLE system_state ADD COLUMN IF NOT EXISTS resolver_anomaly_notified_at TIMESTAMPTZ;
   ```
2. **`scheduler/runner.py` `_run_resolver()`**: `resolve_stuck_orders` 직후 `update_system_state(last_resolver_run=datetime.now(UTC), updated_by="resolver")`. (하트비트만 — scanned=0이어도 "발화함" 증명. 경고 없음.)

## M2 — 자가점검 모듈 + throttle 경고
**신규** `src/trading/ops/resolver_health.py`:
- `evaluate_resolver_health(*, now=None) -> dict`:
  - `last_resolver_run = get_system_state().get("last_resolver_run")`.
  - `resolver_fresh`: **거래일이면** `last_resolver_run`의 KST date == 오늘 KST(=오늘 발화함). **비거래일이면 True**(발화 안 하는 게 정상, is_trading_day로 분기).
  - `stuck_count`: `SELECT COUNT(*) ... status='submitted'`.
  - `parity`: `orders_positions_divergence()["parity"]`.
  - `anomalies: list[str]`(거래일인데 resolver 미발화 / stuck>0 / parity False), `healthy = not anomalies`.
- `summary_line(h) -> str`: `"SPEC-042 운영점검: resolver {✓ 오늘 HH:MM | ⚠ 미발화} · stuck {0|⚠N} · parity {OK|⚠불일치}"`.
- `maybe_notify_resolver_anomaly(h, *, cooldown_seconds=None, now_provider=None) -> bool`: `healthy`면 no-op False. 아니면 `resolver_anomaly_notified_at` + cooldown(기본 6h) throttle → `system_briefing("운영 이상", <anomalies 요약 + 권장조치>)` + stamp. (maybe_notify_halt 미러, system_state 영속.)

## M3 — 일일리포트 배선 + 검증·배포
1. **`reports/daily_report.py` `generate_and_send()`**: 리포트 텍스트에 `summary_line(health)` 추가(`_fallback_text`에 섹션 빌더로, 최종 return 전), 리포트 전송 후 `maybe_notify_resolver_anomaly(health)` 호출(이상 시 별도 경고 = "둘 다").
   - health는 generate_and_send에서 1회 evaluate해 라인+경고 공유.
2. **테스트**:
   - `tests/ops/test_resolver_health.py`: healthy / stuck>0 / parity False / resolver 미발화(거래일) / 비거래일(미발화 정상) 각 케이스 → anomalies·summary_line·healthy. throttle(쿨다운 내 2회차 False).
   - 일일리포트 라인 포함 테스트.
   - **★거짓그린 방지**: `evaluate_resolver_health`의 SQL(stuck count·system_state read)은 conftest FakeCursor가 컬럼 미검증이므로, **배포후 라이브 `docker exec`로 evaluate_resolver_health 실행 + 수동 일일리포트 생성**을 필수 게이트로(오늘=일요일에 즉시 가능, 월요일 안 기다림). 메모리 2연발 교훈(positions.mode·order_type) 반영.
3. **배포**: 커밋·푸시 → make redeploy(app/bot/scheduler) → `docker exec trading-app trading migrate`(mig 039) → dashboard-api 무관(재생성 불요) → **라이브검증**: ① `evaluate_resolver_health` 크래시 없이 dict 반환(실 스키마) ② 수동 일일리포트 1회 생성해 운영점검 라인 렌더 + 텔레그램 도착 ③ 일요일이라 resolver_fresh=True(비거래일 분기) 확인.
4. **관측(자동, 운영자 무동작)**: 월요일 16:00 일일리포트에 `resolver ✓ 오늘 HH:MM · stuck 0 · parity OK` 라인 → 운영자 저녁 폰 확인. 이상 시 "운영 이상" 별도 경고 push.

## 비범위
- 장중 실시간 경고(장마감 후 평가로 한정). resolver-dead는 일일리포트 하트비트 신선도로 감지.
- live 경로 불변(읽기 + system_state 2컬럼 쓰기만).

---

## 감사 반영 (plan-auditor 0.62 FAIL → 필수 수정) — 2026-06-21
(파일 경로는 전부 `src/trading/` 접두사. evidence 표 인용 경로 보정.)

### D1 [CRITICAL] — 자가점검 격리: 일일리포트를 절대 못 죽이게
`generate_and_send`는 `_wrap`(runner.py:443)에 감싸여 예외를 삼킴. `evaluate_resolver_health`가 raise하면 persist(:593)·`system_briefing`(:597) **이전**에 전파 → **일일리포트 전체 소실**(운영자 유일 신호). 스키마 불일치(과거 2연발류)가 정확히 이 트리거.
- **수정**: `generate_and_send`에서 `evaluate_resolver_health`/`maybe_notify_resolver_anomaly`를 **각각 try/except**로 감싸고, 예외 시 degraded 라인(`"운영점검: 평가 실패 — {err}"`)으로 폴백. 헬스체크는 **리포트 본문/전송을 절대 중단 못 함**. [AC] raise하는 health eval 주입해도 리포트가 전송됨을 단언하는 테스트 필수.

### D2 [MAJOR] — 일요일 라이브 게이트에 쓰기 경로 포함
과거 2연발은 **쓰기 경로** 결함(positions.mode·order_type). 신규 쓰기 2곳이 일요일에 미실행: ①하트비트 `update_system_state(last_resolver_run=...)`는 `_run_resolver`(비거래일 `_wrap` 차단) ②`resolver_anomaly_notified_at` 스탬프는 healthy면 미실행. → 월요일까지 미검증.
- **수정(라이브 게이트 추가)**: 배포후 `docker exec`로 (a) `update_system_state(last_resolver_run=datetime.now(UTC), updated_by="resolver")` 직접 호출 후 `get_system_state()`로 read-back, (b) `resolver_anomaly_notified_at` 직접 스탬프 1회 — **mig 039 적용된 실 스키마에 두 신규 컬럼 쓰기 성공**을 일요일에 증명(positions.mode급 결함 월요일 전 차단).

### D3 [MAJOR] — parity 전제 정정 + cry-wolf 방지
**전제 오류**: `_synthetic_fill`(order.py:154-199)이 orders·positions를 **한 트랜잭션**에 갱신(order.py:104-106) → parity는 결합, 매수가 parity 안 깸. "장중 드리프트" 근거 폐기(결론=장마감 후 평가는 유지: reconcile가 진실원).
**진짜 위험**: `fill_sync`/`reconcile_from_balance`(runner.py:312-318, ~15:59까지)가 KIS ReadTimeout(SPEC-051 6/15 사례)로 **실패 시 positions 정체 → 16:00 parity 거짓 False → cry-wolf**.
- **수정**: ①parity는 **soft 신호**로 격하 — summary 라인에 `by_ticker diff 종목수·수량` 표기(맨숫자 ⚠ 아님). ②parity-False 경고는 "드리프트 또는 reconcile 지연 — 확인 필요" 주석(결함 단정 금지). ③**hard 경고는 resolver 미발화·stuck>0 두 자본보존 항목만**(즉시 alert). parity-False는 일일리포트 라인에 표기 + 경고엔 주석부 포함.

### D4 [MAJOR] — mig 039 자가기록 패턴
038은 `DO $$ ... IF NOT EXISTS(SELECT 1 FROM schema_migrations WHERE version='038...') ... ALTER ... INSERT schema_migrations ... INSERT audit_log('SCHEMA_MIGRATED') END $$`. migrate.py:37-49가 자가기록 기대. 계획의 bare ALTER는 매 migrate 재적용·미추적.
- **수정**: 039를 038과 동일한 `DO $$` + `schema_migrations('039_resolver_heartbeat')` 자가기록 + `SCHEMA_MIGRATED` audit 패턴으로 작성(내부 ALTER는 IF NOT EXISTS 유지).

### D5 [MINOR] — tz 명시
`last_resolver_run`(UTC 저장)을 "오늘 KST"와 비교 시 **명시적 `.astimezone(KST)` 후 `.date()`**. `resolver_anomaly_notified_at` 비교도 동일 tz 규율.

### D6 [MINOR] — 비거래일 분기는 수동전용
`generate_and_send`는 `_wrap`가 비거래일 차단 → "비거래일 True" 분기는 **16:00 크론으론 도달 안 됨**(수동 `docker exec` 검증 전용). 방어/수동 호출용임을 주석 명시(버그 아님).

### D7 [MINOR] — migrate-before-redeploy
배포 순서를 **migrate 우선** 권장(또는 `_wrap`가 컬럼부재 write를 삼켜 무해함을 명시). 향후 평일 재배포 시 migrate 전 하트비트 write가 컬럼부재로 무음 no-op → 거짓 `resolver_fresh=False` 방지.
