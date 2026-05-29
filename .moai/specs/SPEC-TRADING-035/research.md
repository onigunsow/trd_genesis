# SPEC-TRADING-035 — Research (코드베이스 디스커버리)

> Regime Awareness — Core Loop (SPEC-016 Phase 2 scoped) 구현 전 사전 조사.
> 모든 라인 번호는 2026-05-29 기준 `fix/SPEC-TRADING-026-overheating-softening` 브랜치(HEAD 1a79ce1).

---

## 1. 현재 regime 흐름 — "산출은 되지만 아무도 읽지 않는다"

### 1.1 Macro 페르소나는 이미 regime/risk_appetite 를 emit 한다

- `src/trading/personas/prompts/macro.jinja` 의 출력 스키마(라인 14~33)는 이미
  `"regime": "bull|neutral|bear"`(라인 16), `"risk_appetite": "risk-on|neutral|risk-off"`(라인 17)
  을 **JSON 키로 명시**한다. 즉 LLM 은 매 매크로 실행마다 이 두 값을 생성하고 있다.
- 이 값들은 `persona_runs.response_json` 에만 저장된다. 구조화된 컬럼이 없다.

### 1.2 유일한 소비처는 "텍스트 요약" 뿐 — 분기 로직 0건

- `src/trading/personas/orchestrator.py:241-246` (`_summarize_persona("macro", ...)`)가
  `response_json.get("regime","?")` / `response_json.get("risk_appetite","?")` 를 읽어
  500자 텍스트 브리핑으로 만든다.
- `run_pre_market_cycle` 등은 `macro_persona.latest_cached(max_age_days=7)`(orchestrator.py:785)
  로 캐시를 읽고, `macro_summary = (cached_macro["response"] or "")[:500]`(orchestrator.py:789)
  로 **500자 blob 텍스트**만 Decision/Risk 프롬프트에 흘려보낸다.
- `decision.jinja`(라인 118~119)는 `{{ macro_guide }}` 라는 자유 텍스트로 받고,
  `risk.jinja`(라인 81)는 `{{ macro_summary }}` 자유 텍스트로 받는다.
- 즉 `if regime == "bull"` / `while regime == "bear"` 류의 **구조화된 분기 로직은 코드 어디에도
  존재하지 않는다.** (grep 검증: `current_regime`/`regime_at_decision`/`regime_branch_applied` 0건.)

> 결론: REQ-035-1 은 "신규 파서"가 아니라, **이미 emit 되는 두 키를 DB 컬럼으로 승격 + 조회 경로 신설**.
> REQ-035-2/4 는 그 컬럼을 읽어 분기.

---

## 2. 마이그레이션 시스템 (alembic 아님 — raw SQL 순차 러너)

- 위치: `src/trading/db/migrations/NNN_*.sql`. 최신 적용본은 `023_halt_notify_cooldown.sql`.
  **다음 번호는 `024_`.**
- 러너: `src/trading/db/migrate.py`.
  - `pending()`(라인 37~41): `migrations_dir().glob("[0-9][0-9][0-9]_*.sql")` 를 파일명 정렬로
    스캔하고, `schema_migrations` 에 없는 것만 적용 → **파일을 떨어뜨리면 자동 발견된다.**
  - `apply_one()`(라인 44~49): 파일 전체를 한 트랜잭션으로 실행. **파일 스스로
    `schema_migrations` 에 자기 버전을 INSERT 해야 한다**(러너가 대신 안 함).
- 멱등 하우스 스타일(`023`/`013` 본보기): `DO $$ ... information_schema.columns 가드 ... ALTER TABLE
  ADD COLUMN ... $$;` 후 `INSERT INTO schema_migrations (version) VALUES (...) ON CONFLICT DO
  NOTHING;` + `INSERT INTO audit_log (...) VALUES ('SCHEMA_MIGRATED', ...)`.
- 001 은 docker `docker-entrypoint-initdb.d` 로 빈 볼륨 초기화 시 자동 적용. 나머지는
  `trading migrate` 수동 실행.

### 2.1 system_state 는 단일 행(id=1) 테이블 — 신규 테이블 금지

- `001_system_state.sql:7-16`: `system_state (id SMALLINT PK DEFAULT 1 CHECK (id=1), live_unlocked,
  halt_state, silent_mode, trading_mode CHECK IN ('paper','live'), updated_at, updated_by)`.
- `023` 이 같은 테이블에 `halt_notified_at TIMESTAMPTZ` 를 ADD COLUMN 한 선례 → **regime 컬럼도
  동일 패턴으로 system_state 확장**(SPEC-016 Q-5 결정: 신규 `macro_state_cache` 테이블 대신 단일 행 확장).
