# Implementation Plan: SPEC-TRADING-010

Created: 2026-05-05
SPEC Version: 0.1.0
Development Mode: DDD (ANALYZE-PRESERVE-IMPROVE)
Agent: manager-strategy

---

## 1. Plan Summary

SPEC-TRADING-010 adds two complementary cost optimization strategies to reduce monthly API cost to <=10 man-won:

1. **Haiku Hybrid Model Routing** — Route low-reasoning personas (Micro screening, Daily Report, Macro News) to claude-haiku-4-5 (~73% cheaper per token), preserving Sonnet/Opus for decision-critical personas.
2. **pgvector Semantic Context Retrieval** — Replace full `.md` file injection with embedding-based top-K chunk retrieval, reducing per-tool-call token count by 60-70%.

Implementation follows 13 atomic tasks across 5 phases (weekly rollout), using the existing DDD methodology with characterization tests to preserve current behavior before enhancement.

---

## 2. Technology Stack

### New Dependencies

| Library | Version | Purpose | Selection Rationale |
|---|---|---|---|
| `pgvector` (Python) | 0.3.6+ | psycopg3 vector type adapter (register_vector) | Official pgvector adapter, tiny footprint, enables clean vector type handling without raw text serialization |
| `pgvector` (PG ext) | Built-in with pg:16-alpine | Vector similarity search extension | Already available in postgres:16-alpine, no image modification needed |

### No New Dependencies Required (project philosophy: direct implementation)

| Capability | Implementation | Rationale |
|---|---|---|
| Voyage AI embedding | Direct `httpx` REST calls | Project avoids SDK proliferation ("자체 통제 가능한 의존성") — httpx already in stack |
| OpenAI embedding | Direct `httpx` REST calls | Same rationale; both APIs are simple JSON-over-HTTPS |
| Token counting | Heuristic (4 chars/token) | Consistent with existing codebase pattern (base.py line 195: `len(input_str) // 4`) |

### Existing Libraries (unchanged)

| Library | Current | Usage in SPEC-010 |
|---|---|---|
| `httpx` | Existing | Embedding API calls (Voyage AI / OpenAI) |
| `psycopg[binary]` v3 | Existing | pgvector queries via registered vector type |
| `anthropic` | Existing | Haiku model calls (same SDK, different model string) |
| `tenacity` | Existing | Exponential backoff for embedding API rate limits |

### Environment Requirements

- PostgreSQL: 16-alpine (existing, supports `CREATE EXTENSION vector`)
- Python: 3.13-slim (existing)
- New env vars: `EMBEDDING_MODEL`, `VOYAGE_API_KEY`, `EMBEDDING_DIMENSIONS`, `MONTHLY_COST_TARGET_KRW`

---

## 3. Task Decomposition

### TASK-001: Migration 011 — pgvector + Model Routing Schema

**Description**: Create SQL migration enabling pgvector extension, context_embeddings table, model_routing column, shadow_test_results table, and semantic retrieval feature flags.

**Requirement Mapping**: REQ-PGVEC-02-1, REQ-PGVEC-02-2, REQ-ROUTER-01-3, REQ-MIGR-05-7

**Dependencies**: None (foundation task)

**Acceptance Criteria**:
- `CREATE EXTENSION IF NOT EXISTS vector` executes without error
- `context_embeddings` table created with correct schema (vector(1024), UNIQUE constraint, IVFFlat index)
- `system_state.model_routing` JSONB column with correct default
- `system_state.semantic_retrieval_enabled` BOOLEAN column (default false)
- `system_state.shadow_test_active` BOOLEAN column (default false)
- `shadow_test_results` table created with FK references
- Migration follows existing 001-010 pattern (schema_migrations + audit_log)

**Effort**: M

---

### TASK-002: Embedding Configuration Module

**Description**: Create `src/trading/embeddings/__init__.py` and `src/trading/embeddings/config.py` with embedding model configuration, API endpoint selection, and dimension constants.

**Requirement Mapping**: REQ-PGVEC-02-4, REQ-PGVEC-02-8

