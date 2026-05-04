---
id: SPEC-TRADING-001
version: 0.2.0
status: draft
created: 2026-05-03
updated: 2026-05-04
author: onigunsow
priority: high
issue_number: 0
---

# SPEC-TRADING-001 — 한국 주식 5-페르소나 AI 매매 시스템 (M1~M5, 모의 자동 매매)

## HISTORY

| 일자 | 버전 | 변경 내용 | 작성자 |
|---|---|---|---|
| 2026-05-03 | 0.1.0 | 초안 작성. M1~M5 (모의 자동 매매) 범위 확정. M6 실거래 본 SPEC 범위 외 명시 | onigunsow |
| 2026-05-04 | 0.2.0 | M5 정밀화 후행 반영 + 자본 손실 직결 결함 보강 + 비용 모델 통합 + Robustness 6대 원칙 + 별도 SPEC 후보 명시 | onigunsow |

## 범위 요약

본 SPEC의 구현 범위는 **M1~M5 (모의 자동 매매)** 까지로 한정한다. M6(실거래 진입)은 본 SPEC에 포함되지 않으며, 3주 모의 운영 결과 평가 후 별도 SPEC(SPEC-TRADING-002 등)으로 작성한다. 본 SPEC에서 M6과 관련된 유일한 요구사항은 `live_unlocked` 안전장치(REQ-FUTURE-08, REQ-MODE-08-1) 뿐이다.

v0.2.0에서는 5 모듈 제약을 유지하되, **매매 비용 모델은 Module 5에 통합**하고 **로버스트 원칙은 모든 모듈에 cross-cutting 분산** 처리한다.

## 환경 (Environment)

- 호스트: Ubuntu 25 (Linux 6.17), Docker 28.3.2, Docker Compose v2.38.2
- 컨테이너: Python 3.13-slim + uv (app), postgres:16-alpine (db)
- 단일 사용자: 박세훈 (chat_id 60443392)
- 운영 디렉토리: `~/trading/` — 인접 시스템 `~/n8n/`의 운영 패턴 동일 적용
- 외부 인터페이스: KIS Developers (paper/live), Anthropic API (Opus 4.7 + Sonnet 4.6), Telegram Bot API, FRED, ECOS, OpenDART, pykrx, yfinance
- 거래소 시간: KRX 정규장 09:00~15:30 KST, 영업일 기준

## 가정 (Assumptions)

1. KIS Developers `paper` 엔드포인트가 안정적으로 가동되며 토큰 1분 재발급 제한이 유지된다
2. Anthropic API의 `claude-opus-4-7` (1M context)와 `claude-sonnet-4-6`이 SPEC 작성 시점 가용하다
3. pykrx, yfinance 등 비공식 라이브러리는 외부 호출 패턴이 본 SPEC 운영 기간 내 큰 변동 없다 — 단 KRX 페이지 변경 시 fundamentals/flows 어댑터가 깨질 위험은 plan.md 위험 분석에서 별도 강조
4. 박세훈 본인이 단일 사용자이며 자본 1,000만원 모의 운영 후 M6 별도 SPEC으로 진입 결정
5. 3주 모의 운영(영업일 ~15일)은 시스템 동작 검증엔 충분하나 페르소나 성과의 통계적 유의미성을 확보하기엔 표본이 부족함을 인지 (정성 지표 우선)

## Robustness Principles (6대 원칙, cross-cutting)

본 SPEC의 모든 모듈은 다음 6대 원칙을 준수한다. 각 원칙은 모듈별 REQ에 분산 반영된다.

1. **외부 의존성은 항상 실패한다고 가정** — KIS, Anthropic, Telegram, FRED, ECOS, DART, pykrx, yfinance 호출은 retry + circuit breaker + graceful degradation 필수
2. **상태 무결성은 트랜잭션으로 보장** — `orders + audit_log + positions` 같은 다중 INSERT/UPDATE는 단일 트랜잭션. 부분 성공 금지
3. **실패는 침묵하지 않는다** — 모든 시스템 에러(API 실패, DB 에러, 백업 실패)는 텔레그램 + audit_log. silent_mode에서도 시스템 에러는 발송
4. **자동 복구 후 인간 통보** — 1차 retry는 자동, 2차 실패는 인간 게이트(Telegram 알림 + audit)
5. **테스트로 명세를 굳힌다** — TRUST 5 ≥85%. 한도/회로차단/트랜잭션 모듈은 100%
6. **동시성 + 멱등성** — Postgres advisory lock + UNIQUE 제약으로 동시 다발 주문·중복 시그널 방어

