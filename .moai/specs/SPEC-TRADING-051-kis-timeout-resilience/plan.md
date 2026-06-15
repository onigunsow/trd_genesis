# SPEC-TRADING-051 구현 계획 (Plan)

## 기술 접근

단일 파일(`src/trading/kis/client.py`) 중심의 brownfield delta. 기존 retry 루프를 try/except로 감싸 httpx 예외를 같은 예산 안에서 재시도하도록 확장하고, 단일 스칼라 타임아웃을 분할 `httpx.Timeout`으로 교체하며, backoff sleep을 주입 가능한 seam으로 바꾼다. DB·마이그레이션 변경 없음.

### 핵심 변경 지점

1. **상수 추가** (client.py 상단, SPEC-043 상수 인근)
   - `KIS_CONNECT_TIMEOUT_SECONDS = 5.0`
   - `KIS_READ_TIMEOUT_SECONDS = 15.0`
   - (write/pool은 connect 값 재사용 또는 명명 상수)
   - `@MX:SPEC SPEC-TRADING-051 REQ-051-A4` 주석.

2. **타입드 예외 추가** (ADR-002)
   - `KisTimeoutError`(가칭) — `httpx.TimeoutException`/`httpx.TransportError` 소진 시 raise.
   - 베이스 계층은 OQ-3 확정 후: 기존 `KisError` 하위 또는 신규 `KisClientError` 베이스 형제.
   - 기존 `KisError`만 잡던 호출자가 깨지지 않도록 계층 설계.

3. **get()/post() retry 루프 확장** (REQ-051-A1a/A1b/A2/A4/A5)
   - `with httpx.Client(timeout=_resolve_timeout(timeout)) as client:` — 스칼라/분할 일관 해석 헬퍼.
   - HTTP 호출을 `try/except (httpx.TimeoutException, httpx.TransportError)`로 감싼다.
   - **get() (A1a)**: 예외 포착 시 마지막 attempt(또는 ADR-005 타임아웃 캡/deadline 도달)면 `KisTimeoutError` raise, 아니면 backoff 후 continue. `_GATE.acquire()`는 기존대로 모든 attempt 진입부에서 호출.
   - **post() (A1b)**: 예외 포착 시 **재시도 없이 즉시 `KisTimeoutError` raise**(POST 정확히 1회). post는 rate gate 미적용 유지. (rate-limit *응답* 재시도는 기존대로 존속하되, httpx *예외*는 즉시 raise)
   - backoff sleep을 모듈 `time.sleep` 직접 호출 대신 주입 가능한 seam으로(REQ-051-A5).

4. **sleep seam 도입** (REQ-051-A5)
   - 옵션 A: 모듈 레벨 `_sleep = time.sleep` 변수 → 테스트에서 monkeypatch.
   - 옵션 B: `_RateGate`처럼 클라이언트 생성자에 주입(더 명시적).
   - `tests/kis/test_rate_gate.py`의 `FakeClock`과 호환되는 형태 선택(구현 시 결정).

### 마일스톤 (우선순위 기반, 시간 추정 없음)

- **M1 (Priority High)**: 재현 테스트 먼저 — get() httpx.ReadTimeout 주입 transport/mock 작성, 현재 코드에서 즉시 전파(재시도 0회) 확인(RED). post() 1회-POST 가드 테스트(AC-1b)도 RED로.
- **M2 (Priority High)**: REQ-051-A1a/A2/A4 구현 — get() try/except 재시도 + 분할 타임아웃. **A1b**: post() try/except는 재시도 없이 즉시 `KisTimeoutError` raise(POST 정확히 1회). M1 테스트 GREEN.
- **M3 (Priority High)**: REQ-051-A3/A5/ADR-002 — `KisTimeoutError`(`RuntimeError` 상속, KisError 하위) + sleep seam. cli.py `except KisError`/`except RuntimeError` 포착 단언(AC-2b).
- **M4 (Priority High)**: ADR-005 wall-time 상한 — 타임아웃 재시도 캡 또는 총 deadline 구현, FakeClock으로 최악 누적시간이 워치독 `*/5` 주기 이내임 단언(AC-8).
- **M5 (Priority Medium)**: SPEC-043 페이싱 회귀 검증 — `test_rate_gate.py` 전부 GREEN, get attempt당 `_GATE.acquire()` 1회, post 비게이팅(AC-7) 단언.

## 위험 (Risks)

- **R1 (예외 계층 호환)**: 신규 예외가 cli.py `except KisError`를 우회하면 fill-sync uncaught traceback → **ADR-002로 `RuntimeError` 상속 강제(해소)**, grep으로 호출자 except 재확인.
- **R2 (post 이중주문) [해소]**: post 타임아웃 재시도는 **이중 주문** 위험(서버 접수·응답 유실 + mig 007 ODNO 멱등성 무력). → **REQ-051-A1b/Exclusion #8로 post 재시도 금지·즉시 raise 확정**. EC-1 회귀 가드로 재도입 방지.
- **R3 (페이싱 회귀)**: get() try/except 추가 중 `_GATE.acquire()` 호출 위치가 바뀌면 SPEC-043 불변식 깨짐 → attempt당 1회 단언 테스트로 가드.
- **R4 (sleep seam 누수)**: 테스트가 실제 wall-clock sleep을 돌면 느려짐 → FakeClock/monkeypatch 강제.

## 종속성

- SPEC-043(페이싱) 위에 쌓임 — 약화 금지.
- SPEC-042(단일원장·in-flight 락) — post 재시도 시 이중주문 방지와 교차 검토(R2).
- 후속 분리: REQ-051-B(워치독 실명 완화).

## 위임

- 구현: manager-tdd (quality.yaml development_mode 따름).
- 브랜치/PR: manager-git (OQ-4 확정 후).