**Dependencies**: None

**Acceptance Criteria**:
- `EMBEDDING_MODEL` env var support (voyage-3, text-embedding-3-small)
- Correct dimension mapping (voyage-3=1024, text-embedding-3-small=1536)
- API endpoint and auth configuration (from env vars, not DB)
- Rate limit constants (100 req/s for Voyage AI)
- Batch size constants (50 chunks per API call)

**Effort**: S

---

### TASK-003: Markdown Chunker

**Description**: Create `src/trading/embeddings/chunker.py` implementing section-header-based splitting with 200-500 token chunks, 50-token overlap, and metadata extraction.

**Requirement Mapping**: REQ-PGVEC-02-6

**Dependencies**: TASK-002

**Acceptance Criteria**:
- Splits on `## ` and `### ` section headers as primary boundaries
- Within sections, splits on double newlines or at 400 tokens (hard limit: 500)
- 50-token overlap between adjacent chunks
- Table rows never split mid-row
- Each chunk has metadata: `{section_header, tickers_mentioned[], date_range}`
- Token counting uses 4-chars-per-token heuristic
- macro_context.md (~3500 tokens) produces 12-18 chunks
- micro_context.md (~5000 tokens) produces 18-25 chunks

**Effort**: M

---

### TASK-004: Embedding Generator

**Description**: Create `src/trading/embeddings/embedder.py` implementing embedding API calls with batching and rate-limit handling.

**Requirement Mapping**: REQ-PGVEC-02-7, REQ-PGVEC-02-4

**Dependencies**: TASK-002

**Acceptance Criteria**:
- Generates embeddings via configured model (Voyage AI or OpenAI)
- Batch size <= 50 chunks per API call
- Rate limiting at <= 100 requests/second
- Exponential backoff on 429 (1s, 2s, 4s, max 30s, 5 retries)
- Returns list of (chunk_index, embedding_vector) tuples
- Handles both 1024-dim (voyage-3) and 1536-dim (text-embedding-3-small)
- Graceful failure with structured error (no crash, returns partial results)

**Effort**: M

---

### TASK-005: Embedding Indexer

**Description**: Create `src/trading/embeddings/indexer.py` implementing incremental upsert of chunks + embeddings into context_embeddings table.

**Requirement Mapping**: REQ-PGVEC-02-5, REQ-PGVEC-02-9

**Dependencies**: TASK-001, TASK-003, TASK-004

**Acceptance Criteria**:
- Detects changed/new/stale chunks via content hash comparison
- Upserts only changed/new chunks (skips unchanged)
- Deletes stale chunks no longer in source file
- Logs to audit_log: chunks_processed, embeddings_generated, upserts, deletes, total_time_ms, embedding_cost_usd
- Completes single .md file processing in <= 30 seconds (REQ-NFR-10-3)
- Transaction-safe (atomic upsert/delete per source_file)

**Effort**: M

---

### TASK-006: Semantic Searcher

**Description**: Create `src/trading/embeddings/searcher.py` implementing cosine similarity search against context_embeddings.

**Requirement Mapping**: REQ-SCTX-03-2, REQ-NFR-10-2

**Dependencies**: TASK-001, TASK-004

**Acceptance Criteria**:
- Given query text, generates query embedding via configured model
- Executes pgvector cosine similarity search (`embedding <=> query_vec`)
- Filters by `source_file` parameter
- Returns top-K chunks ordered by similarity descending
- Total latency (embed + search + assemble) <= 500ms
- If latency exceeds 500ms, logs SEMANTIC_SEARCH_SLOW and returns error for fallback
- Returns structured response: chunks with text, similarity, metadata, total_chunks, returned_chunks, estimated_tokens

**Effort**: M

---

### TASK-007: get_static_context Enhancement

**Description**: Enhance `src/trading/tools/context_tools.py` with `mode`, `query`, `top_k` parameters and update tool schema in `registry.py`.

**Requirement Mapping**: REQ-SCTX-03-1 through REQ-SCTX-03-6

**Dependencies**: TASK-006

