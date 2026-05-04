---
id: SPEC-TRADING-007
version: 0.1.0
status: draft
created: 2026-05-04
updated: 2026-05-04
author: onigunsow
priority: medium
issue_number: 0
---

# SPEC-TRADING-007 — Persona Memory System + Static Market Context

## HISTORY

| 일자 | 버전 | 변경 내용 | 작성자 |
|---|---|---|---|
| 2026-05-04 | 0.1.0 | 초안 — Persona Memory System + Static Market Context | onigunsow |

## 범위 요약

박세훈 페르소나 시스템(SPEC-TRADING-001)이 stateless 호출 방식이어서 (1) 누적 학습 부재, (2) 매번 raw 데이터로 분석 두 가지 한계를 가진다. 본 SPEC은 이를 보완하는 두 가지 보완적 컨텍스트 메커니즘을 정의한다.

1. **Static Market Context** — `~/trading/data/contexts/`에 매크로/마이크로 시장조사 자료를 사전에 `.md`로 정리. 갱신 주체는 **cron + 코드** (대부분), AI는 외부 뉴스 요약 1건만. 페르소나 호출 시 input에 주입하여 응답 풍부화. 비용 거의 0 (자체 캐시 데이터 정리).
2. **Dynamic Persona Memory** — 페르소나가 자가 생성·갱신·삭제하는 누적 인사이트. 갱신 주체는 **페르소나 응답의 `memory_ops` 명령**, 라이프사이클은 페르소나가 결정 (create/update/archive/supersede), orchestrator가 실행. DB 테이블 `macro_memory`, `micro_memory`. 회고 페르소나가 주간 검토.

두 메커니즘은 보완적이며 함께 페르소나 input에 주입된다. 본 SPEC은 SPEC-TRADING-001이 모의 운영(M5)을 안정화한 이후 도입된다 (priority: medium). 본 SPEC은 SPEC-TRADING-001 v0.2.0의 Module 4 (페르소나)를 보완하는 별도 책임 SPEC으로, SPEC-001의 stateless 가정은 그대로 유지하되 입력 풍부화·메모리 레이어를 비침투적으로 얹는다.

## 환경 (Environment)

- 기존 SPEC-TRADING-001 인프라 그대로 활용 — Postgres, Anthropic API, Telegram, Docker compose 재사용
- 추가 cron 스케줄: 06:00 macro_context, 06:30 micro_context, 06:45 micro_news (영업일), 금 16:30 macro_news
- 신규 디렉토리: `~/trading/data/contexts/` (mode 644, `.md` 4종)
- 신규 DB 테이블: `macro_memory`, `micro_memory` (Postgres 16-alpine 동일)
- 외부 RSS 의존성 (macro_news.md 전용): Reuters World, FT Markets, Bloomberg Politics, Federal Reserve press, Bank of Korea press 5종 (구체 피드 URL은 plan.md)

## 가정 (Assumptions)

1. SPEC-TRADING-001 v0.2.0의 페르소나 시스템(Macro/Micro/Decision/Risk/Portfolio/Retrospective)이 모의 3주 운영을 통과하고 안정 작동한다 (메모리 도입 전제)
2. 박세훈 페르소나의 `decision.jinja` 시스템 프롬프트에 메모리 사용 방식과 memory_ops 응답 형식을 추가 명시 가능
3. Anthropic API credits 충분 — 메모리 도입 시 input 토큰 약 +1.5K → 페르소나 호출당 비용 +30%, 월 비용 ~13~17만원 추가 (M5+ 기준 ~50~67만원)
4. SPEC-001의 audit_log, persona_runs 테이블이 본 SPEC의 source_refs FK 대상으로 그대로 사용 가능
5. 외부 RSS 피드는 주간 1회 호출이므로 일시 장애가 있어도 다음 주 갱신으로 충분 (미션 크리티컬 X)

## Robustness Principles (SPEC-001 6대 원칙 승계)

본 SPEC은 SPEC-TRADING-001 v0.2.0의 6대 Robustness 원칙을 그대로 승계한다. 특히 다음 항목이 본 SPEC에서 강조된다:

