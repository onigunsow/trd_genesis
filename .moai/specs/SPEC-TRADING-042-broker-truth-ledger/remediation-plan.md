# SPEC-042 broker-truth-ledger — Remediation Plan (D3 + D1/D6 + D2)

작성: 2026-06-21 · 기준 origin/main: c3b3b29 · 범위: **paper-only, live(D5) 제외**

이 계획은 spec.md REMEDIATION 섹션(라인 145-175)의 정렬 순서(위험 낮은 순)를 RUN-ready로 구체화한다.
세 결함(D3 → D1/D6 → D2)을 하나의 remediation 단위로 구현·감사·배포한다. **모든 변경은 paper 경로
한정**이며 live 주문 경로(`order.py`/`fills.py`/`account.py`)는 byte-for-byte 불변, D5(live TR `TTTC8001R`
검증)는 운영자 실계좌 필요로 본 단위에서 제외한다.

---

## 근거 (코드 실측, 2026-06-21)

| 항목 | 실측 사실 | file:line |
|---|---|---|
| D3 | `resolve_stuck_orders(client, ...)` 완성. scheduler 미등록(CLI/smoke만). 윈도 900s. | `kis/order_resolver.py:107-204` |
| D3 패턴 | `position_watchdog` = `sched.add_job(lambda: _wrap("watcher_position_watchdog", ...poll), CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5", timezone=KST), ...)` | `scheduler/runner.py:407-415` |
| D3 컨텍스트 | 잡 내부에서 `KisClient(get_settings().trading_mode)` 지연 임포트(예: `_run_fill_sync`) | `scheduler/runner.py:70-90` |
| D1 발생원 | `_synthetic_fill`이 paper에서만 `UPDATE orders SET status='filled', synthetic=TRUE` (KIS 미확인 매수도 filled) | `kis/order.py:155-166`, paper gate `:109` |
| D6 FIFO | `_FILL_SQL`은 `status IN ('filled','partial')` 필터만 — **synthetic 미필터**(의도된 단일소스). 유령 매수는 `open_qty` 부풀림, 미래 실제 매도가 유령 lot에 FIFO 매칭 시 원가 오염. | `edge/roundtrips.py:218-235`, FIFO `:135-211` |
| D2 현황 | smoke_gate `ledger_parity`는 **주입형 bool**(실제 비교 없음). orders-agg vs positions 비교 부재. | `kis/smoke_gate.py:87,201-211` |
| 보유 진실원 | `fetch_holdings`는 이미 `positions`(KIS reconcile) 읽음(D1 표시계층 ddf29db 완료). | `dashboard/queries.py:217-261` |
| orders 스키마 | `synthetic BOOLEAN`(mig 029), `status CHECK`에 `expired` 포함(mig 031). mode/side/qty/fill_qty 존재. | `db/migrations/002,029,031` |
| audit 패턴 | `INSERT INTO audit_log (event_type, actor, details) VALUES (%s,%s,%s::jsonb)` | `kis/order_resolver.py:279-282` |

---

## M1 — D3: resolver 5분 주기 스케줄러 등록 (자본보존, 위험 최저)

**문제**: submitted 매도 락(sell_lock)이 resolver 실행 전엔 자가해소 안 됨 → 진짜 손절 영구 차단 가능(REQ-042-C2 위반). resolver는 완성됐으나 scheduler 미등록.

**변경**:
1. `scheduler/runner.py`에 `_run_resolver()` 추가 — `_run_fill_sync` 패턴 미러:
   ```python
   def _run_resolver() -> None:
       from trading.config import get_settings
       from trading.kis.client import KisClient
       from trading.kis.order_resolver import resolve_stuck_orders
       client = KisClient(get_settings().trading_mode)
       result = resolve_stuck_orders(client, dry_run=False)
       LOG.info("SPEC-042 resolver cron: %s", result)
   ```