**Acceptance Criteria**:
- `mode="semantic"` + `query` provided → cosine search via searcher
- `mode="full"` or no mode/query → full .md content (backward compatible)
- Cold start fallback: if no embeddings exist for source_file, auto-fallback to full mode + log
- Feature flag: if `SEMANTIC_RETRIEVAL_ENABLED=false`, ignore mode=semantic
- Registry schema updated with mode/query/top_k properties
- Response format matches SPEC (source, mode, query, results[], total_chunks, returned_chunks, estimated_tokens)
- Persona prompts updated to instruct semantic mode usage

**Effort**: M

---

### TASK-008: Model Router

**Description**: Create `src/trading/personas/model_router.py` implementing per-persona model resolution from system_state.model_routing.

**Requirement Mapping**: REQ-ROUTER-01-1 through REQ-ROUTER-01-4, REQ-ROUTER-01-7

**Dependencies**: TASK-001

**Acceptance Criteria**:
- `resolve_model(persona_name)` reads model_routing from system_state
- haiku_eligible=true AND haiku_enabled=true → configured model (Haiku)
- haiku_eligible=true AND haiku_enabled=false → fallback to claude-sonnet-4-6
- haiku_eligible=false → configured model (Sonnet or Opus)
- Non-eligible personas reject Haiku toggle attempts with error message
- Model routing config cached (TTL ~60s) to avoid per-call DB reads
- Haiku pricing constants added to config.py

**Effort**: M

---

### TASK-009: Orchestrator Model Router Integration

**Description**: Wire Model Router into `orchestrator.py` and `base.py` invocation chain so persona calls use resolved model.

**Requirement Mapping**: REQ-ROUTER-01-4, REQ-ROUTER-01-8

**Dependencies**: TASK-008

**Acceptance Criteria**:
- Orchestrator calls `model_router.resolve_model(persona_name)` before each persona invocation
- Resolved model passed to `call_persona(model=resolved_model)`
- Telegram briefing shows actual model used (not hardcoded "claude-sonnet-4-6")
- `persona_runs.model` column accurately records actual model per invocation
- Existing behavior preserved when model_routing has default config (Sonnet for most)

**Effort**: M

---

### TASK-010: Cost Monitoring Enhancement

**Description**: Extend `daily_report.py` and `config.py` with per-model cost breakdown, Haiku savings estimate, and semantic retrieval statistics.

**Requirement Mapping**: REQ-COST-04-1 through REQ-COST-04-4

**Dependencies**: TASK-007, TASK-009

**Acceptance Criteria**:
- Haiku pricing added to PRICING_USD_PER_MTOK in base.py: (`claude-haiku-4-5`: $0.80/$4.00)
- Embedding model pricing in config.py
- Daily report shows per-model call distribution (Opus/Sonnet/Haiku/Embedding counts)
- Daily report shows Haiku savings estimate vs all-Sonnet baseline
- Daily report shows semantic retrieval savings (avg token reduction %)
- Monthly cost tracking against 100,000 KRW target
- CLI `trading cost-report` extended with new metrics
- Monthly target warning at 100,000 KRW (supplements SPEC-008 200K hard limit)

**Effort**: M

---

### TASK-011: Telegram Commands

**Description**: Implement `/model`, `/haiku`, `/shadow-test`, `/cost`, `/semantic` bot commands for runtime control.

**Requirement Mapping**: REQ-ROUTER-01-5, REQ-ROUTER-01-6, REQ-MIGR-05-4, REQ-COST-04-5

**Dependencies**: TASK-008, TASK-007

**Acceptance Criteria**:
- `/model <persona> <model>` — updates system_state.model_routing + audit_log + confirm
- `/haiku <persona> on|off` — toggles haiku_enabled (only if haiku_eligible) + audit_log + confirm
- `/haiku <non-eligible> on` — rejected with error message
- `/shadow-test <persona>` — triggers dual-model run on next invocation
- `/cost` — replies with today's cost + month cumulative + target %
- `/semantic on|off` — toggles SEMANTIC_RETRIEVAL_ENABLED
- All commands restricted to chat_id=60443392
- Response within 5 seconds

