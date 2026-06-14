# SPEC-TRADING-045 구현 계획 (Plan)

## 기술 접근 (Technical Approach)

본 SPEC은 SPEC-042가 의도적으로 남긴 **단일 seam**(`broker_truth.confirm_fills()`의 live 분기)을
실제 KIS 체결조회로 구현하는 **drop-in** 작업이다. 신규 파이프라인을 만들지 않고, 기존 단일
체결확인 경로 안에서만 live 분기를 채운다. 작업 범위는 `src/trading/kis/` 한정.

### 핵심 설계 결정 (ADR)

#### ADR-045-1 — live 체결 확인: polling vs websocket

라이브 체결 확인의 진짜 설계 분기:

- **(a) Polling** — `inquire-daily-ccld` 체결조회를 사이클 단위로 조회(기존 `confirm_fills` seam에
  자연스럽게 들어맞음). SPEC-043 페이서 경유, 호출 빈도는 "매도 사이클 전 + 주문 후"로 제한
  (SPEC-042 reconcile 케이던스 재사용).
- **(b) Websocket** — KIS 라이브 체결통보 push 구독. 실시간이지만 별도 연결 수명관리·재연결·
  운영 복잡도 증가, 그리고 운영자 계정 가용성 확인 필요([확인 필요-3]).

**권고: (a) Polling.** 근거:
1. SPEC-042의 단일 seam(`confirm_fills`) 구조에 **drop-in**으로 들어맞아 신규 상태기계가 불필요.
2. SPEC-043 전역 페이서로 TPS 예산이 이미 통제됨 — polling 호출은 "매도 사이클 전 + 주문 후"로
   한정되어 추가 부담이 작음(reconcile와 동일 케이던스 공유 가능).
3. 운영 단순성: websocket 연결 수명/재연결 실패는 6/8 같은 "watchdog blind" 사고의 새 표면이 됨.
4. 6/8 실패의 본질은 "실시간성 부족"이 아니라 "체결 확인 경로 자체의 부재"였음 → polling만으로
   근본 공백이 닫힘.

**트레이드오프:** polling은 체결 인지에 최대 한 케이던스(수십 초~수분) 지연. 그러나 매도 의도는
다음 사이클에서 KIS 진실원으로 재평가되므로(REQ-045-D2/B3) 안전. 향후 저지연이 필요하면 websocket을
후속 SPEC으로 **추가**(대체 아님).

**운영자 확인 필요:** (1) live 자격증명, (2) live `TTTC8001R` 실측 동작([확인 필요-1]),
(3) websocket 옵션 가용성([확인 필요-3], polling 채택 시 차단요소 아님).

#### ADR-045-2 — 미확인 체결은 위조 금지, expired로 수렴

live 체결조회가 비거나 오류면 주문을 `filled`로 **위조하지 않는다**(REQ-045-A2). SPEC-042
`order_resolver`의 윈도우 기반 `expired` 수렴에 맡기고, 매도 의도는 다음 사이클 재평가. 이는
SPEC-042의 "never fabricate a live fill" 불변(REQ-042-A5)을 그대로 계승한다.

#### ADR-045-3 — 마이그레이션 불요(잠정)

orders 상태 enum은 mig 031에서 `expired`를 포함. live 체결조회는 기존 `filled`/`partial`/`expired`
상태만 사용 → 새 컬럼/마이그레이션 불필요로 보임. run 단계에서 최종 확정(필요 시에만 mig 030 예약
번호 사용 — 단 SPEC-040/044가 mig 030을 예약 중이므로 충돌 회피 위해 신규 번호는 run에서 확정).

## 구현 대상 파일 (kis/ 한정)

- `src/trading/kis/broker_truth.py` — `confirm_fills()` live 분기 구현(seam 채우기).
  `BrokerFillInquiryNotImplemented` raise를 실제 체결조회 호출로 교체.
- `src/trading/kis/` 신규 모듈(예: `daily_ccld.py` 또는 `broker_truth.py` 내 함수) — live
  체결조회 호출 + 응답 파싱 + 주문 상태 매핑. `client.get()` 경유(SPEC-043 페이서 자동 적용).