## 요구사항 (Requirements) — EARS

EARS 표기 약식: **U**=Ubiquitous, **E**=Event-driven, **S**=State-driven, **O**=Optional, **N**=Unwanted (negative)

---

### Module 1 — Infrastructure & Security & Quality Gates (M1)

**REQ-INFRA-01-1 [U]** The system shall ship a Docker Compose stack with two services (`app`, `postgres`) under project name `trading`, isolated to network `trading-net` with no external port exposure beyond outbound HTTPS.

**REQ-INFRA-01-2 [U]** The system shall load all secrets exclusively from `~/trading/.env` with file permission `600`. The `.env` file shall be excluded from Git via `.gitignore`.

**REQ-INFRA-01-3 [E]** When `docker compose up -d` is executed, the system shall pass `pg_isready` healthcheck for postgres and a `python -m trading.healthcheck` healthcheck for app within 60 seconds.

**REQ-INFRA-01-4 [U]** The system shall provide `backup.sh` reproducing `~/n8n/backup.sh` pattern: timestamped directory, `pg_dump.gz`, `tar.gz`, retention of 30 most-recent backups (configurable via `BACKUP_KEEP`).

**REQ-INFRA-01-5 [U]** The system shall separate paper and live KIS configurations via discrete environment variables (`KIS_PAPER_*`, `KIS_LIVE_*`), selected at runtime by `TRADING_MODE` (default `paper`).

**REQ-INFRA-01-6 [N]** The container shall not run as root. Dockerfile shall declare `USER 1000:1000`.

**REQ-INFRA-01-7 [N]** Postgres shall not bind to a host port. Access is only via `docker compose exec postgres psql` or in-network application client.

**REQ-MODE-01-8 [S]** While the database is initialized for the first time, the system shall set `live_unlocked=false` in a `system_state` table.

**REQ-INFRA-01-9 [U, Robustness-5]** The system shall enforce a `gitleaks` pre-commit hook blocking any commit containing patterns matching `KIS_*`, `ANTHROPIC_API_KEY`, `KRX_*`, or `TELEGRAM_*` secrets.

**REQ-INFRA-01-10 [U, Robustness-5]** The system shall maintain test coverage ≥ 85% (TRUST 5). Modules implementing risk limits, circuit breaker, and DB transactions shall maintain 100% coverage. CI shall enforce ruff + mypy strict + pytest with coverage gate.

**REQ-INFRA-01-11 [N, Robustness-3]** Operators verifying secrets shall NOT print `.env` plaintext under any circumstance. Verification shall use the masked pattern `[ -n "$VAR" ] && echo "VAR: present"`. (Driven by 2026-05-04 KRX_PW exposure incident — see plan.md lessons learned.)

---

### Module 2 — KIS API Integration & Order Audit & Trade Safety (M2)

**REQ-KIS-02-1 [U]** The system shall implement KIS REST integration directly (no third-party SDK) covering: OAuth2 token issuance (`/oauth2/tokenP`), market data (current price, daily candles), account (balance, buyable amount), and orders (buy, sell, modify, cancel).

**REQ-KIS-02-2 [E]** When a KIS access token is requested within 60 seconds of the prior request, the system shall return the cached token instead of issuing a new one. Cache location: `data/.kis_token_cache.json` or DB.

**REQ-KIS-02-3 [U]** The system shall persist DB schema v1 with tables `orders`, `positions`, `audit_log`, and `system_state` (containing `live_unlocked`, `halt_state`, `silent_mode`, `trading_mode_active`).

**REQ-KIS-02-4 [E]** When any order is submitted to KIS, the system shall persist a row to `orders` (request payload, response payload, status, ts) AND insert an `audit_log` entry AND emit a Telegram briefing within 5 seconds (see REQ-BRIEF-04-3).