**Effort**: M

---

### TASK-012: Shadow Testing and Quality Gates

**Description**: Create `src/trading/personas/shadow_test.py` implementing dual-model comparison, overlap scoring, and quality gate auto-revert.

**Requirement Mapping**: REQ-MIGR-05-2 through REQ-MIGR-05-4

**Dependencies**: TASK-008, TASK-009

**Acceptance Criteria**:
- Dual-model run: primary (Haiku) + shadow (Sonnet) on identical inputs
- Candidate overlap calculation: top-5 buy/sell/hold comparison
- Results persisted to shadow_test_results table
- Quality gate evaluation: overlap < 85% for 3 consecutive days → auto-revert
- Auto-revert: set haiku_enabled=false, write audit_log QUALITY_GATE_HAIKU_REVERT, Telegram alert
- Quality gate pass: all scores >= 0.85 for 5 days → disable shadow, audit_log QUALITY_GATE_PASSED
- `/shadow-test` one-shot mode: single test, report overlap, auto-reset

**Effort**: L

---

### TASK-013: Cron Integration (Post-Build Embedding Hook)

**Description**: Wire context build crons (SPEC-007) to trigger embedding pipeline after .md file regeneration.

**Requirement Mapping**: REQ-PGVEC-02-5

**Dependencies**: TASK-005

**Acceptance Criteria**:
- After `build_macro_context()` completes → trigger embedding pipeline for macro_context
- After `build_micro_context()` completes → trigger embedding pipeline for micro_context
- After macro_news/micro_news builds → trigger respective embedding pipelines
- Runs as background task (does not block persona invocations)
- Completes within 30 seconds per file (REQ-NFR-10-3)
- Failure does not block main operation (graceful log + continue)

**Effort**: S

---

## 4. Implementation Phases

### Phase A — Foundation (Week 1)

**Goal**: Deploy pgvector infrastructure and embedding pipeline. Semantic mode available but NOT enabled.

**Tasks**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005, TASK-006, TASK-013

**Deliverables**:
- Migration 011 applied
- `src/trading/embeddings/` module complete with all 5 files
- Initial embeddings generated for all 4 .md files
- `SEMANTIC_RETRIEVAL_ENABLED` remains `false`
- All existing persona invocations continue unchanged

**TAG Chain**:
```
TASK-001 (migration) → TASK-002 (config) → TASK-003 (chunker) ─┐
                                          → TASK-004 (embedder) ─┼→ TASK-005 (indexer) → TASK-013 (cron)
                                                                  └→ TASK-006 (searcher)
```

**Verification**: M2-1 through M2-6 scenarios, M5-1 scenario

---

### Phase B — Semantic Retrieval + Model Router (Week 2)

**Goal**: Enable semantic retrieval. Deploy Model Router. Enable Haiku for macro_news only (lowest risk).

**Tasks**: TASK-007, TASK-008, TASK-009

**Deliverables**:
- `get_static_context` enhanced with semantic mode
- Model Router operational
- Orchestrator uses Model Router for model resolution
- `SEMANTIC_RETRIEVAL_ENABLED=true`
- `haiku_enabled=true` for macro_news only

**TAG Chain**:
```
TASK-006 → TASK-007 (context enhancement)
TASK-001 → TASK-008 (model router) → TASK-009 (orchestrator integration)
```

**Verification**: M1-1, M1-2, M3-1 through M3-5 scenarios

---

### Phase C — Cost Monitoring + Telegram Commands (Week 3)

**Goal**: Full observability. Enable Haiku for daily_report. Validate report quality.

**Tasks**: TASK-010, TASK-011

**Deliverables**:
- Daily report shows per-model breakdown + semantic stats
- All 5 Telegram commands operational
- Haiku enabled for daily_report
- Monthly cost tracking active

**TAG Chain**:
```
TASK-007 + TASK-009 → TASK-010 (cost monitoring)
TASK-008 + TASK-007 → TASK-011 (telegram commands)
```

