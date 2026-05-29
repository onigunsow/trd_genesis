# SPEC-TRADING-036 — Research (코드베이스 + 데이터 가용성 디스커버리)

> 불장 모드 + 천장 방어 + 한국 모멘텀 데이터 레이어 (SPEC-016 Phase 3 + REQ-016-2-4 번들) 구현 전 사전 조사.
> 모든 라인 번호는 2026-05-29 기준 `fix/SPEC-TRADING-026-overheating-softening` 브랜치(HEAD 1a79ce1).
> SPEC-035(Phase 2 core loop)가 오늘 배포되어 마이그레이션 024가 적용된 상태를 전제로 한다.

---

## 1. 검증된 데이터 가용성 (경험적 테스트 — 공식 keyed API 확정)

> 아래는 2차 정보(예: Gemini 제안)가 아니라 **직접 실행/조회**해 확인한 사실이다. 초안의 "KRX MDC/
> KOFIA HTML 스크래퍼" 가정은 **틀렸다** — 3종 모두 **공식 keyed API** 로 안정적으로 받을 수 있다.
> 따라서 SPEC-016 R-2(스크래핑 취약성)는 **대폭 하향**된다(취약한 HTML/OTP 경로 제거).

### 1.1 ✓ pykrx 로 사용 가능 (robust 신호 — 방어선의 바닥)

| 데이터 | pykrx 함수 | 비고 |
|---|---|---|
| KOSPI/KOSDAQ 지수 OHLCV | `get_index_ohlcv` (코드 `1001`=KOSPI / `2001`=KOSDAQ) | 지수 레벨, 일간 %, 5일 %, 52주 비교 도출 가능 |
| 투자자별 매매대금/순매수 | `get_market_trading_value_by_investor`, `get_market_net_purchases_of_equities` | 외국인/기관/개인 순매수. 이미 `flows` 테이블 경유로 사용 중(`pykrx_adapter.fetch_flows` `get_market_trading_value_by_date`, 라인 92~98) |

- 기존 `flows`(외국인/기관/개인 순매수) + KOSPI/KOSDAQ OHLCV 캐시는 SPEC-035 research §7 에서 확인됨
  (`context.py` `_flows_5d` / `_latest_close("pykrx", ...)`).
- **pykrx 로는 신용잔고/예탁금/V-KOSPI 를 받을 수 없다**(경험적 확인): Gemini 가 제안한
  `get_margin_trading_stock_of_market_by_date` 는 **존재하지 않는 함수**, `get_market_trading_volume_by_date`
  는 예탁금이 아니라 투자자 **거래량(VOLUME)**, 코드 `1001` 은 V-KOSPI 가 아니라 **KOSPI 지수**다.
  → 이 3종은 pykrx 가 아닌 아래 공식 API 로 받는다.

### 1.2 ✓ yfinance 로 사용 가능 (이미 fetch 중)

| 데이터 | 소스 | 비고 |
|---|---|---|
| VIX (미국 변동성) | yfinance `^VIX` | `yfinance_adapter.py` 존재, 글로벌 자산 흐름에 이미 사용. V-KOSPI 승인 전 **임시 변동성 프록시** 역할 |

### 1.3 ✓ ECOS (한국은행) — 신용융자 + 예탁금 (공식 keyed API, 이미 통합)

| 데이터 | 통계표 / item | 검증 라이브값 | 비고 |
|---|---|---|---|
| 신용융자 잔고 (빚투/margin) | **901Y056 "증시주변자금동향"** / item **S23E** (cycle=M, 단위=원) | **2026-04 = 35.7조원** (이미 >35조 moderate 임계) | SPEC-016 의 ~36조 reading 과 일치 |
| 투자자예탁금 (deposits) | **901Y056** / item **S23A** (cycle=M, 단위=원) | **2026-04 = 124.8조원** | — |

- **이미 통합됨**: `src/trading/data/ecos_adapter.py` (`ECOS_API_KEY` 존재, `fetch_series(stat_code,
  cycle, item, label, start, end)` 패턴 + `DEFAULT_SERIES` 튜플). 901Y056 S23E/S23A 2개 시리즈를
  `DEFAULT_SERIES`(또는 별도 호출)에 추가하고 기존 `macro_indicators` 테이블에 캐시하면 끝.