**REQ-MODE-02-5 [S]** While `TRADING_MODE=paper`, the system shall use `openapivts.koreainvestment.com:29443` and `tr_id` prefix `V`. While `TRADING_MODE=live`, the system shall use `openapi.koreainvestment.com:9443` and `tr_id` prefix `T`.

**REQ-MODE-02-6 [N, S]** While `TRADING_MODE=live` AND `live_unlocked=false`, the system shall NOT execute any order. Order submission attempts shall be rejected with explicit reason `LIVE_LOCKED` and recorded in `audit_log`.

**REQ-MODE-02-7 [E]** When `TRADING_MODE` is changed (paper↔live) at runtime, the system shall write an `audit_log` entry with old/new mode, operator identity, and timestamp.

**REQ-KIS-02-8 [N]** Persona system prompts and Anthropic API requests shall not contain KIS credentials, account numbers, or any secret material.

**REQ-KIS-02-9 [U, Robustness-1, Robustness-4]** KIS REST calls shall implement retry (max 4 attempts) with exponential backoff and a circuit breaker. On `rt_cd=1` or `EGW00201` responses the system shall retry; on 5xx responses the system shall fail-fast, write `audit_log`, and emit a Telegram alert.

**REQ-KIS-02-10 [U, Robustness-2]** Order flows shall execute as a single DB transaction guaranteeing `INSERT orders` + `INSERT audit_log` + `UPDATE positions` atomicity. Partial success is forbidden. Postgres advisory locks shall serialize concurrent order submissions for the same ticker.

**REQ-KIS-02-11 [E]** When validating buyable cash for a new buy order, the system shall subtract KIS `nrcvb_buy_amt` (pending unfilled buy amount) from available cash so concurrent multiple orders cannot exceed buyable amount.

**REQ-KIS-02-12 [N]** The system shall automatically block any order targeting a ticker that is suspended (거래정지), under management designation (관리종목), classified as investment risk (투자위험), or has reached the daily price limit (상하한가 도달). Pre-validation uses KIS issue-info lookup; rejection writes `audit_log` and emits Telegram alert.

**REQ-KIS-02-13 [U, Robustness-6]** The `orders.kis_order_no` column shall carry a UNIQUE constraint. Idempotent enforcement: a duplicate signal arriving twice shall result in only one trade being executed.

---

### Module 3 — Market Data & Benchmark Backtesting (M3)

**REQ-DATA-03-1 [U]** The system shall provide adapter modules under `src/trading/data/` for: `pykrx_adapter`, `yfinance_adapter`, `fred_adapter`, `ecos_adapter`, `dart_adapter`, `news_adapter`. Each adapter shall expose a uniform `fetch(symbol, start, end)` interface and return validated Pydantic models.

**REQ-DATA-03-2 [U]** The system shall cache OHLCV and macro indicator results in Postgres tables `ohlcv`, `macro_indicators`, `disclosures` with idempotent upsert by `(source, symbol, ts)`.

**REQ-DATA-03-3 [E]** When an adapter is invoked for a date range already cached, the system shall return cached rows without re-fetching the external source.

**REQ-DATA-03-4 [U]** The system shall backfill OHLCV data from `2019-01-01` for KOSPI200, KOSDAQ150, and the user watchlist on first M3 execution.

**REQ-DATA-03-5 [U]** The system shall provide rule-based benchmark strategies in `src/trading/strategies/`: `sma_cross.py` (single-asset SMA crossover) and `dual_momentum.py` (dual momentum across watchlist).

**REQ-DATA-03-6 [E]** When a backtest is executed, the system shall produce CAGR, MDD, Sharpe ratio, and trade-by-trade ledger as the benchmark reference for later persona-system comparison.

**REQ-DATA-03-7 [N]** Backtests shall not be used to validate the persona system itself (lookahead bias risk). Backtests serve only as rule-based benchmark for forward comparison against paper-mode persona performance.

**REQ-DATA-03-8 [U, Robustness-1]** The system shall depend on pykrx ≥ 1.2.8 with `KRX_ID` and `KRX_PW` environment variables required for KRX-website-authenticated endpoints. When KRX login fails, OHLCV fetch (which does not require login) shall continue to operate while fundamentals and flows fetch shall degrade gracefully with a warning logged and Telegram alert emitted.