**Verification**: M4-1 through M4-4, M1-3, M1-4, M5-5 scenarios

---

### Phase D — Micro Persona Haiku + Shadow Testing (Week 4)

**Goal**: Enable Haiku for Micro persona with quality gate monitoring. This is the highest-impact change.

**Tasks**: TASK-012

**Deliverables**:
- Shadow testing infrastructure complete
- Haiku enabled for Micro persona
- 5-day shadow testing period active
- Quality gate monitoring (85% overlap threshold)
- Auto-revert on quality degradation

**TAG Chain**:
```
TASK-008 + TASK-009 → TASK-012 (shadow testing)
```

**Verification**: M5-2, M5-3, M5-4, M5-7 scenarios

---

### Phase E — Stabilization (Week 5)

**Goal**: All phases stable. Remove experimental flags. Permanent operation.

**Tasks**: None (operational validation only)

**Deliverables**:
- Quality gate passed for all Haiku-routed personas
- Shadow testing disabled (SHADOW_TEST_ACTIVE=false)
- Monthly cost confirmed <= 100,000 KRW
- All audit_log events verified
- Rollback procedure documented and tested

**Verification**: NFR-1 through NFR-6 scenarios, M5-6 (full rollback test)

---

## 5. Dependency Graph

```
TASK-001 (migration)
├── TASK-002 (config)
│   ├── TASK-003 (chunker)
│   │   └── TASK-005 (indexer) ─── TASK-013 (cron hook)
│   └── TASK-004 (embedder)
│       ├── TASK-005 (indexer)
│       └── TASK-006 (searcher)
│           └── TASK-007 (context enhancement)
│               ├── TASK-010 (cost monitoring)
│               └── TASK-011 (telegram commands)
├── TASK-008 (model router)
│   ├── TASK-009 (orchestrator integration)
│   │   ├── TASK-010 (cost monitoring)
│   │   └── TASK-012 (shadow testing)
│   └── TASK-011 (telegram commands)
```

Critical path: TASK-001 → TASK-002 → TASK-004 → TASK-006 → TASK-007 → TASK-010

---

## 6. Risk Assessment

### Technical Risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Haiku quality insufficient for Micro screening | Medium | High | Quality gate + shadow testing + auto-revert (REQ-MIGR-05-3). 5-day validation before permanent activation. |
| pgvector retrieval misses critical context | Low | Medium | Top-K tuning (default K=7, adjustable 1-20) + automatic fallback to full mode when no embeddings exist |
| Embedding API downtime (Voyage AI) | Low | Low | Graceful fallback to full .md injection; existing behavior restored immediately |
| Monthly cost still exceeds target | Medium | Medium | Operational adjustments (reduce intraday call frequency from 4 to 2 on stable days); Haiku savings are guaranteed regardless |
| Haiku output format failures (JSON parsing) | Low | Low | Retry once with Sonnet fallback for that single call; tracked via format_compliance metric |
| pgvector IVFFlat index inefficiency at small scale | Low | Low | Only ~80 total chunks; index adds minimal overhead; can switch to HNSW if scale grows |

### Compatibility Risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| SPEC-008 cache key invalidation from schema change | Low | Medium | Tool definitions (including new mode/query params) are cached via same `cache_control: ephemeral` pattern; model routing does not affect cache keys |
| SPEC-009 tool-calling interference | Low | Low | Semantic search uses same `get_static_context` tool interface; no new tools needed; Reflection Loop unaffected (Decision/Risk stay on Sonnet) |
| psycopg3 pgvector type registration conflicts | Low | Low | `register_vector` is called once at connection init; isolated from existing query patterns |

### Rollback Safety

- All changes are feature-flag-gated (`SEMANTIC_RETRIEVAL_ENABLED`, `haiku_enabled` per persona)
- Rollback is instant: toggle flags in system_state, no code deployment needed
- `context_embeddings` table preserved during rollback (re-activation instant)
- Migration 011 is additive-only (no destructive schema changes)

---

## 7. Effort Estimate

