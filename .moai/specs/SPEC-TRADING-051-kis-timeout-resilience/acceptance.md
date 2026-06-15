# SPEC-TRADING-051 인수 기준 (Acceptance)

[HARD] 모든 인수 기준은 **재현 우선(reproduction-first)**이다. httpx.ReadTimeout을 주입하는 transport/mock(첫 N회 실패 후 성공)으로 작성하며, 실제 KIS·wall-clock에 의존하지 않는다(FakeClock/monkeypatch).

## 핵심 시나리오 (Given-When-Then)

### AC-1 (REQ-051-A1a): get() 타임아웃 후 재시도하여 성공
- **Given**: get() 호출에서 첫 2회 `httpx.ReadTimeout`을 던지고 3회째 정상 KisResponse를 반환하는 mock transport.
- **When**: `client.get(...)`을 호출한다.
- **Then**: 예외가 전파되지 않고 3회째 정상 응답이 반환된다. HTTP 호출이 3회 발생했음을 단언.

### AC-1b (REQ-051-A1b — D1 blocker): post() 타임아웃은 재시도하지 않고 즉시 raise
- **Given**: `post()`(주문 제출)가 `httpx.ReadTimeout`을 던지는 mock transport + HTTP POST 호출 횟수 spy.
- **When**: `client.post(...)`를 호출한다.
- **Then**: HTTP POST가 **정확히 1회만** 발생하고(재시도 0회), `KisTimeoutError`가 즉시 raise된다. **이중 주문(2번째 POST)이 발생하지 않음을 하드 단언**한다. (mig 007 UNIQUE 인덱스로 보호 불가한 새 ODNO 주문 생성 방지)

### AC-2 (REQ-051-A3): get() 예산 소진 시 타입드 예외
- **Given**: get() 호출이 매번 `httpx.ReadTimeout`을 던지는 mock transport(재시도 예산 모두 실패).
- **When**: `client.get(...)`을 호출한다.
- **Then**: `KisTimeoutError`가 raise되며, 일반 `KisError`(rate-limit 소진)와 **타입으로 구별**된다. raw `httpx.ReadTimeout`이 그대로 전파되지 않는다.

### AC-2b (REQ-051-A3 / ADR-002 — D2): KisTimeoutError는 기존 호출자 except가 포착
- **Given**: `KisTimeoutError` 인스턴스.
- **When**: `isinstance` 검사 및 호출자 except 절 시뮬레이션.
- **Then**: `isinstance(err, RuntimeError)`가 참이고, `isinstance(err, KisError)`(ADR-002 채택 계층이면) 참이다. `cli.py` fill-sync의 `except KisError`/`except RuntimeError` 절이 `KisTimeoutError`를 포착함을(uncaught traceback 미발생) 단언한다.

### AC-3 (REQ-051-A2): attempt당 _RateGate.acquire 1회
- **Given**: get()이 첫 1회 ReadTimeout 후 성공하는 mock + `_RateGate.acquire`를 카운트하는 spy.
- **When**: `client.get(...)`을 호출한다.
- **Then**: `_RateGate.acquire`가 attempt 수만큼(=2회) 정확히 호출된다. (재시도 attempt도 페이싱 슬롯을 재획득 — SPEC-043 불변식)

### AC-4 (REQ-051-NFR-1): SPEC-043 페이싱 회귀 0
- **Given**: 기존 `tests/kis/test_rate_gate.py` 전체.
- **When**: 본 SPEC 구현 후 테스트 스위트를 실행한다.
- **Then**: `test_rate_gate.py` 전부 GREEN. 전체 회귀 0(pre-existing 6건 제외).

### AC-5 (REQ-051-A4): 분할 타임아웃 적용
- **Given**: 기본 호출(timeout 미지정)과 스칼라 오버라이드 호출(timeout=8.0) 두 경우.
- **When**: 각각 get()을 호출하고 httpx.Client에 전달된 timeout을 검사한다(httpx.Timeout 캡처 spy).
- **Then**: 기본 호출은 `httpx.Timeout(connect=KIS_CONNECT_TIMEOUT_SECONDS, read=KIS_READ_TIMEOUT_SECONDS, ...)`로 구성된다. 스칼라 오버라이드는 `httpx.Timeout(8.0)`(connect=read=8.0)으로 일관 해석된다.