2. 등록(`position_watchdog` 트리거 미러):
   ```python
   sched.add_job(
       lambda: _wrap("order_resolver", _run_resolver),
       CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5", timezone=KST),
       id="order_resolver",
       name="order_resolver */5 (09-15 KST)",
   )
   ```
   - `_wrap`가 `is_trading_day()` 가드 + 예외 격리 제공(기존). 모드 판정은 잡 내부 `get_settings().trading_mode`.
   - watchdog과 동일 `*/5`이나 독립·멱등·저비용. resolver는 윈도 900s 미만 주문은 건드리지 않음(정상 체결 조기 만료 없음).

**테스트** (`tests/scheduler/`):
- `test_order_resolver_cron.py`(신규, `test_position_watchdog_cron.py` 미러): 스케줄러에 `id="order_resolver"` 잡이 mon-fri/9-15/`*/5`로 등록됨을 단언.
- `_run_resolver`가 `resolve_stuck_orders`를 호출함(패치) 단언.

**관측 게이트(배포후)**: 다음 거래일 장중 audit_log에 `STUCK_ORDER_EXPIRED`/`ORDER_RESOLVED`/resolver cron INFO 관측. 현 submitted 잔존 주문(5건 이력)이 윈도 경과분 expired로 수렴.

---

## M2 — D1/D6: 유령 합성매수 append-only 교정 (CRITICAL, paper-only)

**문제**: KIS-확인 보유를 초과하는 synthetic filled 매수가 orders에 잔존(086790 13 vs 10; 000270/071050/064350 net>0·0held). 어느 모듈도 교정 안 함. → `open_qty` 부풀림 + 미래 실제 매도 FIFO 원가 오염(D6).

**설계 결정 (append-only 교정 SELL lot + correction 플래그)** — ★plan-auditor 중점 검증 대상:
- 과거 filled 행은 **절대 UPDATE/DELETE 금지**([HARD], edge/scorecard 파생물 보호).
- 유령 초과분을 닫기 위해 **교정 SELL 행을 추가**(append-only). `correction=TRUE`로 표식.
- `build_roundtrips`는 correction 매도를 **실현손익 미생성 원장정리**로 처리(FIFO lot은 pop하되 RoundTrip 미기록) → open_qty가 KIS 진실로 수렴하고, 미래 실제 매도는 잔존 실제 lot에 매칭. 거짓 실현손익 미발생.
- orders-net(매수−매도, 교정매도 포함)이 자연히 positions로 수렴 → D2 parity의 근거가 됨.

**대안 검토(기각 사유 기록)**:
- (B) 읽기시점 open_qty cap(orders 미기록): spec이 "append-only 교정 lot 영속"을 명시(원장 단일소스·감사성). 읽기시점 환상은 edge/scorecard 영속 산출과 불일치 → 기각.
- (C) 교정매도를 FIFO에 포함해 ~0 P&L 라운드트립 생성: 단일가로 다수 lot 정확상쇄 불가, 소액 거짓손익 잔존 → 기각.

**변경**:
1. **mig 038** `038_orders_correction.sql`: `ALTER TABLE orders ADD COLUMN correction BOOLEAN NOT NULL DEFAULT FALSE;`
2. **신규** `kis/ghost_convergence.py` — `converge_ghost_buys(client, *, dry_run=False) -> dict`:
   - paper-only 가드(`client.mode != PAPER` → no-op 요약 반환, audit 생략). live 안전.
   - orders에 등장하는 각 ticker별:
     - `orders_net = Σ(filled buy.fill_qty) − Σ(filled sell.fill_qty)` (paper, 교정매도 포함).
     - `kis_held = positions.qty`(없으면 0; KIS reconcile 진실).
     - `excess = orders_net − kis_held`. `excess > 0`이면 교정 SELL 1행 INSERT: side='sell', qty=fill_qty=excess, fill_price=positions.avg_cost(없으면 해당 ticker 오픈 lot VWAP), fee=0, status='filled', synthetic=TRUE, correction=TRUE, mode='paper', filled_at=now(), persona_decision_id=NULL.
     - audit `GHOST_BUY_CONVERGED` {ticker, orders_net_before, kis_held, excess, fill_price}.
   - 멱등: 재실행 시 excess=0 → 추가 INSERT 없음.
