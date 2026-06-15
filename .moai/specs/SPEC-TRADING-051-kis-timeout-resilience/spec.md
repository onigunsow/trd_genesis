---
id: SPEC-TRADING-051
version: 0.2.0
status: draft
created: 2026-06-15
updated: 2026-06-15
author: oni
priority: high
issue_number: null
labels: ["trading", "kis", "resilience", "backend", "reliability"]
---

# SPEC-TRADING-051: KIS API 타임아웃 회복탄력성 — httpx 예외 재시도·분할 타임아웃 (SPEC-043 후속)

## HISTORY

- 0.2.0 (2026-06-15): plan-auditor 1차 감사(REVISE 0.62) 반영 개정. 코드 검증으로 확인된 결함 수정: **(D1, blocker)** REQ-051-A1을 A1a(get — 멱등 읽기, 자유 재시도)와 A1b(post — 주문 제출, **타임아웃 재시도 금지·즉시 raise**)로 분리. `order.py` L294-309 검증 결과 현재 post 타임아웃 동작은 "error 마킹+audit+re-raise(재시도 없음)"이며 안전하다. mig 007 UNIQUE 인덱스는 응답 *후* 발급되는 `kis_order_no`(ODNO)에 걸려 있어 재시도된 post(새 주문, 새 ODNO)에 대해 멱등성 보호가 0이므로 post 재시도는 **이중 주문 위험**이 있다. Exclusion #8 추가, EC-1을 "지연 결정"에서 하드 테스트로 전환. **(D2, major)** ADR-002 규범화 — `KisTimeoutError`는 반드시 `RuntimeError`를 상속한다. `cli.py` L292 fill-sync는 `except KisError`(+ L295 `except RuntimeError` 폴백)를 잡으므로(research.md 정정), KisError 하위로 두어 기존 호출자 호환 보장. **(D3, major)** ADR-005 추가 — 재시도 소진 시 호출당 최악 wall-time(~70s)이 워치독 `*/5`(300s) 주기와 충돌하지 않음을 분석, 타임아웃 경로 재시도 캡을 4보다 낮추는 옵션 명시. **(D4, minor)** REQ-051-A4 규범 텍스트를 구조(분할·명명상수)로 한정, 구체 초 값은 ADR-001/config로 이동.
- 0.1.0 (2026-06-15): 최초 초안. SPEC-043(능동적 TPS 페이싱)이 rate-limit 응답 빈도는 낮췄으나, KIS 측 응답 지연으로 인한 httpx 예외(ReadTimeout/ConnectTimeout/TransportError)는 **단 한 번의 시도 후 즉시 호출자에 전파**되는 격차를 메운다. 2026-06-15 trading-scheduler 로그에서 fill_sync failed 89건·position_watchdog 잔고 읽기 실패 14건(손절 워치독 실명)·intraday_adaptive failed 5건이 전부 httpx.ReadTimeout 트레이스백으로 종료됨을 근거로 함. 시장 중립 순수 KIS 레이어 유지, paper/live 동일 코드 경로. brownfield delta. **코드 미구현(plan only).**

---

## 배경 (WHY)

SPEC-043은 `_RateGate`(프로세스 전역 페이싱 게이트, `KIS_MIN_REQUEST_INTERVAL_SECONDS=0.4`)를 도입하여 KIS의 "초당 거래건수 초과"(rt_cd="1", EGW00201) **응답**을 능동적으로 줄였다. 하지만 그것은 KIS가 정상적으로 *응답을 돌려주는* 경우의 문제다. KIS 서버가 응답을 지연시켜 **httpx가 예외를 던지는** 경우는 SPEC-043이 다루지 않았고, 현재 `get()`/`post()`의 재시도 루프는 이 예외를 전혀 처리하지 못한다.

### 진단된 근본 원인 (재조사 불필요 — 코드 검증 완료)

파일: `src/trading/kis/client.py`

```python
for attempt in range(RATE_LIMIT_RETRIES + 1):
    _GATE.acquire()
    with httpx.Client(timeout=timeout) as client:
        r = client.get(...)        # <-- 여기서 httpx.ReadTimeout 발생, try/except 없음
    resp = self._parse(r)
    if not self._is_rate_limited(resp) or attempt == RATE_LIMIT_RETRIES:
        return resp
    backoff = RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1)
    time.sleep(backoff)
```