**REQ-DATA-03-9 [U]** For each watchlist ticker the system shall cache daily PER, PBR, EPS, BPS, DIV, DPS, market capitalization, and foreign/institutional/individual trading flows in `fundamentals` and `flows` tables. These values shall be auto-injected into Micro persona input.

**REQ-DATA-03-10 [U]** The system shall additionally cache five FRED series: reverse repo (`RRPONTSYD`), high-yield spread (`BAMLH0A0HYM2`), WTI (`DCOILWTICO`), financial stress index (`STLFSI4`), trade-weighted USD (`DTWEXBGS`). These values shall be integrated into Macro persona input.

---

### Module 4 — 5-Persona Intraday System & Telegram Briefing & Paper Auto-Trading (M4)

**REQ-PERSONA-04-1 [U]** The system shall implement six personas under `src/trading/personas/`: `Macro` (Opus 4.7), `Micro`, `Decision` (박세훈), `Risk`, `Portfolio` (M5+, gated by holdings ≥ 5), `Retrospective` (M5+). Each persona uses a Jinja2 system prompt template under `personas/prompts/`.

**REQ-PERSONA-04-2 [U, AUDIT]** Every persona invocation shall persist a row to `persona_runs` containing: `persona_name`, `model`, `prompt`, `response`, `input_tokens`, `output_tokens`, `cost_krw`, `latency_ms`, `timestamp`, `trigger_context`.

**REQ-PERSONA-04-3 [U]** The Decision persona shall additionally persist its signal to `persona_decisions` (ticker, direction, qty, rationale, refs to upstream `persona_runs`). The Risk persona shall persist to `risk_reviews` (verdict APPROVE/HOLD/REJECT, rationale, refs).

**REQ-INTRADAY-04-4 [E]** On every Korean trading day, the system shall execute the following scheduled cycles:
- Pre-market 07:30 KST: Micro (full analysis) → 07:50 Decision → [M5+] 07:55 Portfolio → 08:00 Risk → 08:05 code-rule check → 09:00 KRX-open paper auto-execution of approved signals
- Intraday 09:30, 11:00, 13:30, 14:30 KST: Micro cache reuse → Decision (delta signals only) → Risk → code-rule check → immediate paper auto-execution
- Post-market 16:00 KST: Daily report generation (see REQ-RISK-05-6)

**REQ-PERSONA-04-5 [E]** When Friday 17:00 KST occurs, the system shall invoke Macro persona (Opus 4.7). The Macro response shall be cached for 7 days; downstream personas shall reference the latest valid Macro cache.

**REQ-EVENT-04-6 [E]** When any of the following triggers fires during 09:00~15:30 KST, the system shall invoke Decision persona with trigger context within 60 seconds and proceed through Risk + code-rule + execution pipeline:
- A held stock moves ±3% intraday (per minute-level tick check)
- A new DART disclosure arrives for any held or watchlist ticker
- VIX moves +15% intraday OR USD/KRW moves ±1% intraday

**REQ-RISK-04-7 [N, SoD]** The system shall NOT execute any trade unless ALL of the following hold simultaneously:
- The Risk persona returns `APPROVE`
- All code-level limit checks pass (REQ-RISK-05-2)
- (When applicable, M5+) Portfolio persona returns a non-rejected adjusted signal

**REQ-BRIEF-04-8 [E]** When a persona invocation completes OR a trade executes OR an event trigger fires, the system shall send a structured briefing message to `TELEGRAM_CHAT_ID` (60443392) within 5 seconds. Message contents:
- Persona briefing: persona name, model, timestamp, response summary (3~5 lines), input tokens, output tokens, cost in KRW
- Trade briefing: ticker, direction, qty, fill price, fee, AND updated asset status (total assets, cash %, equity %)
- Trigger briefing: trigger reason, downstream persona response references, execution outcome

**REQ-BRIEF-04-9 [U]** The Telegram channel functions as a time-series briefing log, not merely an alert channel. Every persona response and every trade and every triggered cycle shall be recorded there.