- **외부 의존성 실패 가정** — RSS 피드 장애, cron 실행 실패, LLM 호출 실패 시 graceful degradation (REQ-CTX-01-6)
- **상태 무결성 트랜잭션** — memory_ops 다중 실행은 단일 트랜잭션 (REQ-MEM-03-2)
- **실패 침묵 금지** — 컨텍스트 갱신 실패 / 메모리 ops 실패 모두 텔레그램 알림 + audit_log
- **Memory bias 차단** — 메모리는 컨텍스트일 뿐, 현재 데이터 우선 (REQ-MEM-04-5)
- **자동 삭제 금지** — 모든 메모리는 soft-delete (status='archived') 만, 영구 삭제는 박세훈 수동 SQL (REQ-MEM-05-4)

## 요구사항 (Requirements) — EARS

EARS 표기 약식: **U**=Ubiquitous, **E**=Event-driven, **S**=State-driven, **O**=Optional, **N**=Unwanted

---

### Module 1 — Static Market Context (`.md`-based, cron-managed)

**REQ-CTX-01-1 [U]** The system shall maintain four market-context files under `~/trading/data/contexts/` with file mode 644:
- `macro_context.md` — 거시 캐시 표 (FRED+ECOS+yfinance) + 글로벌 자산 추이 (S&P500/Nasdaq/VIX/USD-KRW/금/원유/BTC)
- `macro_news.md` — 외부 RSS 글로벌 정책·지정학 헤드라인 주간 LLM 요약
- `micro_context.md` — 워치리스트 종목별 펀더(PER/PBR/ROE) + 수급(외국인/기관/개인 5일/20일 누적) + 기술적(MA/RSI/MACD/거래량) 표
- `micro_news.md` — DART 공시 일별 정리 (보유/관심 종목)

**REQ-CTX-01-2 [E]** When 06:00 KST occurs, the system shall regenerate `macro_context.md` from cached `macro_indicators`, `ohlcv`, FRED+ECOS+yfinance source rows. Generation is pure code (no LLM call).

**REQ-CTX-01-3 [E]** When 06:30 KST occurs, the system shall regenerate `micro_context.md` from cached `fundamentals`, `flows`, and `ohlcv` rows for KOSPI200 + KOSDAQ150 + 사용자 워치리스트. Generation is pure code (no LLM call).

**REQ-CTX-01-4 [E]** When 06:45 KST occurs on Korean trading days (per `holidays.KR()` + KRX calendar in REQ-CAL-05-18), the system shall regenerate `micro_news.md` from cached DART `disclosures` rows for held tickers + watchlist over the past 7 days. Generation is pure code (no LLM call).

**REQ-CTX-01-5 [E]** When Friday 16:30 KST occurs, the system shall regenerate `macro_news.md` via single Sonnet 4.6 call summarizing external RSS feeds (Reuters World, FT Markets, Bloomberg Politics, Federal Reserve press, Bank of Korea press; specific URLs in plan.md). Output 5~7 headlined bullets covering 정책·지정학·중앙은행 시그널. This is the only LLM-driven static context file.

**REQ-CTX-01-6 [N]** 컨텍스트 `.md` 갱신 실패 시 (cron 에러, RSS 타임아웃, LLM 에러) the system shall NOT overwrite existing files. The system shall emit a Telegram alert via `system error` channel and write `audit_log` entry. Stale file 사용은 허용되며 다음 주기 갱신을 기다린다.

---

### Module 2 — Dynamic Persona Memory Schema

**REQ-MEM-02-1 [U]** The system shall persist two tables `macro_memory` and `micro_memory` with the following columns:
- `id` BIGSERIAL PRIMARY KEY
- `scope` TEXT NOT NULL
- `scope_id` TEXT (e.g., ticker code or sector code; nullable for scope='global')
- `kind` TEXT NOT NULL
- `summary` TEXT NOT NULL (≤ 500 chars enforced application-side)
- `importance` SMALLINT NOT NULL CHECK 1 ≤ importance ≤ 5
- `source_refs` JSONB NOT NULL
- `valid_from` DATE NOT NULL
- `valid_until` DATE (nullable; null = open-ended)
- `status` TEXT NOT NULL DEFAULT 'active' (CHECK status IN 'active','archived','superseded')
- `supersedes_id` BIGINT REFERENCES same_table(id)
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT NOW()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT NOW()
- `last_accessed_at` TIMESTAMPTZ