- 재시도 루프는 KIS가 **rate-limit 응답**(`_is_rate_limited`)을 돌려줄 때만 재시도한다.
- httpx 예외(ReadTimeout/ConnectTimeout/PoolTimeout = `httpx.TimeoutException`; ConnectError 등 = `httpx.TransportError`)는 `client.get()`/`client.post()`에서 던져져 **재시도 0회로 즉시 호출자에 전파**된다.
- 현재 기본값 `timeout: float = 10.0` (connect/read/write/pool에 동일하게 적용되는 단일 스칼라).
- 상수: `RATE_LIMIT_RETRIES=4`, `RATE_LIMIT_BACKOFF_SECONDS=1.0`, `KIS_MIN_REQUEST_INTERVAL_SECONDS=0.4`.

### 운영 증거 (2026-06-15 trading-scheduler 로그)

| 증상 | 발생 횟수 | 종결 트레이스백 |
|------|----------|----------------|
| `fill_sync failed` | 89건 | 전부 httpx.ReadTimeout |
| `position_watchdog: could not read balance — skipping poll` | 14건 | 동일 ReadTimeout — **그 폴 동안 손절/익절 워치독 실명** |
| `intraday_adaptive failed` | 5건 | 결정 사이클 중 httpx 타임아웃 |
| `KIS rate limited` 경고 | 694건 (15시대 162건 집중) | SPEC-043 페이싱 적용분(별개 문제) |

position_watchdog의 14건은 `client.py` 172번 줄의 기존 `@MX:REASON`이 경고한 정확한 실패 모드다: "TPS-breach 잔고 읽기 실패로 인한 워치독 실명 구간이 손절을 놓칠 수 있다". SPEC-043 페이싱은 rate-limit *응답*은 줄였으나 타임아웃 *예외* 회복탄력성은 다루지 않았다 — 그 격차가 이 SPEC의 대상이다.

---

## 핵심 아키텍처 제약 (HARD)

- [HARD] KIS 레이어는 시장 중립(market-neutral)으로 유지한다. paper/live 동일 코드 경로(`get()`/`post()` 단일 구현).
- [HARD] 기존 `_RateGate` 및 SPEC-043 REQ-043-B 동작을 약화하거나 제거하지 않는다(회귀 0).
- [HARD] 결정적 테스트를 위한 주입형 clock/sleep seam을 유지·확장한다(`_RateGate`의 `now`/`sleep` 패턴, `tests/kis/test_rate_gate.py`의 `FakeClock`과 호환).
- [HARD] 주문 제출(`post`)은 현재처럼 rate gate를 통과하지 않는다(주문 페이싱 미적용 정책 보존).

---

## 기존 시스템 컨텍스트 (BROWNFIELD)

### [EXISTING] 그대로 재사용하는 자산

| 영역 | 위치 | 역할 |
|------|------|------|
| KIS REST 클라이언트 | `src/trading/kis/client.py` get() (L159-188), post() (L190-206) | 페이싱·rate-limit 재시도·파싱 |
| 페이싱 게이트 | `src/trading/kis/client.py` _RateGate (L40-79), _GATE 싱글톤 (L79) | SPEC-043 전역 TPS 페이싱(injectable now/sleep) |
| rate-limit 상수 | `RATE_LIMIT_RETRIES=4` (L28), `RATE_LIMIT_BACKOFF_SECONDS=1.0` (L29), `RATE_LIMIT_MSG_CODES` (L31) | 재시도 예산·backoff |
| 예외 타입 | `KisError` (L92), `KisResponse` (L82) | KIS 에러 표현 |
| 페이싱 테스트 | `tests/kis/test_rate_gate.py` FakeClock (L14-26) | 결정적 clock/sleep 픽스처 |
| 호출자 (try/except 보유) | fill_sync(SPEC-029/042), position_watchdog(SPEC-033) L401-407, intraday_adaptive | 자체 try/except로 격리 |

[EXISTING] 마이그레이션 최신 = 033. 이 SPEC은 **스키마 변경이 필요 없다**(DB 마이그레이션 없음).

### 현재 격차 (이 SPEC이 메우는 것)

- `client.get()`/`client.post()`가 던지는 httpx 예외에 대한 try/except와 재시도 경로가 **전혀 없음** → 첫 타임아웃에 즉시 전파(재시도 0회).
- connect/read를 구분하지 않는 단일 스칼라 타임아웃(10.0s) → 빠른 연결 실패와 느린 응답을 같은 임계로 취급.
- 네트워크/타임아웃 기원과 rate-limit 소진을 구별하는 타입드 예외가 **없음** → 호출자가 로그에서만 구분 가능.
- `get()`/`post()`의 backoff sleep이 모듈 레벨 `time.sleep`을 직접 호출 → 결정적 테스트 불가(seam 부재).

