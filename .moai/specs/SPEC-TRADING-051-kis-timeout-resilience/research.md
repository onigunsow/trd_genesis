# SPEC-TRADING-051 연구 (Research) — 코드베이스 검증

## 근본 원인 코드 검증 (확정)

`src/trading/kis/client.py`:
- `get()` L159-188, `post()` L190-206: retry 루프가 `client.get()`/`client.post()`를 try/except 없이 호출. httpx 예외(ReadTimeout 등)는 즉시 전파. **재조사 불필요 — 코드와 100% 일치.**
- 재시도 조건은 `_is_rate_limited(resp)`(rt_cd="1" + EGW00201/"초당 거래건수")일 때만. httpx 예외 경로 없음.
- `timeout: float = 10.0` 단일 스칼라.
- backoff는 `time.sleep(backoff)` 모듈 직접 호출 — get() L187, post() L205. (seam 부재)
- `_RateGate`(L40-79)는 injectable `now`/`sleep` 보유 → 이미 결정적 테스트 가능. `_GATE` 싱글톤 L79.
- 기존 `@MX:REASON`(L172): "blind exit-watchdog window caused by a TPS-breach balance read failure could miss a stop" — REQ-051-B 동기와 정확히 일치.

## httpx 예외 분류 (검증)
- `httpx.TimeoutException` 베이스: ReadTimeout, ConnectTimeout, WriteTimeout, PoolTimeout.
- `httpx.TransportError` 베이스: ConnectError, ReadError, NetworkError 등. (TimeoutException도 TransportError 하위)
- 두 베이스를 함께 잡으면 타임아웃+전송 오류를 포괄. (httpx 0.x 계층 — 구현 시 `import httpx` 버전 확인)

## 기존 테스트 컨벤션 (재사용)
- `tests/kis/test_rate_gate.py`: `FakeClock`(monotonic + sleep 카운트, L14-26). 본 SPEC의 sleep seam·재시도 테스트가 이 패턴을 따른다.
- `tests/kis/` 디렉터리에 14개 테스트 파일 존재. 신규 테스트는 `tests/kis/test_client_timeout_resilience.py`(가칭) 또는 기존 `test_client_pacing_wiring.py` 확장.

## 호출자 except 절 (코드 검증 — 0.2.0 정정)
호출자 except 절은 **균일하지 않다**(0.1.0의 "모두 broad except Exception" 주장은 거짓이었음, plan-auditor D2 지적):
- `position_watchdog.py` L401-407: `except Exception` → 신규 예외 자동 포착(안전).
- **`cli.py` L292 fill-sync: `except KisError as e`** (+ L295 `except RuntimeError` 폴백). → `KisTimeoutError`가 `KisError`/`RuntimeError` 하위가 **아니면 uncaught traceback 누출**.
- `KisError`(`client.py` L92)는 `RuntimeError`를 상속한다(검증).
→ 결론(ADR-002 규범): `KisTimeoutError`는 **반드시 `RuntimeError` 상속**(가장 깔끔: `KisError` 하위). cli.py의 `except KisError`/`except RuntimeError`, watchdog의 `except Exception` 세 패턴 모두 안전.

## post() 타임아웃 안전 경로 (코드 검증 — D1)
- `order.py` L293-309: post 호출이 `except Exception`에서 orders.status='error' + audit_log + **re-raise(재시도 없음)**. 안전한 현재 동작.
- `kis_order_no`(ODNO)는 응답 성공 후 `out["ODNO"]`에서 읽음(L314-318). mig 007 UNIQUE 인덱스는 이 ODNO에 걸림 → 재시도된 post는 **새 ODNO 별개 주문** 생성, 멱등성 보호 0.
- SPEC-042 `sell_lock`(in-flight 락)은 **결정 레이어**(watchdog vs orchestrator)에서 작동, `client.post()` 내부 재시도는 그 위라 보이지 않음 → post 재시도를 막지 못함.
→ 결론(REQ-051-A1b/Exclusion #8): post 타임아웃은 **재시도 금지·즉시 raise**.

## 마이그레이션 상태
- 최신 = `033_edge_hardening.sql`(SPEC-048). 본 SPEC은 **DB 변경 없음** → 마이그레이션 미추가(NFR-3).

## 시장 중립성
- KIS 레이어(client.py)는 이미 시장 무관(paper/live는 tr_id·base_url·credential만 분기, L103-139). timeout/retry 상수는 네트워크 파라미터로 시장 무관 → NFR-4 충족 용이.

## 미해결 (구현 전 확인)
- ADR-002 예외 베이스 계층: `KisError` 하위 vs 별도 베이스 — 어느 쪽이든 `RuntimeError` 상속 확정. 구현 시 `grep -rn "except KisError" src/`로 재확인(OQ-3).
- ADR-005 wall-time 상한: 타임아웃 재시도 캡(예: 2회) 또는 총 deadline 구체 값(OQ-5).