Indexes: `(scope, status, importance DESC)`, `(scope_id, status)`, `(valid_until)` for retention sweeps.

**REQ-MEM-02-2 [U]** Domain enums:
- `macro_memory.scope` ∈ {'global','korea','usa','china','etc'}
- `macro_memory.kind` ∈ {'geopolitical','economic','policy','regime','event'}
- `micro_memory.scope` ∈ {'sector','ticker'}
- `micro_memory.kind` ∈ {'earnings','disclosure','thematic','flow_pattern','regulatory'}

Enforced as CHECK constraints in DB migration 008.

**REQ-MEM-02-3 [N]** The system shall reject any memory INSERT with empty or null `source_refs`. `source_refs` JSONB must contain `persona_run_id` (required, FK semantic to `persona_runs.id`) plus at least one optional element from {DART `rcept_no`, news URL, FRED `series_id`, ECOS stat code}. The application layer shall validate this; DB shall additionally enforce `jsonb_typeof(source_refs) = 'object'` and presence of `persona_run_id` key.

**REQ-MEM-02-4 [E]** When `valid_until ≤ CURRENT_DATE` OR (`last_accessed_at < NOW() - INTERVAL '30 days'` AND `importance < 4`) OR (`last_accessed_at < NOW() - INTERVAL '60 days'` AND `importance >= 4`), the system shall set `status='archived'` and write `audit_log` event 'MEMORY_OP_ARCHIVE_AUTO'. The retention sweep shall run as a daily job (suggested 06:55 KST, before any persona invocation that day).

---

### Module 3 — Memory Operations (페르소나 응답 → DB)

**REQ-MEM-03-1 [U]** Persona response JSON schema shall accept an optional top-level `memory_ops` array. Each element shape:
- `op` ∈ {'create','update','archive','supersede'}
- `table` ∈ {'macro_memory','micro_memory'}
- For `create`: scope, scope_id, kind, summary, importance, valid_from, valid_until (optional), source_refs (optional refs beyond persona_run_id)
- For `update`: id, fields-to-update (summary | importance | valid_until)
- For `archive`: id, reason (free text)
- For `supersede`: old_id, new_summary + new_importance + new_valid_from + new_valid_until (optional)

Persona base class (`src/trading/personas/base.py`) shall extract `memory_ops` from the response prior to returning the structured result to the orchestrator.

**REQ-MEM-03-2 [E]** When the orchestrator receives a persona response containing `memory_ops`, the system shall execute every op in a single Postgres transaction:
- `create` → INSERT row, automatically attach `persona_run_id` of the current invocation to `source_refs` even if persona omitted it
- `update` → UPDATE specified columns + bump `updated_at` + bump `last_accessed_at`
- `archive` → SET `status='archived'`, write reason to `audit_log`
- `supersede` → SET old row `status='superseded'`, INSERT new row with `supersedes_id = old_id`, copy `scope/scope_id/kind` from old row

If any op fails (constraint violation, ownership rejection per REQ-MEM-03-3, source_refs validation), the entire transaction shall ROLLBACK and the failure shall be logged + Telegram-alerted; the persona response itself is still recorded to `persona_runs` (decision/signal handling proceeds independently).

**REQ-MEM-03-3 [N]** The system shall reject `memory_ops` that target a memory belonging to a different persona's domain. Ownership matrix:
- Macro persona may operate on `macro_memory` only
- Micro persona may operate on `micro_memory` only
- Decision, Risk, Portfolio personas may NOT execute memory_ops in this SPEC version (read-only consumers)
- Retrospective persona may execute `archive` on either table but NOT `create`/`update`/`supersede` (its proposals route through `retrospectives` table per REQ-MEM-05-2)

Ownership violations shall be rejected with `audit_log` event 'MEMORY_OP_OWNERSHIP_REJECT' and a Telegram alert.