- 헬퍼: `src/trading/db/session.py`
  - `get_system_state()`(라인 61~71): `SELECT * FROM system_state WHERE id=1` → dict.
    `SELECT *` 라서 **컬럼 추가에 자동 대응**(코드 수정 없이 신규 컬럼이 dict 키로 노출됨).
  - `update_system_state(**fields)`(라인 74~89): 지정 필드만 UPDATE, `updated_at=NOW()` 자동.
    `current_regime=...` 같은 신규 컬럼도 그대로 쓸 수 있다. **단, `regime_updated_at=NOW()` 처럼
    함수 호출이 필요한 필드는 현재 `updated_at` 한 가지만 특수 처리** — regime 갱신 시 별도 처리 필요
    (설계 노트: 헬퍼에 `regime_updated_at` 마커를 추가하거나 raw SQL 1회 UPDATE).

---

## 3. CLI 패턴 — 비용 0 의 정식 경로 (반드시 준수)

- `src/trading/personas/base.py`:
  - `is_cli_mode_active() -> bool` (라인 747).
  - `call_persona_via_cli(...)` (라인 555) — Claude CLI 브릿지(유료 API 미사용).
  - `call_persona(...)` (라인 210) — 유료 API. cli_only_mode 에서는 `block_if_cli_only_mode`
    가드(라인 84/120 부근)가 작동하여 crash 가능 → **bare 호출 금지.**
- 모든 페르소나의 정식 분기: `if is_cli_mode_active(): return call_persona_via_cli(...)` else
  `call_persona(...)`. 예: `macro.py:65-88`, portfolio/decision 도 동일.
- `macro.run(...)`(`src/trading/personas/macro.py:38-88`)은 이미 이 분기를 갖는다 →
  **데일리 06:00 매크로는 `orchestrator.run_weekly_macro` 의 기존 CLI 경로를 그대로 재사용**하면
  추가 비용 0. (run_weekly_macro 는 `macro_persona.run(...)` 을 호출, orchestrator.py:1555.)

---

## 4. 스케줄러 — cron 상수는 Python 코드 (scheduler.yaml 런타임 미적재)

- `src/trading/scheduler/runner.py`:
  - 매크로 **페르소나**: 금요일 17:00 KST 만. `sched.add_job(lambda: _wrap("weekly_macro",
    orchestrator.run_weekly_macro), CronTrigger(day_of_week="fri", hour=17, minute=0, ...),
    id="weekly_macro", ...)` (라인 345~350).
  - 매크로 **데이터 스크립트**(`build_macro_context.main`)는 **매일 06:00** 실행
    (라인 178~183, `CronTrigger(hour=6, minute=0, ...)`, id="ctx_macro"). 데이터는 데일리지만
    **regime 을 생산하는 페르소나는 주 1회** → regime staleness 의 근본 원인.
- 모든 스케줄은 위처럼 **Python 코드의 CronTrigger 상수**다. `scheduler.yaml` 은 런타임에
  로드되지 않는다(코드가 진실의 원천) → 데일리 06:00 매크로 페르소나 잡도 `add_job` 1개로 추가.

> 결론: REQ-035-3 = `add_job(... run_weekly_macro(또는 동등 데일리 메서드) ..., CronTrigger(hour=6,
> minute=0))` 1개 추가. 금요일 17:00 주간 잡은 **유지**(중복 무해, 캐시 갱신). impact-5 뉴스 트리거
> (REQ-016-2-3 b)는 **명시적 defer**.

---

## 5. A/B 통합 지점 (이미 배포됨) — 그리고 C 가 건드리지 않는 것

- **A = SPEC-033 (자동 손절/익절 워치독, `*/5`)**: ATR 동적 임계는 **변동성 레짐**
  (`src/trading/strategy/volatility/thresholds.py` 의 low/normal/high/extreme)을 사용한다.
  이는 매크로 regime(bull/neutral/bear)과 **별개의 축**이다. → C 는 stop-loss 강도를 macro regime 에
  연결하지 않는다(과매도 방지).
- **B = SPEC-034 (포트폴리오 페르소나 사이클 연결)**: `src/trading/personas/portfolio_gate.py`
  의 `_apply_portfolio_adjustment(...)`(라인 89~)가 decision→portfolio→risk→execute 사이에
  buy-only 조정을 삽입. portfolio.run 입력에 이미 `holdings/holdings_count/total_assets/cash_pct`
  (라인 134~141)를 넘긴다. portfolio.run 은 CLI 패턴 준수(REQ-034-9).
  → C 의 REQ-035-4 는 이 입력에 `current_regime` 을 추가하고, 현금 가이드를 regime 으로 시프트.
- **portfolio.jinja 의 현금 가이드**: 현재 `portfolio.jinja`(38줄)에는 섹터 편중/상관관계만 있고
  명시적 "30~50% 현금" 라인은 **없다**(decision.jinja:12 에 30~50% 가 있음). → REQ-035-4 는
  portfolio.jinja 에 regime-aware 현금 타깃 라인을 **신규로** 주입한다(또는 portfolio_gate 의
  입력 컨텍스트로 주입).