- **월 단위·~1개월 시차는 허용 가능**: 신용융자/예탁금은 **느린 구조적 후기 사이클 신호**(절대 레벨
  임계 35/40/140조, 일간 델타 아님)라 월별 granularity 로 충분하다.

### 1.4 ✓ KRX OpenAPI — V-KOSPI (공식 keyed API, 승인 대기)

| 데이터 | endpoint | 헤더/파라미터 | 비고 |
|---|---|---|---|
| V-KOSPI (코스피200 변동성지수) | `idx/drvprod_dd_trd` ("파생상품지수 시세정보") | base `https://data-dbg.krx.co.kr/svc/apis`, header `AUTH_KEY: <key>`, param `basDd=YYYYMMDD` | 응답에 모든 파생상품지수 나열 → VKOSPI row 필터 필요(§Q-2) |

- 키는 `.env` 에 이미 존재(`#openapi.krx.co.kr` 주석 + `api_key=...`).
- **현재 상태**: 모든 endpoint 가 HTTP **401 "Unauthorized API Call"** 반환 — 키는 발급됐으나
  KRX OpenAPI My Page 의 **per-API 이용신청+승인(~1일)** 이 아직 미완료. → V-KOSPI 는 승인 전까지
  graceful `(unavailable)`, **승인 후 코드 변경 없이 자동 활성화**.
- **다른 소스에는 V-KOSPI 가 없다**(확인): yfinance / FinanceDataReader(FDR) / Naver 모두 V-KOSPI
  미제공. KRX OpenAPI 파생상품지수가 유일한 공식 경로.

> ✅ **결론**: 초안의 "첫 리서치 과제 = MDC/KOFIA endpoint 사냥" 은 **RESOLVED**. 신용융자/예탁금 =
> ECOS 901Y056 S23E/S23A, V-KOSPI = KRX OpenAPI `idx/drvprod_dd_trd`. HTML/OTP 스크래핑 불필요.
> 남은 작업은 (1) ECOS 2개 시리즈 추가, (2) KRX OpenAPI 어댑터 작성(승인 시 자동 활성), (3) 승인 후
> VKOSPI row 식별자 확인.

---

## 2. SPEC-035 인프라 재사용 (오늘 배포됨 — 중복 구현 금지)

> SPEC-036 은 SPEC-035 가 깐 레일 위에 **얹는다**. 아래는 grep/read 로 확인한 실재 코드다.

### 2.1 regime 캐시 + 읽기 헬퍼 (마이그레이션 024 적용됨)

- `system_state.current_regime` / `current_risk_appetite` / `regime_updated_at` / `regime_source_run_id`
  컬럼 존재(마이그레이션 `024_regime_awareness.sql`). 단일 행(id=1) 확장.
- `src/trading/db/session.py:145` `get_effective_regime() -> tuple[str, str]`
  (`@MX:ANCHOR`, fan_in==3). 7일 TTL → `('neutral','neutral')` 안전 폴백 + 텔레그램 경고 1회.
  Decision(`decision.py:57`) / Risk(`risk.py:55`) / Portfolio gate(`portfolio_gate.py:45`)가 이미 호출.
- → REQ-036-2(불장 모드)는 **이 헬퍼를 그대로 읽는다.** 신규 regime 읽기 경로 만들지 않음.

### 2.2 regime 분기 테이블 — `regime_branch.py` (Phase 3 가 확장하는 모듈)

- `src/trading/personas/regime_branch.py` (순수 함수, DB/네트워크 없음):
  - `adjust_for_regime(regime) -> RegimeAdjustment` — **보수 분기**(bull: 현금바닥 30→**20%**, conf
    **−0.05**, 섹터 40→45; bear: conf +0.1, 섹터 40→35, leverage 차단). Phase 3 의 **공격 프로필
    (현금 10%, 1~2종 집중)은 SPEC-035 에서 명시적으로 defer** 됨(라인 8~10 주석).
  - `enforce_cash_floor(signals, cash_pct, regime)` — LLM 이 못 넘는 **하드 현금 바닥 가드**. sell/hold
    는 절대 건드리지 않음(SPEC-033/034 정합, 라인 124~126).
- → REQ-036-2 의 **불장 모드는 이 모듈에 "aggressive" 프로필을 추가**한다(또는 신규 bull_mode 모듈).
  SPEC-035 의 conservative 프로필을 깨지 않고, **paper-mode + NOT late_cycle** 게이트 하에서만 적용.
  `enforce_cash_floor` 패턴(하드 가드)을 불장 모드의 10~20% 현금 바닥에도 재사용.