**REQ-MEM-03-4 [E]** Every memory op (whether successfully committed or rejected) shall write an `audit_log` entry with `event_type` matching the pattern `MEMORY_OP_{CREATE|UPDATE|ARCHIVE|SUPERSEDE}_{OK|FAIL}` and a JSON payload capturing op details + persona_run_id + outcome.

---

### Module 4 — Memory Injection (페르소나 input)

**REQ-MEM-04-1 [E]** When the Macro persona is invoked, the system shall load the following into prompt input:
- `macro_context.md` 전문
- `macro_news.md` 전문
- Active macro_memory rows filtered by `status='active' AND importance >= 3 AND valid_from <= CURRENT_DATE AND (valid_until IS NULL OR valid_until >= CURRENT_DATE)`, ordered by `importance DESC, updated_at DESC`, limited to top 20

**REQ-MEM-04-2 [E]** When the Micro persona is invoked, the system shall load the following into prompt input:
- `micro_context.md` 전문
- `micro_news.md` 전문
- Active micro_memory rows scoped to (현재 워치리스트 종목 + 그 종목들의 섹터), filtered by `status='active'`, ordered by `importance DESC, updated_at DESC`, limited to top 20

**REQ-MEM-04-2a [E]** When the Decision persona (박세훈) is invoked, the system shall load **read-only** access to BOTH active macro_memory + micro_memory (top 10 each, scoped by relevance to current signals being considered). Decision persona may reference memory in rationale but **MUST NOT** emit `memory_ops` in its response — write 권한은 Macro/Micro persona 한정 (SoD enforcement). Any `memory_ops` field in Decision response is silently ignored with `audit_log` event 'MEMORY_OP_DECISION_WRITE_REJECTED'.

**REQ-MEM-04-2b [E]** When the Risk persona is invoked, the system shall load **read-only** access to:
- Active macro_memory + micro_memory relevant to the decision under review (top 5 each)
- Recent risk_reviews (last 7 days, max 20 rows) for persona drift detection
Risk persona uses memory + history to detect 페르소나 drift / inconsistency patterns. Same write restriction as Decision (REQ-MEM-04-2a).

**REQ-MEM-04-3 [U]** Memory injection token caps: Macro persona ≤ 4,000 tokens, Micro persona ≤ 2,000 tokens, **Decision persona ≤ 1,500 tokens (10+10 rows)**, **Risk persona ≤ 1,000 tokens (5+5 rows)**. When candidate memory rows exceed the cap, the system shall greedily include rows by `importance DESC, last_accessed_at DESC` until cap reached. Token counting uses Anthropic tokenizer.

**REQ-MEM-04-4 [U]** When a memory row is included in a persona prompt, the system shall update its `last_accessed_at = NOW()` (LRU tracking). This update happens regardless of whether the persona response contains memory_ops referring to that row.

**REQ-MEM-04-5 [U]** The Macro and Micro persona system prompts shall include the directive (translated into Korean for prompt clarity): *"메모리는 과거 인사이트의 컨텍스트 정보일 뿐이며, 현재 시점 데이터 (시세, 공시, 뉴스, 거시 지표)가 의사결정의 우선이다. 메모리와 현재 데이터가 모순될 경우 현재 데이터를 따른다. 모순 발견 시 `memory_ops`로 해당 메모리를 archive 또는 supersede 하라."* This is the memory-bias mitigation clause.

---

### Module 5 — Memory Audit & Retrospective

**REQ-MEM-05-1 [E]** When the Sunday Retrospective persona runs, the system shall load:
- Summary of memory ops in the past 7 days (counts per op type per table, top 10 newly created memories by importance)
- Memory consistency report computed by code (not LLM): pairs of active memories with overlapping `(scope, scope_id, kind)` and conflicting `summary` keywords (heuristic) flagged as 모순 후보; pairs with high `summary` similarity flagged as 중복 후보