**REQ-EXEC-04-10 [E]** When the KRX opens at 09:00 KST, queued Pre-market signals (approved during 08:05 code-rule check) shall be submitted as 시가 (open-price) market orders.

**REQ-PERSONA-04-11 [U]** The Decision persona system prompt (`decision.jinja`) shall encode 박세훈 본인의 트레이딩 원칙과 7-rule portfolio operating policy: (1) cash floor 30~50%, (2) take-profit when RSI > 85, (3) stop-loss at -7%, (4) sector cap 40%, (5) 3~7 holdings, (6) value-trap avoidance, (7) no short selling. The prompt shall additionally encode trading-cost awareness and trade-frequency guidance.

**REQ-PERSONA-04-12 [U, Cost-Awareness]** The Decision and Risk persona prompts shall explicitly reference trading costs: buy 0.015% + sell 0.345% (KOSPI) / 0.195% (KOSDAQ). Take-profit rule shall be precision-stated as "+1% gross, ≈ +0.5% net after fees".

---

### Module 5 — Risk, Cost, Calendar & Observability (M5)

**REQ-RISK-05-1 [U]** The system shall enforce five hard limits in code (`src/trading/risk/limits.py`):
- Daily max loss: -1.0% of capital
- Per-ticker max position: 20.0% of capital
- Total invested ratio: ≤ 80.0% (cash floor 20%)
- Max single-order amount: 10.0% of capital
- Daily order count: ≤ 10

**REQ-RISK-05-2 [E]** When any of the five limits would be violated by a pending order, the system shall reject the order BEFORE submission, record `audit_log` entry, and emit a Telegram alert.

**REQ-RISK-05-3 [E]** When the daily-loss limit is reached during a session, the system shall trigger the circuit breaker: set `halt_state=true` in `system_state`, block all subsequent orders for the day, and emit Telegram alert.

**REQ-RISK-05-4 [E]** When a Telegram message from `chat_id=60443392` contains command `/halt`, the system shall set `halt_state=true` and respond with confirmation within 5 seconds. When the same chat sends `/resume`, the system shall set `halt_state=false` and confirm.

**REQ-RISK-05-5 [N]** While `halt_state=true`, the system shall NOT submit any order to KIS regardless of persona output.

**REQ-REPORT-05-6 [E]** When 16:00 KST is reached on a Korean trading day, the system shall generate and send a daily report (Sonnet 4.6) covering: trades executed, PnL, persona response summary, persona token cost sum, SoD statistics (Risk REJECT/HOLD count), limit-trigger events. The report shall be generated even if zero trades occurred.

**REQ-PERSONA-05-7 [S]** While the number of held tickers is ≥ 5, the Portfolio persona shall be activated. While < 5, Portfolio persona shall be skipped.

**REQ-PERSONA-05-8 [E]** When Sunday occurs, the system shall invoke Retrospective persona producing a weekly review and proposing system-prompt improvements. Improvements shall be logged (table `retrospectives`) and NOT auto-applied.

**REQ-FATIGUE-05-9 [S, E]** While the most recent 3 consecutive Decision invocations all returned "no new signal", the system shall enter silent mode (`silent_mode=true`). In silent mode, only trade-execution events, event-trigger cycles, and circuit-breaker alerts shall be sent to Telegram; routine persona briefings shall be deferred to the daily report.

**REQ-FATIGUE-05-10 [E]** When the operator sends `/verbose` from `chat_id=60443392`, the system shall exit silent mode immediately and resume full briefing.

**REQ-OPS-05-11 [U]** The system shall execute a 3-week paper operation phase. At the end of the period, the system shall produce an evaluation report covering: uptime (container restart count), audit completeness (% of persona invocations and trades persisted), SoD function (Risk REJECT/HOLD counts > 0), persona response consistency notes, paper PnL vs benchmark (SMA + dual momentum), MDD, and total persona token cost.

**REQ-OPS-05-12 [U]** During M5, the system shall execute one backup-restore rehearsal: produce a backup, restore into a clean environment, and verify data integrity.