3. **`edge/roundtrips.py`**:
   - `_FILL_SQL` SELECT에 `COALESCE(o.correction, false) AS correction` 추가.
   - `build_roundtrips`: `side=='sell' and row.get('correction')`이면 FIFO lot을 pop(수량만큼)하되 `result.roundtrips`에 미추가·`unmatched_sells` 미기록(원장정리). 잔여 lot 부족 시 no-op.
4. **1회 수렴 실행**(배포후): `trading converge-ghost-buys`(신규 CLI, `cleanup_stuck_orders` 패턴) 또는 인라인 1회. dry_run 먼저 라이브 DB 대조 → 실행.

**테스트**:
- `tests/kis/test_ghost_convergence.py`(신규): excess>0 → 교정행 1개(필드 검증); paper-only(live no-op); 멱등(재실행 무추가); excess≤0 무동작.
- `tests/edge/test_roundtrips.py`: correction 매도가 lot pop·RoundTrip 미생성·realized P&L 불변; correction 미설정 매도는 종전대로 RoundTrip 생성.
- `tests/edge/test_realized_pnl.py`: 교정 후 realized_pnl_cum이 교정 전과 동일(교정은 손익 무영향).
- **★거짓그린 방지(메모리 교훈 — persona 컬럼 사고)**: `converge_ghost_buys`의 집계 SQL은 dict 직접주입이 아니라 **실 cursor/conn 더블로 SQL 실행 경로**를 타도록 테스트. 더불어 배포후 라이브 DB `dry_run` 대조를 필수 게이트로 둠.

---

## M3 — D2: orders-positions parity 게이트 (게이트 맹점 보강)

**문제**: AC-5("positions=KIS divergence 0")가 positions만 봐서 orders 드리프트 미감지. smoke_gate `ledger_parity`는 주입형 bool로 실제 비교 부재.

**변경**:
1. **신규** `orders_positions_divergence() -> dict` (위치: `kis/broker_truth.py` 또는 `ghost_convergence.py`):
   - 각 ticker: `orders_net`(M2 정의, 교정매도 포함) vs `positions.qty` 비교. 반환 `{ticker: {orders_net, positions_qty, diff}}` + `parity: bool`(모든 diff==0).
2. **smoke_gate 배선**: SmokeEvidence를 구성하는 CLI 러너(cli.py smoke-gate 경로)에서 `ledger_parity`를 위 함수 결과로 산출(주입 bool → 실측). `evaluate_smoke_evidence` 순수함수 시그니처 불변(주입 계약 유지).
3. **acceptance.md AC-5 갱신**: "positions==KIS divergence 0"에 "**그리고 orders-agg net == positions 보유(parity)**" 추가.

**테스트**:
- `tests/kis/test_orders_positions_parity.py`(신규): 정합 시 parity=True/diff=0; orders_net>positions 시 parity=False·해당 ticker diff>0; M2 교정 후 parity=True.
- smoke_gate 배선 테스트: ledger_parity가 실측 함수에서 옴(드리프트 주입 시 item 'c' FAIL).

---

## 통합 검증 & 배포 (M4)

1. **로컬**: `pytest`(전체) 회귀 0, 신규 테스트 통과. ruff clean.
2. **plan-auditor 감사** 통과(본 계획 — 특히 M2 correction 설계).
3. **마이그레이션**: `docker exec trading-app trading migrate`로 mig 038 라이브 적용.
4. **1회 수렴**: `converge-ghost-buys --dry-run`으로 라이브 DB 대조 → 실 실행. audit `GHOST_BUY_CONVERGED` 관측.
5. **재배포** + healthcheck.
6. **라이브 검증(거짓그린 방지)**: 신규+기존 대시보드 엔드포인트 전수 curl(scorecard/roundtrips/portfolio/pnl-daily/positions), `orders_positions_divergence` parity==0 확인, Playwright 포지션뷰 렌더. 거래완료/실현손익이 교정으로 변동 없음 확인.
7. **관측 게이트**: 다음 거래일 장중 resolver cron 동작(STUCK_ORDER_EXPIRED) + parity 0 유지.