- 테스트: `tests/.../test_live_fill_inquiry*.py`, `test_spec042_6_8_reproduction*.py`(또는 기존
  SPEC-042 테스트 보강), `test_live_smoke_gate*.py`.

**수정 금지(SPEC-044 소유):** config.py, backtest/*, edge/*, pyproject.toml, 일일리포트 배선.
**미변경(불변 보존):** order.py(submit_order), fills.py(paper reconcile), sell_lock.py, account.py.

## 마일스톤 (Milestones — 우선순위 기반, 시간 추정 없음)

### M1 (Priority High) — 6/8 재현 회귀 테스트 [HARD, 선행]
- REQ-045-B1/B2/B3. 체결확인 실패 주입 → SELL이 submitted 영구정체하지 않고 expired 수렴 +
  매도 의도 미소실을 **실패하는 테스트로 먼저 고정**. fake clock 결정론.
- 완료 기준: 수정 전 테스트가 실패를 보임(재현 확인) → 이후 M2 구현으로 통과.

### M2 (Priority High) — live 체결조회 seam 구현
- REQ-045-A1~A5. `confirm_fills()` live 분기를 `inquire-daily-ccld`(가정 A-1) 호출로 구현.
  `client.get()` 경유(SPEC-043 페이서). 미확인 시 위조 금지·expired 위임(ADR-045-2).
- 완료 기준: M1 재현 테스트 통과. live 분기가 raise 대신 실제 조회 수행. paper 경로 byte-불변.

### M3 (Priority Medium) — 라이브 조건 멱등성/이중매도 검증
- REQ-045-D1/D2/D3. sell_lock submitted leg가 live 체결확인으로 자동 해제됨을 테스트로 보장.
  중복 KIS 매도 0, 멱등 재전이 금지.

### M4 (Priority Medium) — 소액 라이브 스모크 게이트 정의 + 절차
- REQ-045-C1~C4. bounded 최소수량 라이브 실행 검증 절차 문서화 + 게이트 판정 로직(관측 증거 체크).
  실행-only 명시, 미충족 시 승급 차단. SPEC-042 AC-5 보완.

### M5 (Priority Low) — [확인 필요] 운영자 실측 게이트
- [확인 필요-1/2/3]. 운영자 라이브 자격증명으로 live TR_ID·응답 스키마·(옵션)websocket 가용성 실측.
  M4 스모크의 첫 실제 체결이 최종 검증 게이트.

## 위험 (Risks)

- **R-1 (live TR_ID 미실측):** 가정 A-1이 live에서 빈 응답/다른 스키마일 위험. → [확인 필요-1/2]
  운영자 실측 게이트로 차단. 미확인 시 위조 금지(ADR-045-2)로 자본 안전은 보존.
- **R-2 (TPS 위반):** 추가 KIS 호출이 SPEC-043 예산 초과 위험. → `client.get()` 경유 강제,
  호출을 "매도 사이클 전 + 주문 후"로 한정. 통제되지 않은 호출 금지(REQ-045-A3).
- **R-3 (paper 경로 회귀):** live 구현이 paper balance-reconcile를 깨뜨릴 위험. → paper 분기
  byte-불변 유지(REQ-045-A5), 회귀 테스트로 고정.
- **R-4 (라이브 스모크 자본 손실):** 최소수량(1주)로 bounded. 실행 검증이 목적이므로 알파 무관.
- **R-5 (SPEC-044 파일 충돌):** 동시 구현 중. → kis/ 한정 + 소유 파일 미접촉으로 회피. 전용
  브랜치 사용 권장.

## 품질 게이트

- pytest 커버리지 ≥ 85%. money/risk 경로는 **reproduction-first**(M1 선행).
- ruff/black 통과. EARS 추적성(spec ↔ acceptance) 유지.
- LIVE 임박: 모듈 C 스모크 게이트 미통과 시 전면 라이브 승급 **금지**(하드 게이트).
- 정직성: [확인 필요] 항목은 운영자 실측 전 단언 금지.
