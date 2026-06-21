# SPEC-TRADING-056 — 실 Postgres 통합테스트 레이어 (거짓그린 근절)

작성 2026-06-21 · 기준 b23fa5c · 범위: 테스트 인프라(프로덕션 코드 불변)

## 문제 (이번 세션이 3번 증명)
pytest 1853 passed였으나 **라이브 SQL 3건이 통과를 뚫고 프로덕션에서 터짐**:
`positions.mode 부재`(SPEC-042 수렴), `orders.order_type NOT NULL 누락`, 과거 `pd.persona 컬럼 부재`. 전부 **conftest `FakeCursor` mock이 실 스키마를 미검증**해서. 만성 패턴 → 매 배포마다 라이브 디버깅 사이클.

## 목표
SQL 무거운 모듈을 **실 Postgres(마이그레이션 적용된 진짜 스키마)** 에 실행하는 통합테스트 레이어. mock이 못 잡는 `column does not exist`·NOT NULL·CHECK·JOIN 오류를 CI/로컬에서 사전 차단.

## 설계
1. **마커**: `pyproject.toml [tool.pytest.ini_options]`에 `markers = ["integration: 실 Postgres 필요"]`. 기본 실행에 포함하되 DB 없으면 skip.
2. **`tests/integration/conftest.py`** — `migrated_db` 픽스처(session scope):
   - 테스트 DSN 결정: 환경의 POSTGRES_*/DATABASE_URL로 **유지보수 연결** → `CREATE DATABASE trading_test`(존재 시 drop 후 재생성). **절대 prod `trading` DB 미접촉**.
   - `trading_test`로 DATABASE_URL monkeypatch(또는 env override) → `trading.db.migrate.run()`로 **전 마이그레이션 적용**(001은 init script 의존이므로 명시 적용 보장).
   - Postgres 도달 불가(소켓 실패)면 `pytest.skip`(DB 없는 개발기 단위테스트 무영향).
   - teardown: 연결 종료(+선택 drop).
   - 헬퍼 `seed_minimal(conn)`: persona_runs 1 + persona_decisions 1 + orders 몇 행(buy filled·sell·synthetic·correction) + positions 몇 행 + system_state 보장 → JOIN·집계 경로 실행.
3. **`tests/integration/test_sql_schema.py`** — 거짓그린 표면 전수 실행, 각 함수가 **raise 없이** 결과 반환 단언(실 스키마):
   - `kis/ghost_convergence`: `orders_positions_divergence()`, `converge_ghost_buys(dry_run=True)` + **실 INSERT 경로**(seeded 유령 → 교정 SELL INSERT 성공 = order_type/NOT NULL 검증).
   - `kis/order_resolver.resolve_stuck_orders(dry_run=True)` + 실 expire UPDATE 경로.
   - `edge/roundtrips.load_fill_rows()`/`compute_roundtrips()`(_FILL_SQL + correction 컬럼).
   - `edge/realized_pnl` 집계(synthetic/correction 필터).
   - `dashboard/queries`: `fetch_holdings·fetch_portfolio·fetch_roundtrips·fetch_postmortem·fetch_confidence_analysis·fetch_pnl_daily·fetch_recent_orders`(인라인 SQL 전부).
   - `ops/resolver_health.evaluate_resolver_health()`(system_state read + stuck count).
4. **★메타검증(수용기준, 필수)**: 통합테스트가 **심은 스키마 버그를 잡는다**를 증명 — 예: `_positions_qty_by_ticker`에 `WHERE mode='paper'` 임시 재도입 시 통합테스트가 **FAIL**함을 1회 시연(증명 후 원복). 이게 거짓그린 근절의 진짜 보증.
5. **(선택) 최소 CI**: `.github/workflows/integration.yml` — postgres 서비스 + `pytest -m integration`. CI 부재 환경이라 후속 가능(우선은 로컬 trading_test 실행).

## 검증·완료
- 단위테스트(mock) 회귀 0 유지. 통합테스트 전부 green(실 trading_test).
- 메타검증: 심은 버그 → 통합테스트 RED 시연 → 원복 → GREEN.
- 프로덕션 코드 불변(테스트·conftest·pyproject만). 배포 불필요(테스트 레이어).

## 비고
- plan-auditor 대신 **메타검증**으로 보증(테스트 인프라엔 "심은 버그를 잡나"가 더 강한 증명).
- prod `trading` DB 절대 미접촉(별도 `trading_test`). 운영 영향 0.