**REQ-MEM-05-2 [O]** Where the Retrospective persona suggests memory cleanup actions in its response, the system shall log the proposals to the existing `retrospectives` table (column `memory_proposals` JSONB to be added in migration 008) — NOT auto-apply. User review via Telegram acknowledgement is required for any cleanup. SPEC-001 REQ-PERSONA-05-8 already mandates Retrospective improvements are not auto-applied; this clause extends that policy to memory cleanup.

**REQ-MEM-05-3 [U]** The daily report (SPEC-001 REQ-REPORT-05-6) shall append a single line to its body: `"메모리 활성 X건 (M:{macro_count}, U:{micro_count}), 최근 7일 ops {created, updated, archived, superseded}"`. This is informational only, not a quality gate.

**REQ-MEM-05-4 [N]** The system shall NEVER hard-delete (`DELETE FROM macro_memory` / `DELETE FROM micro_memory`) memory rows automatically. Retention sweeps (REQ-MEM-02-4) only set `status='archived'`. Permanent deletion is reserved for manual SQL by 박세훈 with corresponding `audit_log` entry written by the operator.

---

### Future Scope (Out of Scope for SPEC-TRADING-007)

The following enhancements are deliberately deferred:

- **Vector embeddings on memory** — Currently memory ranking uses `importance + last_accessed_at` only. Future SPEC may add pgvector embeddings for cosine-similarity recall.
- **Cross-persona memory query enhancement** — Currently Decision/Risk personas have read-only memory access (REQ-MEM-04-2a/2b). Future SPEC may add bidirectional memory linking (e.g., Decision flags an insight back to Macro for memory creation), advanced retrieval (semantic search by current signal context), or Portfolio persona memory consumption (currently no memory access).
- **Memory consolidation** — When 5+ archived memories share `(scope, scope_id, kind)`, future LLM call could summarize into a single high-level memory. Deferred.
- **Memory provenance UI** — A Telegram or CLI viewer for browsing active memories by scope. Deferred.

---

## Specifications (구현 명세 요약)

- 디렉토리/모듈/스키마/스케줄/모델 매핑은 `.moai/project/structure.md` 및 `tech.md` 단일 출처를 그대로 참조한다
- 신규 마이그레이션: `src/trading/db/migrations/008_persona_memory.sql` — `macro_memory`, `micro_memory` 테이블 + `retrospectives.memory_proposals` JSONB 컬럼 추가
- 신규 모듈:
  - `src/trading/contexts/build_macro_context.py`, `build_micro_context.py`, `build_micro_news.py`, `build_macro_news.py`
  - `src/trading/memory/store.py` — memory CRUD + retention sweep + LRU tracking
  - `src/trading/memory/injector.py` — `.md` 로드 + 활성 메모리 조회 + 토큰 캐핑 + persona input 조립
- 페르소나 base 확장: `src/trading/personas/base.py` 응답 파서가 `memory_ops` 추출 후 orchestrator로 전달, orchestrator가 `memory.store.execute_ops()` 호출
- 시스템 프롬프트 보강: `personas/prompts/macro.jinja`, `micro.jinja` 두 파일에 (1) memory_ops 응답 스키마 (2) memory bias 차단 directive 추가

## Traceability

| REQ ID | 모듈 | 구현 위치 (예정) | 검증 (acceptance.md) |
|---|---|---|---|
| REQ-CTX-01-1~6 | M1 (Static Context) | `src/trading/contexts/*`, `src/trading/scheduler/daily.py` | M1 시나리오 |
| REQ-MEM-02-1~4 | M2 (Schema) | `src/trading/db/migrations/008_persona_memory.sql`, `src/trading/memory/store.py` | M2 시나리오 |
| REQ-MEM-03-1~4 | M3 (Memory Ops) | `src/trading/personas/base.py`, `src/trading/memory/store.py`, `src/trading/personas/orchestrator.py` | M3 시나리오 |
| REQ-MEM-04-1~5 | M4 (Injection) | `src/trading/memory/injector.py`, `personas/prompts/macro.jinja`, `personas/prompts/micro.jinja` | M4 시나리오 |
| REQ-MEM-05-1~4 | M5 (Audit & Retro) | `src/trading/personas/retrospective.py`, `src/trading/reports/daily_report.py`, `src/trading/memory/store.py` | M5 시나리오 |