### AC-6 (REQ-051-A5): 결정적 sleep seam
- **Given**: backoff sleep을 FakeClock/monkeypatch로 가로채는 테스트.
- **When**: AC-1처럼 재시도가 발생하는 시나리오를 실행한다.
- **Then**: 실제 wall-clock sleep이 발생하지 않고(테스트가 즉시 완료), backoff 호출이 카운트된다. backoff 값이 `RATE_LIMIT_BACKOFF_SECONDS * (attempt+1)`과 일치.

### AC-7 (REQ-051-A2 — post 비게이팅 보존): 주문은 게이트 미통과
- **Given**: `post()`가 ReadTimeout을 던지는 mock + `_RateGate.acquire` spy.
- **When**: `client.post(...)`를 호출한다(AC-1b와 동일하게 즉시 raise).
- **Then**: post 경로에서 `_RateGate.acquire`가 호출되지 않는다(현재 정책 보존).

### AC-8 (REQ-051-A1a / ADR-005 — D3): get() 재시도 소진 최악 wall-time 경계
- **Given**: get()이 매번 타임아웃하는 mock + FakeClock(가상 시간 누적).
- **When**: `client.get(...)`을 호출해 재시도가 모두 소진된다.
- **Then**: FakeClock 누적 가상 시간(타임아웃 대기 + backoff)이 ADR-005가 정한 상한(재시도 캡 또는 총 deadline) 이내이며, 워치독 `*/5`(300s) 주기보다 작음을 단언한다. (실제 wall-clock sleep 없이 FakeClock으로 검증)

## 엣지 케이스

### EC-1 (REQ-051-A1b — D1 해소): post 이중주문 회귀 가드
- **Given**: `post()`가 타임아웃을 던지는 경우.
- **검증**: AC-1b가 채택한 raise-only 정책(POST 정확히 1회)이 회귀 테스트로 고정되어, 이후 변경으로 post 재시도가 재도입되면 테스트가 실패하도록 가드한다.
- **Then**: 이중주문 시나리오(2번째 POST)가 발생하면 테스트 RED. SPEC-042 단일원장/order.py error-마킹 경로와 충돌 없음을 확인.

### EC-2 (REQ-051-A1a): ConnectTimeout / TransportError 도 동일 처리
- **Given**: `httpx.ConnectTimeout`, `httpx.ConnectError`(TransportError 하위)를 던지는 mock.
- **Then**: ReadTimeout과 동일하게 재시도되며, 소진 시 `KisTimeoutError`로 통일 raise.

### EC-3: rate-limit 응답과 httpx 예외 혼재
- **Given**: attempt 1 = ReadTimeout, attempt 2 = rate-limit 응답(EGW00201), attempt 3 = 성공.
- **Then**: 두 종류의 재시도가 같은 예산을 공유하며 attempt 3에서 정상 반환(ADR-004).

## 품질 게이트 (Quality Gates)

- [ ] 신규/수정 테스트 전부 GREEN, 전체 회귀 0(pre-existing 6 제외).
- [ ] ruff check 통과, 타입 힌트 완비.
- [ ] httpx 예외에 대한 try/except가 get()/post() retry 루프에 존재.
- [ ] 분할 httpx.Timeout 적용 + 명명 상수 + 스칼라 오버라이드 호환.
- [ ] 타입드 예외(KisTimeoutError)가 `RuntimeError` 상속·KisError와 구별, cli.py fill-sync except 포착(AC-2b).
- [ ] post() 타임아웃은 재시도 0회·즉시 raise(AC-1b/EC-1) — 이중주문 가드.
- [ ] get() 재시도 소진 최악 wall-time이 ADR-005 상한·워치독 주기 이내(AC-8).
- [ ] backoff sleep이 주입 seam을 통해 결정적으로 테스트됨.
- [ ] DB 마이그레이션 미추가(최신 033 유지).
- [ ] `@MX:SPEC SPEC-TRADING-051` 주석이 변경 지점에 부착.

## Definition of Done

1. REQ-051-A1a/A1b/A2~A5 전부 구현·테스트 GREEN.
2. REQ-051-B는 ADR-003에 따라 범위 제외(후속 SPEC) — OQ-2 운영자 확인 반영.
3. SPEC-043 페이싱·SPEC-042 단일원장 회귀 0.
4. 운영 검증 게이트: 배포 후 다음 거래일 trading-scheduler 로그에서 fill_sync ReadTimeout 즉시실패가 재시도-성공 또는 `KisTimeoutError`(구별된 로그)로 전환됨을 관측. position_watchdog 잔고읽기 실명 건수 감소 확인.