---

## 6. 현재 페르소나 임계값 (하드코딩 텍스트)

- `decision.jinja`:
  - 현금 비중 가이드 30~50% (라인 12), confidence 0.7 부분매수 기준 (라인 19),
    섹터 분산 40% 상한 (라인 15).
  - 위험 한도(라인 131~136): 일일 손실 -1.0%, 종목당 20%, 전체 80%, 단일 주문 10%, 일일 10건.
- `risk.jinja`(라인 73~78): 동일 한도 5종(일일 -1.0%, 종목당 20%, 전체 80%, 단일 10%, 일일 10건).
- 이 값들은 **프롬프트 텍스트**다. Python 레벨 enforcement 는 `limits.py`(risk gate, SPEC-034 가
  최종 hard gate 로 유지)에 별도 존재.

> 설계 결정 필요(REQ-035-2): regime 조정값을 (a) 프롬프트 컨텍스트로 주입 vs (b) Python enforcement.
> 권고 = **(a) 프롬프트 컨텍스트 주입(이 시스템의 단일 턴 LLM 패턴과 일관, SPEC-016 A-5)** +
> **하드 현금 바닥은 Python 가드** 병행(LLM 이 무시 못 하도록).

---

## 7. 사용 가능한 데이터 vs 부재 데이터

- **사용 가능**: `flows` 테이블 — 외국인/기관/개인 순매수(pykrx). `context.py:261-276`
  (`_flows_5d`)가 `foreign_net`/`institution_net` 5일 누적을 조회. KOSPI/KOSDAQ OHLCV 도 캐시 존재
  (`context.py` `_latest_close("pykrx", ...)`).
- **부재(이 SPEC 비목표)**: 신용잔고(margin balance), 투자자예탁금(investor deposits),
  V-KOSPI(변동성 지수) 전용 fetcher 는 **없다**(REQ-016-2-4). 매크로 프롬프트 텍스트(macro.jinja:11)
  는 "신용잔고/거래대금"을 언급하지만 구조화 fetcher 는 없음.
- **regime 결정 방식(SPEC-016 Q2)**: 기계적 임계 계산이 아니라 **Macro LLM 의 regime 출력을 신뢰**
  (LLM 이 macro 컨텍스트 전반을 본다). 신규 fetcher 는 향후 SPEC 으로 defer.

---

## 8. 테스트/품질 환경

- 테스트 실행: `.venv/bin/python -m pytest` (docker 이미지에 pytest 없음).
- 베이스라인: **801 passed / 6 pre-existing fail**(web_scraper ×1, volatility ×2,
  tools/registry ×3). 신규 회귀 0 허용.
- Lint: ruff 가 BLE001 을 select 하지 **않음** → `# noqa: BLE001` 쓰면 RUF100 발생. 평범한
  `except Exception:` 사용.
- 신규 코드 85%+ 커버리지(TRUST 5).

---

## 9. 영향 받는 파일 요약 (구현 시)

| 영역 | 파일 | 변경 성격 |
|---|---|---|
| 마이그레이션 | `src/trading/db/migrations/024_*.sql` (신규) | system_state + persona_runs 컬럼 ADD (멱등) |
| 마이그레이션 러너 | `src/trading/db/migrate.py` | 변경 불필요(자동 발견) |
| system_state 헬퍼 | `src/trading/db/session.py` | regime 읽기 헬퍼 + TTL 폴백 + `regime_updated_at=NOW()` 마커 |
| Macro 후처리 | `src/trading/personas/macro.py` / `orchestrator.run_weekly_macro` | 성공 응답에서 regime/risk_appetite 추출 → update_system_state; 스키마 가드 |
| Macro 프롬프트 | `src/trading/personas/prompts/macro.jinja` | regime/risk_appetite 필수 명시(주석/문구) |
| Decision/Risk | `decision.py`, `risk.py`, `decision.jinja`, `risk.jinja` | regime 읽기 + 보수 분기값 + 컨텍스트 라인 + regime_branch_applied |
| Portfolio | `portfolio.py` / `portfolio_gate.py` / `portfolio.jinja` | current_regime 입력 + 현금 타깃 시프트 |
| 스케줄러 | `src/trading/scheduler/runner.py` | 데일리 06:00 매크로 페르소나 잡 1개 추가 |
| 알림 | Telegram notifier | TTL 폴백 경고 + regime 모드 변경 알림 |
| 테스트(신규) | `tests/db/test_regime_cache.py`, `tests/personas/test_regime_branching.py`, `tests/scheduler/test_macro_frequency.py`, `tests/personas/test_portfolio_regime.py` | 단위/통합 |