---

## 요구사항 (EARS)

요구사항 모듈 2개: A(클라이언트 회복탄력성, 핵심) + B(워치독 실명 완화, ADR로 범위 결정) + NFR(비기능).

### REQ-051-A: KIS 클라이언트 타임아웃 회복탄력성 [MODIFY]

`@MX:SPEC SPEC-TRADING-051 REQ-051-A` — `src/trading/kis/client.py` get()/post()

- **REQ-051-A1a** (Event-Driven): KIS **`get()`**(조회 — 멱등 읽기) HTTP 호출이 `httpx.TimeoutException` 또는 `httpx.TransportError`를 던지면(WHEN), 클라이언트는 첫 실패에 즉시 전파하지 않고 동일한 재시도 예산(`RATE_LIMIT_RETRIES`, 또는 ADR-005의 타임아웃 전용 캡) 안에서 backoff를 적용하여 재시도해야 한다(shall). (rate-limit 재시도와 동일 예산 경로를 공유하되 예외 발생도 한 attempt로 소비한다 — ADR-004.) get은 멱등이므로 재시도가 안전하다.
- **REQ-051-A1b** (Unwanted): KIS **`post()`**(주문 제출) HTTP 호출이 `httpx.TimeoutException` 또는 `httpx.TransportError`를 던지면(IF), 클라이언트는 **타임아웃 재시도를 해서는 안 되며**(then shall not retry), 정확히 1회의 HTTP POST만 발생시킨 뒤 즉시 타입드 예외(`KisTimeoutError`)를 raise해야 한다(shall). 근거: post 타임아웃은 KIS가 주문을 *거부*했음을 의미하지 않는다 — 서버가 접수·체결했으나 응답만 유실됐을 수 있다. 멱등성 키(KIS client-order-id)가 없는 상태에서 재시도하면 **새 ODNO를 가진 별개 주문이 생성**되어 mig 007 UNIQUE 인덱스(응답 후 발급되는 `kis_order_no` 기준)가 보호하지 못하므로 이중 주문 위험이 있다. 즉시 raise는 기존 `order.py` L294-309의 "error 마킹+audit+re-raise → SPEC-042 broker-truth reconcile" 안전 경로를 보존한다. (ADR-002/Exclusion #8 참조)
- **REQ-051-A2** (Event-Driven): get() 재시도 시도마다(WHEN), 클라이언트는 `_RateGate` 페이싱 슬롯을 재획득해야 한다(shall) — SPEC-043 REQ-043-B 페이싱 보존(회귀 금지). 또한 주문 제출(`post`)은 현재처럼 rate gate를 통과시키지 않아야 한다(shall not gate post).
- **REQ-051-A3** (Event-Driven): get() 재시도 예산이 소진되거나 post()가 타임아웃하면(WHEN), 클라이언트는 네트워크/타임아웃 기원을 rate-limit 소진과 **구별 가능한** 타입드 예외 `KisTimeoutError`를 던져야 한다(shall). [HARD] `KisTimeoutError`는 반드시 `RuntimeError`를 상속해야 한다(ADR-002) — 호출자(fill_sync `cli.py` L292 `except KisError`/L295 `except RuntimeError`, position_watchdog `except Exception`, intraday)의 기존 except 절이 깨지지 않고 포착하도록 보장한다. raw `httpx` 예외가 그대로 전파되어서는 안 된다(shall not).
- **REQ-051-A4** (Ubiquitous): 클라이언트는 단일 스칼라 대신 분할 `httpx.Timeout`(짧은 connect, 긴 read)을 명명된 튜닝 가능 상수로 사용해야 한다(shall). 구체 초 값은 ADR-001/config에 둔다(규범 텍스트는 구조만 강제). 호출별 `timeout` 파라미터 오버라이드는 계속 지원해야 한다(shall) — 스칼라가 주어지면 분할 타임아웃으로 일관 해석한다.
- **REQ-051-A5** (Ubiquitous): `get()`/`post()`의 backoff sleep은 결정적 테스트를 위해 주입 가능한 sleep seam을 통해 수행되어야 한다(shall) — `_RateGate`의 injectable `now`/`sleep` 패턴 및 `tests/kis/test_rate_gate.py`의 `FakeClock`과 호환되어야 한다. (모듈 레벨 `time.sleep` 직접 호출을 seam으로 대체.)

### REQ-051-B: 워치독 실명 완화 [범위 결정 = ADR-003]

`@MX:SPEC SPEC-TRADING-051 REQ-051-B`

- **REQ-051-B1** (Unwanted): position_watchdog가 한 폴에서 잔고를 읽지 못하면(IF), 시스템은 출구 모니터링 실명 구간을 단순히 무성(silent) 스킵하지 말고 최소화해야 한다(then shall) — 예: 짧은 경계 재시도 또는 last-known-good 가드. 단, 잘못된 손절을 유발할 수 있는 방식으로 **stale 데이터에 근거하여 행동해서는 안 된다**(shall not act on stale data in a way that risks a wrong stop).

**[범위 판단 — ADR-003 확정]** REQ-051-A1a(get 타임아웃 재시도)가 들어가면 잔고 읽기(get)는 재시도를 자동으로 얻으므로, 14건의 워치독 실명 대부분이 A1a만으로 해소된다. 따라서 **REQ-051-B는 본 SPEC 범위에서 제외(out of scope)**하고 별도 후속 SPEC으로 분리한다. 이유: (1) A1이 근본 원인(재시도 0회)을 제거하므로 B의 추가 가치가 한계적이다, (2) "stale 데이터로 손절하지 않기"와 "실명 구간 최소화"는 손절 정책(SPEC-040)과 얽혀 별도의 신중한 설계·인수 기준이 필요하다, (3) 본 SPEC의 초점(KIS 레이어 순수 회복탄력성)을 흐리지 않기 위함이다. 운영자가 A1 배포 후에도 워치독 실명이 잔존하면 그때 REQ-051-B 전용 SPEC을 연다. (운영자 확인 필요 — OQ-2 참조)

### REQ-051-NFR: 비기능 요구사항

- **REQ-051-NFR-1** (Ubiquitous): 이 SPEC 구현은 기존 테스트의 회귀를 0으로 유지해야 한다(shall). 특히 `tests/kis/test_rate_gate.py`(SPEC-043 페이싱)는 전부 GREEN을 유지한다(pre-existing 6건 제외).
- **REQ-051-NFR-2** (Ubiquitous): 모든 신규 동작은 TDD로 개발하며, httpx 예외 주입(첫 N회 실패 후 성공하는 transport/mock)으로 재현 우선 테스트를 작성해야 한다(shall). (재현 우선 — CLAUDE.md Rule 4)
- **REQ-051-NFR-3** (Ubiquitous): 이 SPEC은 DB 스키마를 변경하지 않으므로 신규 마이그레이션을 추가하지 않아야 한다(shall not). (현재 최신 033 유지)
- **REQ-051-NFR-4** (Ubiquitous): KIS 레이어는 시장 중립을 유지하며, 한국 시장 종속 상수를 client.py get()/post() 회복탄력성 로직에 새로 도입하지 않아야 한다(shall not). (timeout/retry 상수는 시장 무관 네트워크 파라미터)

---

## Exclusions (What NOT to Build) — 범위 제외

이 섹션은 [HARD] 필수이며 범위 폭주를 막는다.

1. **REQ-051-B(워치독 실명 완화) 구현 제외**: ADR-003에 따라 본 SPEC은 클라이언트 레이어 회복탄력성(A)만 구현한다. 워치독 last-known-good/경계 재시도는 후속 SPEC. (A1 배포 후 잔존 여부로 판단)
2. **SPEC-043 페이싱 변경 금지**: `_RateGate`, `KIS_MIN_REQUEST_INTERVAL_SECONDS`, 주문 비게이팅 정책을 약화·변경하지 않는다. 재시도마다 게이트 재획득은 기존 설계 유지.
3. **재시도 예산/backoff 정책 재설계 제외**: `RATE_LIMIT_RETRIES=4`/`RATE_LIMIT_BACKOFF_SECONDS=1.0`을 그대로 재사용한다. 지터(jitter)·지수 backoff 등 고도화는 본 SPEC 범위 밖(필요 시 후속).
4. **httpx → 다른 HTTP 라이브러리 교체 금지**: httpx 유지. 연결 풀링·HTTP/2 등 전송 계층 재설계 제외.
5. **DB 스키마/마이그레이션 추가 금지**: 본 SPEC은 순수 네트워크 회복탄력성. DB 변경 없음(최신 033 유지).
6. **호출자(fill_sync/intraday/watchdog) 내부 로직 변경 최소화**: 호출자는 새 타입드 예외를 기존 try/except로 받기만 하면 되며, 호출자 비즈니스 로직(B 제외)을 변경하지 않는다.
7. **circuit-breaker/halt 연동 추가 금지**: 타임아웃 소진을 halt_state나 회로차단기에 연결하지 않는다(별도 정책 영역). 본 SPEC은 재시도+타입드 예외 전파까지만.
8. **post() 타임아웃 재시도 제외 (D1 blocker)**: 주문 제출(`post`)에 대한 타임아웃 재시도는 **KIS 측 멱등성 키(client-order-id)가 존재하기 전까지 영구 제외**한다. post 타임아웃은 즉시 `KisTimeoutError`를 raise하고 기존 `order.py` "error 마킹 + SPEC-042 reconcile" 경로에 맡긴다. (재시도 시 새 ODNO 별개 주문 생성 → 이중 주문 위험, mig 007 UNIQUE 인덱스가 보호 불가)

---

## ADR (Architecture Decision Records)

### ADR-001: 분할 타임아웃 값 (connect≈5s / read≈15s)

- **결정**: 단일 스칼라 `timeout=10.0`을 `httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)`로 분할하고, 각 값을 명명 상수(예: `KIS_CONNECT_TIMEOUT_SECONDS=5.0`, `KIS_READ_TIMEOUT_SECONDS=15.0`)로 둔다.
- **근거**: KIS 응답 지연은 **연결**이 아니라 **응답 본문 수신(read)** 단계에서 발생한다(로그의 ReadTimeout 일색). 연결 자체가 안 되는 상황(서버 다운·네트워크 단절)은 빠르게(5s) 실패시켜 재시도 사이클을 신속히 돌리는 편이 낫고, 일시적으로 느린 정상 응답은 더 여유 있게(15s) 기다려 헛된 재시도를 줄인다. 단일 10s는 두 경우를 구별하지 못해 (a) 죽은 연결을 10s 붙잡거나 (b) 느린 정상 응답을 10s에서 끊어 불필요한 재시도를 유발했다.
- **호환**: 호출별 `timeout` 스칼라 오버라이드가 주어지면 `httpx.Timeout(scalar)`로 해석(connect=read=write=pool=scalar)하여 기존 호출 시그니처를 깨지 않는다.
- **상태**: 제안 — 운영자가 5s/15s 구체 값을 확인·조정할 수 있다(OQ-1).

### ADR-002: 타입드 예외 계층 (KisTimeoutError 분리) [규범]

- **결정 [HARD]**: 네트워크/타임아웃 기원 실패에 대해 `KisError`(KIS가 비성공 rt_cd를 응답한 경우)와 **구별되는** 신규 예외 `KisTimeoutError`(`httpx.TimeoutException`/`httpx.TransportError` 소진 또는 post 타임아웃 시)를 둔다. `KisTimeoutError`는 **반드시 `RuntimeError`를 상속**해야 한다 — 가장 깔끔한 방식은 기존 `KisError`(`client.py` L92, `RuntimeError` 하위) 아래에 두는 것이다.
- **근거**: 코드 검증 결과 호출자 except 절이 균일하지 않다 — `cli.py` L292 fill-sync는 `except KisError`(+ L295 `except RuntimeError`)를, position_watchdog/fills는 `except Exception`을 사용한다. `KisTimeoutError`가 `KisError`(또는 최소한 `RuntimeError`) 하위가 아니면 cli.py fill-sync가 **포착하지 못해 uncaught traceback**이 새어 나간다. `RuntimeError` 상속을 강제하면 세 호출자 패턴 모두 안전하다. 타임아웃(일시적·재시도 가능)과 rate-limit 소진을 타입으로 구별해 운영 진단도 쉬워진다.
- **상태**: 확정(규범) — `KisError` 하위 vs 별도 베이스의 선택지는 남지만, 어느 쪽이든 `RuntimeError`를 상속해야 한다는 제약은 확정. 구현 시 `grep -rn "except KisError" src/`로 전 호출자 except 절 재확인(OQ-3).

### ADR-003: REQ-051-B 범위 제외

- **결정**: 워치독 실명 완화(REQ-051-B)를 본 SPEC에서 제외하고 후속 SPEC으로 분리한다.
- **근거**: REQ-051-A1이 잔고 읽기에 4회 재시도를 부여하므로 14건 워치독 실명의 근본(재시도 0회)이 제거된다. last-known-good/stale-data 가드는 손절 정책(SPEC-040)과 얽혀 별도 인수 기준이 필요하며, 본 SPEC의 KIS 레이어 순수 회복탄력성 초점을 흐린다.
- **상태**: 확정(범위 제외) — A1 배포 후 워치독 실명 잔존 시 운영자가 전용 SPEC 개시(OQ-2).

### ADR-004: 재시도 예산 공유 (rate-limit과 httpx 예외 통합)

- **결정**: httpx 예외 재시도를 별도 예산이 아닌 기존 `RATE_LIMIT_RETRIES=4` 예산 안에서 처리한다. 한 attempt에서 (a) httpx 예외가 나면 backoff 후 재시도, (b) rate-limit 응답이면 기존대로 backoff 후 재시도, (c) 성공이면 반환.
- **근거**: 단일 루프·단일 예산이 단순하고(과복잡 방지), 페이싱 게이트(`_GATE.acquire()`)가 모든 attempt 진입부에서 호출되는 기존 구조를 그대로 보존한다. backoff(≥1s)가 게이트 간격(0.4s)보다 크므로 SPEC-043의 "재시도도 전역 TPS에 포함" 불변식이 유지된다.
- **상태**: 확정. (단, 최악 wall-time은 ADR-005에서 별도 제약)

### ADR-005: 재시도 소진 최악 wall-time 경계 (D3) [규범]

- **문제**: ADR-004가 `RATE_LIMIT_RETRIES=4`(총 5회 시도)를 재사용하고 각 시도가 `read=15s`(ADR-001)까지 대기하며 backoff `1+2+3+4=10s`가 더해지면, get() 한 번이 최악 **~70s** 동안 블록된 뒤 raise할 수 있다. position_watchdog는 `*/5`(300s) cron, intraday_adaptive/fill_sync도 지연 민감 cron이다 — 재시도 소진이 오히려 워치독 실명 구간을 *연장*하고 cron 중첩(pile-up)을 유발할 위험이 있다.
- **결정 [HARD]**: 타임아웃 경로의 **총 호출 wall-time에 명시적 상한(deadline)을 둔다**. 구현 옵션 (구현 시 택1):
  - (a) 타임아웃 전용 재시도 캡을 `RATE_LIMIT_RETRIES`(4)보다 낮게 설정(예: 타임아웃 재시도 2회) — rate-limit 재시도와 분리된 명명 상수 `KIS_TIMEOUT_RETRIES`.
  - (b) get() 전체에 총 deadline(예: 45s)을 두어 누적 시간이 초과하면 더 재시도하지 않고 raise.
- **제약**: 최악 wall-time이 워치독 `*/5`(300s) 주기와 intraday 예산 안에 들도록 보장하고, 인수 기준(AC-8)으로 검증한다. (구체 캡/deadline 값은 OQ-5로 운영자 확인)
- **상태**: 확정(상한 필요) — 구체 값은 OQ-5.

---

## Open Questions (운영자 확인 필요)

- **OQ-1 (ADR-001 타임아웃 값)**: connect=5s / read=15s 구체 값이 적절한가? 운영 로그상 정상 응답의 최대 read 시간을 알면 read 값을 더 정밀히 보정할 수 있다. (현 제안값으로 진행 가능, 추후 config 보정)
- **OQ-2 (ADR-003 REQ-051-B)**: REQ-051-B(워치독 실명 완화)를 본 SPEC에서 제외하고 후속으로 분리하는 데 동의하는가? (A1 배포 후 워치독 실명 잔존 여부로 후속 SPEC 개시 판단)
- **OQ-3 (ADR-002 예외 계층)**: `KisTimeoutError`를 기존 `KisError` 하위(가장 깔끔, 기존 except 자동 포착)로 둘지, 별도 베이스로 둘지? 어느 쪽이든 `RuntimeError` 상속은 확정. 후자 선택 시 호출자 except 절 검토가 선행되어야 한다.
- **OQ-4 (전용 브랜치)**: SPEC-043 메모리상 "main 단일 트렁크"가 기록되어 있다. 구현은 main에서 진행하는가, 전용 브랜치가 필요한가? (manager-git 위임 대상)
- **OQ-5 (ADR-005 wall-time 상한)**: 타임아웃 경로의 재시도 캡/총 deadline 구체 값(예: 타임아웃 재시도 2회 또는 총 45s)이 적절한가? 워치독 `*/5` 주기·intraday 예산과의 정합을 운영자가 확인.