### 2.3 매크로 데일리 실행 (SPEC-035 REQ-035-3)

- `src/trading/scheduler/runner.py:352~` — 평일 06:10 KST 매크로 페르소나 잡(신규) +
  금요일 17:00(기존, 라인 345~350) 유지. `run_weekly_macro` 의 CLI 경로 재사용(비용 0).
- → REQ-036-1(데이터 레이어)의 한국 모멘텀 fetch 는 06:00 `ctx_macro`(`build_macro_context.main`)
  잡이 흡수. REQ-036-3(방어)의 16:00 평가는 그 데이터를 읽는다.

---

## 3. 마이그레이션 시스템 (raw SQL 순차 러너 — 다음 번호 025)

- 위치: `src/trading/db/migrations/NNN_*.sql`. **최신 적용본 `024_regime_awareness.sql` → 다음은 `025_`.**
- 러너: `src/trading/db/migrate.py`. `migrations_dir().glob("[0-9][0-9][0-9]_*.sql")` 자동 발견,
  `schema_migrations` 에 없는 것만 적용. **파일 스스로** `schema_migrations` + `audit_log` 에 INSERT.
- 멱등 하우스 스타일: `DO $$ ... information_schema 가드 ... ALTER/CREATE ... $$;` (`023`/`024` 본보기).
- **자동 boot 미적용**: `docker exec trading-app trading migrate` 수동 실행(운영 단계 — 세션 lessons).
- `system_state` 는 단일 행(id=1, `001_system_state.sql:7-16`). `trading_mode CHECK IN ('paper','live')`
  컬럼 존재 → **paper-only 가드의 진실의 원천**(REQ-036-2 안전 게이트). `get_system_state()` 는
  `SELECT *` 라서 신규 컬럼 자동 노출.

> 결론: 025 = `late_cycle_events` 테이블 신규 CREATE + `system_state` 에 `late_cycle_defense_active` /
> `late_cycle_level` / `late_cycle_entered_at` 컬럼 ADD (멱등). 023/024 하우스 스타일 복제.

---

## 4. 스케줄러 — cron 상수는 Python 코드 (`runner.py`)

- `scheduler.yaml` 은 런타임 미적재(코드가 진실의 원천 — SPEC-035 research §4 확인).
- 관련 잡(`runner.py`):
  - `ctx_macro`(`build_macro_context.main`) — **매일 06:00**(라인 178~183). REQ-036-1 의 한국 모멘텀
    fetch 가 여기에 붙는다.
  - `daily_report`(`daily_report.generate_and_send`) — **평일 16:00**(라인 337~342).
  - `position_watchdog */5` — 평일 09~15(라인 326~334). REQ-036-3 의 severe 강제 매도가 참조할
    **direct-sell-bypass** 패턴 보유.
- → REQ-036-3 = `runner.py` 에 **late-cycle 평가 잡 1개 추가**(평일 16:00, `daily_report` 와 충돌 회피
  위해 16:00 정각 별도 잡으로 두거나 16:05 미세 조정 — 구현자 판단). 06:00 `ctx_macro` 잡은 변경하지
  않고 fetch 모듈만 build() 에 추가.

---

## 5. 매도 bypass 패턴 (SPEC-033 position_watchdog — severe 강제 deleverage 가 재사용)

- `src/trading/watchers/position_watchdog.py:210~219`: **Direct `kis_sell`** — orchestrator 사이클
  halt 게이트와 일일 주문수 사전 체크 한도를 **우회**(REQ-033-4). 위험 축소(exit)는 매수 게이트를
  타지 않는다. 이중 매도 가드(`_confirm_qty`, 라인 200~206) 보유.
- → REQ-036-3 의 severe 단계 강제 부분매도(30%)는 **이 동일한 direct-sell-bypass 패턴**을 따른다.
  buy gate 통과 불요(위험 축소 exit). `enforce_cash_floor`(buy 차단)와 방향이 반대인 강제 매도 경로.

---

## 6. macro_context 빌드 구조 (REQ-036-1 이 확장)

- `src/trading/contexts/build_macro_context.py:123` `build()` — 섹션 조립(`parts` 리스트):
  거시지표(FRED+ECOS) / 글로벌 자산(yfinance) / 한국 대형주 흐름(워치리스트). `guarded_build` 로 감싸
  실패 시 빌드 자체는 막지 않음(`main()`).