## live 안전 판정
- 본 단위 완료 후에도 **D5(live TR `TTTC8001R` 검증) 미완 → live 전환 불가**. D3(손절차단 해소)·D1/D2(드리프트·맹점 해소)는 닫히나, live 체결확인 seam은 운영자 실계좌 검증 전까지 가드 상태 유지(실패는 안전).
- 변경 전부 paper 경로. live 주문/체결 경로 불변. correction 행은 paper·synthetic으로 명시.

---

## 감사 반영 (plan-auditor 0.68 FAIL → 필수 수정) — 2026-06-21

아키텍처는 통과. 단 RUN 전 아래 반영 필수. **핵심 계약: `build_roundtrips`에 들어가는 모든 fill-row dict는 반드시 `correction` 키를 포함한다(단일 FIFO 초크포인트).**

### C1 [CRITICAL/blocking] — 분산 인라인 SQL이 correction 우회 (거짓그린 실패모드)
`build_roundtrips`를 직접 호출하는 소비자가 `_FILL_SQL` 외에 **둘 더 존재**: `dashboard/queries.py:644-670` `fetch_confidence_analysis`, `dashboard/queries.py:882-895` `fetch_calibration` — 각자 인라인 `fill_sql`(`status IN ('filled','partial')`)을 짜서 correction 미선택. → 패치 후 이 행들은 `row.get('correction')=None`(falsy)으로 **정상 매도 취급 → 가짜 RoundTrip 생성** → `/api/confidence`·`/api/calibration` 오염. M2 dict-주입 단위테스트는 통과하나 실 엔드포인트는 깨짐 = `pd.persona` 사고 재현.
- **수정**: 세 SQL 소스 전부에 `COALESCE(o.correction,false) AS correction` 추가(`_FILL_SQL`, `queries.py:644`, `queries.py:882`). **회귀 테스트는 `fetch_confidence_analysis`/`fetch_calibration` 엔드포인트 경로(실 SQL)로 correction 매도 → RoundTrip 0건 단언**(dict 주입 금지).

### M1 [MAJOR] — D2 전제 정정: `ledger_parity`는 주입 bool 아님
실제로 `cli.py:732`에서 `intraday_reconcile` drift로 산출(`cli.py:726-732`). 단 **positions-vs-KIS drift만** 보고 orders-agg 미감지가 진짜 갭.
- **배선**: `cli.py:732` `ledger_parity = (drift == 0 and errors == 0)` → `... and orders_parity`(`orders_parity = orders_positions_divergence()["parity"]`).

### M2 [MAJOR] — FIFO 원가 주장 완화
correction은 **수량(open_qty)만** KIS로 수렴. 잔존 lot 원가는 FIFO 산물이라 `positions.avg_cost`와 불일치하나 paper 합성가 기존 부정확성과 동일(악화/개선 없음). 한계로 명시.

### M3 [MAJOR] — correction이 합성 매도 카운터 오염
`realized_pnl._count_synthetic_sell_fills`(`realized_pnl.py:77-87`)에 `AND COALESCE(correction,false)=FALSE` 추가 + correction이 `synthetic_sell_fills` 미증가 테스트.

### m1 [MINOR] — daily_report 수렴일 오계상
`daily_report.py:174-210` 당일 orders 직접 집계. → **수렴을 장 휴장일(오늘 일요일) 실행**(권장) 또는 `sql_orders`/`sql_cost` correction 제외. 검증 체크리스트 추가.

### m4 [MINOR] — `orders_net` status 필터 고정
`orders_positions_divergence`의 `orders_net`은 `status IN ('filled','partial')` (M2/`_FILL_SQL` 동일). 수렴후 parity==True 단언.

### m3 [MINOR] — */5 동시발화
resolver를 `minute="2-59/5"`로 오프셋해 watchdog와 desync(KIS TPS 완충).

### m2 [MINOR/선택] — correction 행 대시보드 라벨(후속 허용)
`queries.py:177-186` `fetch_recent_orders` correction 무라벨 — 응답 플래그/활동피드 제외(스코프 최소면 후속).