| Task | Effort | Est. Hours | Phase |
|---|---|---|---|
| TASK-001: Migration 011 | M | 2-3h | A |
| TASK-002: Embedding Config | S | 1h | A |
| TASK-003: Chunker | M | 3-4h | A |
| TASK-004: Embedder | M | 3-4h | A |
| TASK-005: Indexer | M | 3-4h | A |
| TASK-006: Searcher | M | 2-3h | A |
| TASK-007: Context Enhancement | M | 3-4h | B |
| TASK-008: Model Router | M | 3-4h | B |
| TASK-009: Orchestrator Integration | M | 2-3h | B |
| TASK-010: Cost Monitoring | M | 3-4h | C |
| TASK-011: Telegram Commands | M | 3-4h | C |
| TASK-012: Shadow Testing | L | 5-6h | D |
| TASK-013: Cron Integration | S | 1-2h | A |
| **Total** | | **~35-45h** | 5 weeks |

---

## 8. Implementation Notes for DDD Execution

### ANALYZE Phase (per task)

- Read existing code that will be modified
- Identify integration points and side effects
- Map existing test coverage

### PRESERVE Phase (per task)

- Write characterization tests for existing `get_static_context` behavior (TASK-007)
- Write characterization tests for existing orchestrator model selection (TASK-009)
- Write characterization tests for existing daily_report cost calculation (TASK-010)
- New modules (embeddings/) do not need PRESERVE — they are greenfield

### IMPROVE Phase (per task)

- Implement changes with behavior preservation verified by characterization tests
- Target 85%+ coverage for all new code
- Verify backward compatibility at each step

### Key Design Decisions

1. **Model Router caching**: Cache system_state.model_routing for ~60s to avoid per-call DB reads (acceptable staleness for model routing changes which are rare)
2. **Embedding API**: Use httpx directly (no SDK) — consistent with project philosophy
3. **Chunker token counting**: Use 4-chars-per-token heuristic — consistent with existing base.py pattern
4. **Vector dimensions**: Default 1024 (voyage-3); configurable via env var for model switching
5. **IVFFlat lists=20**: Appropriate for ~100 vectors; can tune later if scale grows significantly

---

## 9. Files Modified/Created

### New Files (Phase A)
- `src/trading/db/migrations/011_pgvector_model_routing.sql`
- `src/trading/embeddings/__init__.py`
- `src/trading/embeddings/config.py`
- `src/trading/embeddings/chunker.py`
- `src/trading/embeddings/embedder.py`
- `src/trading/embeddings/indexer.py`
- `src/trading/embeddings/searcher.py`

### New Files (Phase B-D)
- `src/trading/personas/model_router.py`
- `src/trading/personas/shadow_test.py`

### Modified Files
- `src/trading/tools/context_tools.py` — add mode/query/top_k params (TASK-007)
- `src/trading/tools/registry.py` — update get_static_context schema (TASK-007)
- `src/trading/personas/orchestrator.py` — use Model Router (TASK-009)
- `src/trading/personas/base.py` — add Haiku pricing to PRICING_USD_PER_MTOK (TASK-010)
- `src/trading/reports/daily_report.py` — per-model breakdown + semantic stats (TASK-010)
- `src/trading/config.py` — embedding/cost target constants (TASK-002, TASK-010)
- `src/trading/bot/telegram_bot.py` — 5 new commands (TASK-011)
- `src/trading/contexts/build_*.py` — post-build embedding hook (TASK-013)
- `pyproject.toml` — add pgvector dependency (TASK-001)

---

## 10. Approval Checklist

- [ ] Technology stack: pgvector Python package as sole new dependency
- [ ] Implementation order: pgvector foundation first, then semantic retrieval, then model routing
- [ ] 5-phase rollout with feature flags
- [ ] Haiku routing restricted to: Micro (screening), Daily Report, Macro News
- [ ] Quality gate: 85% overlap threshold with auto-revert
- [ ] Embedding model: voyage-3 default (configurable)
- [ ] Monthly cost target: 100,000 KRW
- [ ] Rollback: instant via system_state flags, no code deployment