**REQ-COST-05-13 [U, Cost-Model]** Trading costs shall be defined as central constants in `src/trading/config.py`: `PAPER_FEE_BUY=0`, `LIVE_FEE_BUY=0.00015`, `LIVE_FEE_SELL_KOSPI=0.00345`, `LIVE_FEE_SELL_KOSDAQ=0.00195`, `SLIPPAGE_BPS=0.0005`. A function `estimate_fee(mode, side, market, notional)` shall apply these consistently.

**REQ-COST-05-14 [U, Cost-Aware-Limits]** All five hard limits in REQ-RISK-05-1 shall evaluate `notional + estimated_fee` (cost-inclusive). Single-order limit, per-ticker limit, and total invested ratio shall all use cost-inclusive computation.

**REQ-COST-05-15 [E]** After every KIS order response, the system shall populate `orders.fee` with the estimated fee (paper=0, live=`estimate_fee`). When the KIS fill response carries an actual fee field, the actual value shall override the estimate.

**REQ-COST-05-16 [E]** The daily report (REQ-REPORT-05-6) shall include cumulative trading fees, transaction taxes, and estimated slippage as separate line items.

**REQ-COST-05-17 [U]** The backtest engine (M3) shall use the same fee/tax constants as live cost model so `benchmark_runs` and persona-system forward results compare under identical cost assumptions.

**REQ-CAL-05-18 [E, Robustness-1]** The scheduler shall invoke `holidays.KR()` plus weekend plus 12/31 KRX closing checks before every cycle. On non-trading days no persona shall be invoked (Anthropic token savings) and an `audit_log` entry shall record the skip reason.

**REQ-OPS-05-19 [U]** The system shall automatically compute daily, weekly, and monthly cumulative PnL (mark-to-market via KIS balance lookup) and include them in the daily report.

**REQ-OPS-05-20 [E, Robustness-3]** Failure of any Anthropic API, KIS API, Postgres, or Telegram call shall write `audit_log` AND emit an immediate Telegram alert. Silent failure is forbidden. System errors shall be sent even when `silent_mode=true`.

**REQ-OPS-05-21 [E, Robustness-5]** Immediately after `backup.sh` completes, the system shall validate backup integrity via `pg_dump --schema-only` against the produced archive. On failure the system shall emit a Telegram alert.

---

### Future Scope — M6 별도 SPEC 후보 (Out of Scope for SPEC-TRADING-001)

**REQ-FUTURE-08-1 [U]** M6 (live trading) is OUT OF SCOPE for this SPEC. No live execution code path is delivered beyond the safety guard.

**REQ-FUTURE-08-2 [N, S]** The system shall keep `live_unlocked=false` by default. The system shall NOT provide any automated path to set `live_unlocked=true`. Unlocking requires ALL of:
1. Completion of the 3-week paper evaluation report (REQ-OPS-05-11)
2. Manual SQL update by the operator with an explicit `audit_log` entry justifying the change
3. A future SPEC documenting M6 entry conditions and live-mode safeguards

**REQ-FUTURE-08-3 [U]** M6 entry conditions, capital ratio (5~30%), and any tightened limits (e.g., daily loss -0.5%) shall be defined in the future SPEC, not here.

본 SPEC v0.2.0이 다루지 않는 영역은 다음 5개 후속 SPEC 후보로 분할한다 (이름과 범위만 명시; 본 SPEC에서는 구현하지 않는다):

- **SPEC-TRADING-002 (Live Trading Entry)**: M6 실거래 진입. `live_unlocked` 해제 절차, KIS API IP 제한, 자본 증액 룰, 양도세 신고 자동 생성.
- **SPEC-TRADING-003 (Intraday Precision)**: 분봉 데이터, 분할 주문, VWAP 평균 매수, 시초가/종가 동시호가, 호가창 분석.
- **SPEC-TRADING-004 (Market Microstructure)**: 선물 베이시스, 신용잔고, 공매도 잔량, KRX 업종 분류, 섹터 모멘텀, 상관관계 한도, VaR.
- **SPEC-TRADING-005 (CI/CD & Operations)**: 자동 백업 cron, Grafana 대시보드, 시스템 메트릭, 백업 복원 자동 검증, 로그 회전.
- **SPEC-TRADING-006 (Robustness 전담)**: 본 SPEC v0.2.0이 cross-cutting으로 분산 처리하지만, 안정화 후 단독 SPEC으로 통합 검토. Chaos engineering 테스트, 장애 시나리오 playbook.
- **SPEC-TRADING-007 (Persona Memory System)**: Macro/Micro 페르소나의 메모리 보유·갱신·삭제 패턴. `macro_memory`/`micro_memory` 테이블, memory_ops (create/update/archive/supersede) 응답 스키마, source_refs 의무, importance(1-5) 기반 retention, 회고 페르소나 주간 검토. 본 SPEC v0.2.0의 Module 4가 stateless persona를 가정하므로, 메모리 도입은 별도 SPEC에서 단일 책임으로 처리.