- → REQ-036-1 은 `## 한국 시장 모멘텀` 섹션을 `build()` 의 `parts` 에 추가. 신용융자/예탁금은
  `ecos_adapter.py`(901Y056 S23E/S23A 확장, monthly), V-KOSPI 는 신규 `krx_openapi.py`
  (`idx/drvprod_dd_trd`, keyed)가 채우되, 실패/stale/401 필드는 `(unavailable)` 마커.
  robust 필드(KOSPI 일간%/모멘텀/flows/VIX)는 pykrx/yfinance 로 항상 채움. 형식은 SPEC-016 S-2 따름.

---

## 7. paper/live 가드 위치 (REQ-036-2 안전 게이트)

- `system_state.trading_mode CHECK IN ('paper','live')`(`001_system_state.sql`). `config.py` 노출,
  `kis/order.py`·`kis/client.py`·`blocked_cache.py:43` 등이 사용.
- → 불장 모드의 공격적 파라미터(현금 10~20%, 1~2종 집중, 보유 4~10일, event-CAR |1.0%|, +10%pt 한도)는
  **`trading_mode == 'paper'` 일 때만** 적용. live 면 SPEC-035 의 conservative 분기로 폴백(별도 사용자
  승인 전까지). 이 paper-only 가드는 Python enforcement(하드)로 — LLM 프롬프트 신뢰 금지.

---

## 8. 테스트/품질 환경

- 테스트 실행: `.venv/bin/python -m pytest` (docker 이미지에 pytest 없음).
- 베이스라인(SPEC-035 배포 후): **853 passed / 6 pre-existing fail**(web_scraper ×1, volatility ×2,
  tools/registry ×3). 신규 회귀 0 허용.
- Lint: ruff 가 BLE001 을 select 하지 **않음** → `# noqa: BLE001` 금지(RUF100 유발). `except Exception:`.
- 신규 코드 85%+ 커버리지(TRUST 5). 모든 외부 fetcher 는 graceful 실패(`(unavailable)`), 빌드/사이클
  크래시 금지(R-2).

---

## 9. 영향 받는 파일 요약 (구현 시)

| 영역 | 파일 | 변경 성격 |
|---|---|---|
| 마이그레이션 | `src/trading/db/migrations/025_*.sql` (신규) | `late_cycle_events` 테이블 + `system_state` 3컬럼 ADD (멱등) |
| 데이터 fetcher (신용융자/예탁금) | `src/trading/data/ecos_adapter.py` (확장) | 901Y056 S23E/S23A 2개 시리즈 추가 → `macro_indicators` 캐시 (graceful) |
| 데이터 fetcher (V-KOSPI) | `src/trading/data/krx_openapi.py` (신규) | KRX OpenAPI `idx/drvprod_dd_trd` keyed fetch, 401 시 graceful `(unavailable)` (승인 후 자동 활성) |
| 데이터 컨텍스트 | `src/trading/contexts/build_macro_context.py` | `## 한국 시장 모멘텀` 섹션 추가 + `(unavailable)` 마커 |
| 방어 평가 | `src/trading/risk/late_cycle.py` (신규) | 5신호 → 단계 → 현금바닥/진입차단/강제deleverage |
| 불장 모드 | `src/trading/personas/regime_branch.py` (확장) 또는 신규 bull_mode | aggressive 프로필 (paper + NOT late_cycle 게이트) |
| 프롬프트 | `decision.jinja`, `risk.jinja` | bull 컨텍스트 라인 주입 (target_holdings 1~2, cash 10~20, CAR \|1.0%\|) |
| 스케줄러 | `src/trading/scheduler/runner.py` | 평일 16:00 late-cycle 평가 잡 1개 추가 |
| 매도 bypass | `src/trading/watchers/position_watchdog.py` (참조) | severe 강제 매도가 동일 패턴 재사용 |
| 알림 | Telegram notifier | BULL ON/OFF + LATE-CYCLE DEFENSE 전환 알림 |
| 테스트(신규) | `tests/data/test_ecos_market_funds.py`, `tests/data/test_krx_openapi.py`, `tests/risk/test_late_cycle.py`, `tests/personas/test_bull_mode.py`, `tests/scheduler/test_late_cycle_job.py` | 단위/통합 |
