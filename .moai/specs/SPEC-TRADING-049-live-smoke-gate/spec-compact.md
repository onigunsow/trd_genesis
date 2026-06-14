# SPEC-TRADING-049 (compact) — 라이브 스모크 게이트 (REQ-045-C 구현)

- id: SPEC-TRADING-049 | v0.2.0 | status: draft | priority: high | mode: tdd | brownfield([DELTA])
- mig: 최신 033(SPEC-048) → 본 SPEC 신규 **034**(필요 시; 기록 위치 OQ-3와 함께 run에서 확정)

## WHY
SPEC-045 REQ-045-C(bounded live round-trip 검증 게이트) = **ACCIDENTAL-MISSING**.
live 체결조회 seam(`confirm_fills` execution_inquiry, `_inquire_daily_ccld`, `_apply_live_fills`)은
SPEC-045 M1/M2로 **이미 구현됨**. 부재한 것은 "실거래 전환 직전 1회 소액 round-trip으로 실 체결·
원장 일치를 측정 증거로 확인하고, 통과 못 하면 live 승격을 차단"하는 **운영 게이트/CLI/런북**.

## SCOPE
1. CLI `trading smoke-gate`(기존 `cmd==` 디스패치 + `_cmd_*` 규약): live 1회 bounded BUY→SELL(상한 주입).
2. 증거 5항목 판정(주입형 순수함수): (a)BUY확정 (b)SELL확정 (c)원장정합 (d)stuck submitted 0 (e)live TR_ID/필드 실검증.
3. PASS/FAIL + 영구 기록(증거 스냅샷) + FAIL 시 live 승격 차단(하드 게이트).
4. 멱등·안전: broker-truth·sell_lock·order_resolver 재사용, 미체결 자동정리, phantom/이중주문 없음.

## EARS (4 모듈)
- **M1 CLI**: M1-1 `smoke-gate` 서브커맨드 / M1-2 수량·금액 상한 강제 / M1-3 PAPER·무자격증명 거부 / M1-4 "실행 검증, 전략 검증 아님" 고지.
- **M2 판정·차단**: M2-1 증거 5항목 수집·판정 / M2-2 주입형 순수함수 / M2-3 1항목 미충족→FAIL·차단·보고 / M2-4 영구 기록(FAIL→PASS 덮어쓰기 금지) / M2-5 스모크 PASS 없으면 live 전면 승격 선행 차단.
- **M3 멱등·안전**: M3-1 단일 BUY/SELL(sell_lock 재사용) / M3-2 미체결 자동정리(order_resolver expired, 위조금지) / M3-3 SPEC-043 TPS 페이서 경유 / M3-4 판정·기록 멱등.
- **NFR**: NFR-1 회귀 0(paper 불변) / NFR-2 TDD·순수함수 단위테스트·CI mock(실거래 미발주) / NFR-3 mig 034 또는 불요 확정·conftest 호환.

## INTEGRATION (검증된 실제 위치)
- `kis/broker_truth.py`: `confirm_fills()` L506, `_inquire_daily_ccld()` L234(TTTC8001R/CTSC9115R, [확인필요-1/2] L249~), `_apply_live_fills()` L315(ODNO/CCLD_QTY/CCLD_AVG_UNPR), `BrokerFillInquiryNotImplemented` L75, `clamp_sell_to_confirmed()` L113, `intraday_reconcile()` L167.
- `kis/order_resolver.py`: `resolve_stuck_orders()` L107(15분 윈도). `kis/sell_lock.py`: `guard_sell()` L197/`set_sell_inflight()` L140.
- `kis/order.py`: `submit_order()` L224, `_check_live_gate()` L33(live_unlocked, REQ-MODE-02-6).
- `edge/realized_pnl.py` `aggregate_realized_pnl_cum()` L98 / `edge/roundtrips.py` `build_roundtrips()` L127 (읽기만).
- `cli.py` `main()` L84(`cmd,rest=args[0],args[1:]`), 참고 `_cmd_resolve_orders` L306/`_cmd_aggregate_pnl` L380.
- `config.py` `TradingMode.LIVE/PAPER` L24, `get_settings()`. `tests/conftest.py` fake_cursor/fake_conn/patch_db_connection.

## EXCLUSIONS [HARD]
1. CI 실거래 발주 금지(live POST/inquiry mock만). 2. 전략/알파 검증 제외(SPEC-044/046/048 소관).
3. 새 신호/전략 금지. 4. live seam 재구현 금지(호출만). 5. live_unlocked 의미 변경 금지(상위 선행검사만).
6. SPEC-044 소유 파일(config 엣지상수·backtest·edge 산식·pyproject) 수정 금지. 7. 자격증명 회전 제외. 8. websocket 제외(polling만).

## RELATED
045(출처: REQ-045-C/모듈C) · 042(broker-truth, AC-5 보완) · 043(TPS 존중) · 029(paper 미변경) ·
048(M2 전략엣지 게이트=별개; 실거래 확대는 둘 다 만족) · 044(경계분리).

## [확인 필요] (운영자 1회 라이브 실행이 해소 절차)
1. live TR_ID 실측(TTTC8001R/CTSC9115R). 2. live output 필드명 호환(ODNO/CCLD_QTY/CCLD_AVG_UNPR). 3. 증거 영구 기록 위치(system_state/audit_log/신규테이블 mig034).