---

## Specifications (구현 명세 요약)

- 디렉토리/모듈/스키마/스케줄/모델 매핑은 `.moai/project/structure.md` 및 `tech.md` 를 단일 출처로 참조
- 테이블 진화: M2 (`orders`, `positions`, `audit_log`, `system_state`) → M3 (`ohlcv`, `macro_indicators`, `disclosures`, `fundamentals`, `flows`) → M4 (`persona_runs`, `persona_decisions`, `risk_reviews`) → M5 (`portfolio_adjustments`, `retrospectives`, `circuit_breaker_state`, `daily_reports`)
- 모든 스키마 변경은 `src/trading/db/migrations/` SQL 파일로 버전 관리
- Cost model 상수는 `src/trading/config.py` 단일 출처. backtest와 live 경로 모두 동일 상수 참조

## Traceability

| REQ ID | M | 구현 위치(예정) | 검증 (acceptance.md) |
|---|---|---|---|
| REQ-INFRA-01-1~8 | M1 | `compose.yaml`, `Dockerfile`, `backup.sh`, `src/trading/healthcheck.py` | M1 시나리오 |
| REQ-INFRA-01-9~11 | M1 | `.pre-commit-config.yaml`, CI 설정, 운영 문서 | M1 보강 시나리오 |
| REQ-MODE-01-8, REQ-MODE-02-* | M1/M2 | `src/trading/config.py`, `src/trading/kis/client.py` | M1, M2 시나리오 |
| REQ-KIS-02-1~8 | M2 | `src/trading/kis/*` | M2 시나리오 |
| REQ-KIS-02-9~13 | M2 | `src/trading/kis/client.py`, `src/trading/db/transactions.py` | M2 보강 시나리오 |
| REQ-DATA-03-1~7 | M3 | `src/trading/data/*`, `src/trading/strategies/*`, `src/trading/backtest/*` | M3 시나리오 |
| REQ-DATA-03-8~10 | M3 | `pykrx_adapter.py`, `fred_adapter.py`, `fundamentals/flows` 캐시 | M3 보강 시나리오 |
| REQ-PERSONA-04-1~10 | M4 | `src/trading/personas/*`, `src/trading/scheduler/*` | M4 시나리오 |
| REQ-PERSONA-04-11~12 | M4 | `personas/prompts/decision.jinja`, `risk.jinja` | M4 보강 시나리오 |
| REQ-BRIEF-04-* | M4 | `src/trading/alerts/telegram.py`, `src/trading/personas/orchestrator.py` | M4 시나리오 |
| REQ-RISK-04-7, REQ-RISK-05-1~5 | M4/M5 | `src/trading/risk/*`, `src/trading/personas/risk.py` | M5 시나리오 |
| REQ-REPORT-05-6, REQ-FATIGUE-05-*, REQ-OPS-05-11~12 | M5 | `src/trading/reports/*`, `src/trading/bot/telegram_bot.py` | M5 시나리오 |
| REQ-COST-05-13~17 | M5 | `src/trading/config.py`, `src/trading/risk/limits.py`, `src/trading/backtest/engine.py` | M5 비용 시나리오 |
| REQ-CAL-05-18, REQ-OPS-05-19~21 | M5 | `src/trading/scheduler/calendar.py`, `src/trading/reports/daily_report.py`, `backup.sh` | M5 신규 시나리오 |
| REQ-FUTURE-08-* | (out) | `src/trading/config.py` 안전장치만 | M2 live-block 시나리오 |
