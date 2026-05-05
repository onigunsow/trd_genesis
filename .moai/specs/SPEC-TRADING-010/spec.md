---
id: SPEC-TRADING-010
version: 0.1.0
status: draft
created: 2026-05-05
updated: 2026-05-05
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "Cost Optimization Phase 2 — Haiku Hybrid Routing + pgvector Semantic Context Retrieval"
related_specs:
  - SPEC-TRADING-001
  - SPEC-TRADING-007
  - SPEC-TRADING-008
  - SPEC-TRADING-009
---

# SPEC-TRADING-010 — Cost Optimization Phase 2

## HISTORY

| Date | Version | Change | Author |
|---|---|---|---|
| 2026-05-05 | 0.1.0 | Draft — Haiku Hybrid Routing + pgvector Semantic Context Retrieval (5 modules) | onigunsow |

## Scope Summary

SPEC-TRADING-008 (Phase A) applied Anthropic Prompt Caching to reduce monthly cost from ~30-50만원 to ~18-30만원. SPEC-TRADING-009 introduced Tool-calling Active Retrieval which further reduces token usage by ~80% per persona invocation through on-demand data fetching. This SPEC takes cost optimization to its final target: **monthly cost ≤ 10만원**.

Two complementary strategies:

1. **Haiku Hybrid Model Routing** — Route low-reasoning tasks (Micro screening, Daily Report, Macro News summary) to Claude Haiku 4.5 at ~73% lower cost per token, while preserving Sonnet 4.6/Opus 4.7 for decision-critical personas (Decision, Risk, Macro, Retrospective).
2. **pgvector Semantic Context Retrieval** — Replace full `.md` file injection with embedding-based semantic search. The SPEC-009 `get_static_context` tool currently returns entire `.md` files; this SPEC enhances it to return only the top-K most relevant chunks via pgvector cosine similarity, further reducing per-tool-call token count.

**Key Principle**: Cost reduction must NEVER degrade Decision/Risk persona quality. Quality gates auto-revert Haiku routing if degradation is detected.

### Cost Projection

| Optimization Layer | Monthly Cost | Cumulative Savings |
|---|---|---|
| Baseline (M5+ full operation) | ~40-50만원 | — |
| After SPEC-008 (Prompt Caching) | ~18-30만원 | ~40% |
| After SPEC-009 (Tool-calling) | ~12-20만원 (estimated) | ~55-60% |
| After SPEC-010 (Haiku + pgvector) | **≤ 10만원** (target) | ~75-80% |

---

## Environment

- Existing SPEC-TRADING-001 infrastructure — Postgres 16-alpine, Anthropic API, Telegram, Docker compose
- SPEC-009 Tool-calling architecture active (`TOOL_CALLING_ENABLED=true` in `system_state`)
- SPEC-008 Prompt Caching active (`cache_control: ephemeral` on system prompts and tool definitions)
- PostgreSQL 16-alpine supports `pgvector` extension (`CREATE EXTENSION vector;`)
- Anthropic API supports `claude-haiku-4-5` model alongside existing `claude-sonnet-4-6` and `claude-opus-4-7`
- Embedding model configurable: Voyage AI `voyage-3` (Anthropic partnership) OR OpenAI `text-embedding-3-small` — user preference TBD at implementation time, architecture supports both
- Existing `get_static_context` tool (SPEC-009 REQ-TOOL-01-2) returns full `.md` content — this SPEC adds semantic search mode

## Assumptions

1. Claude Haiku 4.5 (`claude-haiku-4-5`) maintains sufficient quality for screening/aggregation tasks (candidate selection, report formatting, news summarization) during the operation period of this SPEC. Pricing: input $0.80/M tok, output $4/M tok (vs Sonnet: $3/$15).
2. pgvector extension is available in `postgres:16-alpine` Docker image (confirmed: `CREATE EXTENSION IF NOT EXISTS vector;` works without additional image modification).
3. Embedding model API (Voyage AI or OpenAI) is stable and priced below $0.10/M tokens, making embedding costs negligible (~10K embeddings/month < $1).
4. Context chunk size of 200-500 tokens provides sufficient semantic granularity for financial market data retrieval.
5. Top-K retrieval (K=5-10) provides equivalent or better context quality compared to full `.md` injection, since irrelevant sections currently waste ~60-70% of injected tokens.
6. Haiku 4.5 for Micro persona screening produces candidate lists with ≥ 90% overlap with Sonnet 4.6 baseline (quality gate threshold).
7. The combined per-call cost reduction (Haiku model + pgvector reduced tokens) achieves the ≤ 10만원/month target.
8. SPEC-009 `get_static_context` tool interface is stable and extensible with an additional `mode` parameter.

## Robustness Principles (SPEC-001 6-Principle Inheritance)

This SPEC inherits SPEC-TRADING-001 v0.2.0's 6 Robustness Principles:

- **External dependency failure assumption (Principle 1)** — Embedding API failure: fallback to full `.md` injection. Haiku API error: fallback to Sonnet.
- **Failure silence prohibition (Principle 3)** — Model routing fallback, embedding failures, quality gate triggers all emit audit_log + Telegram.
- **Auto-recovery with human notification (Principle 4)** — Quality gate violation triggers auto-revert to Sonnet + human notification.
- **Tests harden the specification (Principle 5)** — Model router, embedding pipeline, quality gate modules require 85%+ coverage.

---

## Requirements (EARS)

EARS notation: **U**=Ubiquitous, **E**=Event-driven, **S**=State-driven, **O**=Optional, **N**=Unwanted

---

### Module 1 — Model Router (Per-Persona Model Selection)

**REQ-ROUTER-01-1 [U]** The system shall implement a Model Router under `src/trading/personas/model_router.py` that determines the LLM model for each persona invocation based on a per-persona configuration stored in the `system_state` table.

**REQ-ROUTER-01-2 [U]** The system shall define the following model routing configuration:

| Persona | Default Model | Haiku-Eligible | Rationale |
|---|---|---|---|
| Macro | `claude-opus-4-7` | No | Deep macro analysis requires Opus reasoning |
| Micro (screening) | `claude-haiku-4-5` | **Yes** | Candidate selection only; Decision makes final call |
| Decision (Park Sehoon) | `claude-sonnet-4-6` | No | Core decision-making quality critical |
| Risk | `claude-sonnet-4-6` | No | Validation accuracy critical for SoD |
| Portfolio (M5+) | `claude-sonnet-4-6` | No | Size adjustment requires nuanced judgment |
| Retrospective | `claude-sonnet-4-6` | No | Strategic quality for meta-learning |
| Daily Report | `claude-haiku-4-5` | **Yes** | Aggregation + formatting, no reasoning |
| Macro News (RSS summary) | `claude-haiku-4-5` | **Yes** | Simple summarization of RSS feeds |

**REQ-ROUTER-01-3 [U]** The model routing configuration shall be stored in `system_state` as a JSONB column `model_routing` with the following schema:

```json
{
  "macro": {"model": "claude-opus-4-7", "haiku_eligible": false},
  "micro": {"model": "claude-haiku-4-5", "haiku_eligible": true, "haiku_enabled": true},
  "decision": {"model": "claude-sonnet-4-6", "haiku_eligible": false},
  "risk": {"model": "claude-sonnet-4-6", "haiku_eligible": false},
  "portfolio": {"model": "claude-sonnet-4-6", "haiku_eligible": false},
  "retrospective": {"model": "claude-sonnet-4-6", "haiku_eligible": false},
  "daily_report": {"model": "claude-haiku-4-5", "haiku_eligible": true, "haiku_enabled": true},
  "macro_news": {"model": "claude-haiku-4-5", "haiku_eligible": true, "haiku_enabled": true}
}
```

**REQ-ROUTER-01-4 [E]** When the orchestrator invokes any persona, the Model Router shall resolve the target model by:
1. Reading the persona's routing config from `system_state.model_routing`
2. If `haiku_eligible=true` AND `haiku_enabled=true` → use configured `model`
3. If `haiku_eligible=true` AND `haiku_enabled=false` → fallback to `claude-sonnet-4-6`
4. If `haiku_eligible=false` → use configured `model` (Sonnet or Opus)

**REQ-ROUTER-01-5 [E]** When the Telegram command `/model <persona> <model>` is received from `chat_id=60443392`, the system shall update `system_state.model_routing` for the specified persona, write `audit_log` event `MODEL_ROUTING_CHANGED`, and confirm via Telegram within 5 seconds.

**REQ-ROUTER-01-6 [E]** When the Telegram command `/haiku <persona> on|off` is received from `chat_id=60443392`, the system shall toggle `haiku_enabled` for the specified persona (only if `haiku_eligible=true`), write `audit_log`, and confirm.

**REQ-ROUTER-01-7 [N]** The system shall NOT allow Haiku routing for Decision or Risk personas via any command. Attempts to set `haiku_enabled=true` for non-eligible personas shall be rejected with Telegram error message: "Decision/Risk personas require Sonnet or higher for quality assurance."

**REQ-ROUTER-01-8 [U]** The `persona_runs` table shall record the actual model used per invocation in the existing `model` column. The daily report cost section shall show per-model breakdown (Opus/Sonnet/Haiku call counts and costs).

---

### Module 2 — pgvector Semantic Context Retrieval

**REQ-PGVEC-02-1 [U]** The system shall enable the `pgvector` extension in PostgreSQL via migration: `CREATE EXTENSION IF NOT EXISTS vector;`.

**REQ-PGVEC-02-2 [U]** The system shall create a `context_embeddings` table:

```sql
CREATE TABLE context_embeddings (
    id BIGSERIAL PRIMARY KEY,
    source_file TEXT NOT NULL,        -- e.g., 'macro_context', 'micro_context'
    chunk_index INTEGER NOT NULL,     -- sequential order within file
    chunk_text TEXT NOT NULL,          -- 200-500 token chunk
    chunk_tokens INTEGER NOT NULL,    -- token count
    embedding vector(1024) NOT NULL,  -- 1024-dim for voyage-3; configurable
    metadata JSONB,                   -- {section_header, tickers_mentioned, date_range}
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(source_file, chunk_index)
);

CREATE INDEX idx_context_embeddings_source ON context_embeddings(source_file);
CREATE INDEX idx_context_embeddings_vector ON context_embeddings
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20);
```

**REQ-PGVEC-02-3 [U]** The system shall implement an embedding pipeline under `src/trading/embeddings/` with:
- `chunker.py` — Split `.md` files into 200-500 token chunks with overlap (50 tokens)
- `embedder.py` — Generate embeddings via configured model (Voyage AI or OpenAI)
- `indexer.py` — Upsert chunks + embeddings into `context_embeddings` table
- `searcher.py` — Cosine similarity search: given query text, return top-K chunks

**REQ-PGVEC-02-4 [U]** The embedding model shall be configurable via environment variable `EMBEDDING_MODEL` with supported values:
- `voyage-3` (Anthropic/Voyage AI, 1024 dimensions, $0.06/M tokens)
- `text-embedding-3-small` (OpenAI, 1536 dimensions, $0.02/M tokens)

The `context_embeddings.embedding` vector dimension shall match the configured model. Default: `voyage-3`.

**REQ-PGVEC-02-5 [E]** When any Static Context `.md` file is regenerated (SPEC-007 REQ-CTX-01-2/3/4/5 cron triggers at 06:00/06:30/06:45/Fri 16:30), the embedding pipeline shall:
1. Re-chunk the updated `.md` file
2. Generate embeddings for new/changed chunks
3. Upsert into `context_embeddings` (unchanged chunks skip re-embedding)
4. Delete stale chunks no longer present in the updated file

**REQ-PGVEC-02-6 [U]** The chunking strategy shall:
- Split on section headers (`## `, `### `) as primary boundaries
- Within sections, split on double newlines or at 400 tokens (hard limit: 500)
- Maintain 50-token overlap between adjacent chunks for continuity
- Preserve table rows as atomic units (never split mid-table-row)
- Attach metadata: `{section_header, tickers_mentioned[], date_range}`

**REQ-PGVEC-02-7 [U]** Embedding generation shall enforce a rate limit of 100 requests/second (Voyage AI default) with exponential backoff on 429 responses. Batch size: 50 chunks per API call.

**REQ-PGVEC-02-8 [N]** The system shall NOT store raw API keys for embedding models in the database. Use environment variables only: `VOYAGE_API_KEY` or `OPENAI_API_KEY`.

**REQ-PGVEC-02-9 [U]** The system shall log embedding pipeline runs to `audit_log`: chunks_processed, embeddings_generated, upserts, deletes, total_time_ms, embedding_cost_usd.

---

### Module 3 — get_static_context Tool Enhancement (Semantic Search Mode)

**REQ-SCTX-03-1 [U]** The existing `get_static_context` tool (SPEC-009 REQ-TOOL-01-2) shall be enhanced with an optional `mode` parameter:

```json
{
  "name": "get_static_context",
  "description": "Load static market context. Use mode='full' for entire file, mode='semantic' for relevant chunks only.",
  "input_schema": {
    "type": "object",
    "properties": {
      "name": {"type": "string", "enum": ["macro_context", "micro_context", "macro_news", "micro_news"]},
      "mode": {"type": "string", "enum": ["full", "semantic"], "default": "semantic"},
      "query": {"type": "string", "description": "Search query for semantic mode (required when mode=semantic)"},
      "top_k": {"type": "integer", "default": 7, "minimum": 1, "maximum": 20}
    },
    "required": ["name"]
  }
}
```

**REQ-SCTX-03-2 [E]** When `mode="semantic"` AND `query` is provided, the system shall:
1. Generate an embedding for the query text using the configured embedding model
2. Execute cosine similarity search against `context_embeddings` filtered by `source_file=name`
3. Return the top-K chunks ordered by similarity score (descending)
4. Include similarity score and chunk metadata in the response

**REQ-SCTX-03-3 [E]** When `mode="full"` OR `mode` is omitted AND `query` is not provided, the system shall return the entire `.md` file content (backward-compatible with SPEC-009 behavior).

**REQ-SCTX-03-4 [S]** While `context_embeddings` table has no embeddings for the requested `source_file` (cold start or embedding failure), the system shall automatically fallback to `mode="full"` and log `audit_log` event `SEMANTIC_FALLBACK_NO_EMBEDDINGS`.

**REQ-SCTX-03-5 [U]** The persona system prompts shall be updated to instruct use of semantic mode: *"get_static_context 호출 시 mode='semantic'을 사용하고, 현재 분석 맥락에 맞는 query를 작성하세요. 전체 파일이 필요한 경우에만 mode='full'을 사용하세요."*

**REQ-SCTX-03-6 [U]** Semantic search response format:

```json
{
  "source": "macro_context",
  "mode": "semantic",
  "query": "Fed rate decision impact on Korean market",
  "results": [
    {
      "chunk_index": 3,
      "text": "... chunk content ...",
      "similarity": 0.87,
      "metadata": {"section_header": "Fed Policy", "tickers_mentioned": [], "date_range": "2026-04-29~05-05"}
    }
  ],
  "total_chunks": 42,
  "returned_chunks": 7,
  "estimated_tokens": 1200
}
```

**REQ-SCTX-03-7 [U]** The daily report shall include a semantic retrieval summary: `"Semantic Context: 호출 X건, 평균 top-K Y건, 평균 토큰 Z tok (vs full injection 대비 W% 절감)"`.

---

### Module 4 — Cost Monitoring Dashboard Enhancement

**REQ-COST-04-1 [U]** The existing cost monitoring (SPEC-008 REQ-COSTM-03-1) shall be extended with per-model cost breakdown:

```
[Cost Report]
Today: 4,200원
  Opus:   1건 × 5,100원 = 5,100원
  Sonnet: 8건 × 950원 = 7,600원
  Haiku:  12건 × 260원 = 3,120원
  Embedding: 420건 × 0.3원 = 126원
This week: 28,500원
This month: 92,300원 (target: ≤ 100,000원)
Cache hit rate: 72%
Semantic retrieval savings: 65% token reduction
```

**REQ-COST-04-2 [U]** The system shall calculate per-model cost using the following rates (configurable in `config.py`):

| Model | Input ($/M tok) | Output ($/M tok) | Cache Read ($/M tok) |
|---|---|---|---|
| claude-opus-4-7 | $15.00 | $75.00 | $1.50 |
| claude-sonnet-4-6 | $3.00 | $15.00 | $0.30 |
| claude-haiku-4-5 | $0.80 | $4.00 | $0.08 |
| voyage-3 (embedding) | $0.06 | — | — |
| text-embedding-3-small | $0.02 | — | — |

**REQ-COST-04-3 [E]** When monthly cumulative cost exceeds 100,000원 (target threshold), the system shall emit a Telegram warning: "Monthly cost target exceeded: {current}원 / 100,000원 target". This supplements SPEC-008 REQ-COSTM-03-2 (200,000원 hard limit warning).

**REQ-COST-04-4 [U]** The CLI command `trading cost-report` (SPEC-008 REQ-COSTM-03-3) shall additionally show:
- Per-model call distribution (Opus/Sonnet/Haiku/Embedding counts)
- Haiku savings estimate: `"Haiku 라우팅 절감: X원 (Sonnet 사용 시 Y원 → Haiku Z원)"`
- Semantic retrieval savings: `"Semantic 절감: 평균 W% 토큰 감소 (full: A tok → semantic: B tok)"`

**REQ-COST-04-5 [E]** When the Telegram command `/cost` is received from `chat_id=60443392`, the system shall reply with today's cost + this month's cumulative + target progress percentage.

---

### Module 5 — Migration, Rollback & Quality Gates

**REQ-MIGR-05-1 [U]** The migration shall be phased:
- **Phase A** (Week 1): Deploy pgvector + embedding pipeline + context_embeddings table. Generate initial embeddings for all 4 `.md` files. Semantic mode available but not default. Feature flag `SEMANTIC_RETRIEVAL_ENABLED=false`.
- **Phase B** (Week 2): Enable Haiku routing for `macro_news` only (lowest risk: weekly summary). Enable `SEMANTIC_RETRIEVAL_ENABLED=true`. Monitor quality + cost.
- **Phase C** (Week 3): Enable Haiku for `daily_report`. Validate report quality against Sonnet baseline.
- **Phase D** (Week 4): Enable Haiku for `micro` persona (screening). Activate quality gate monitoring. This is the highest-impact change.
- **Phase E** (Week 5): All phases stable. Remove experimental flags. Permanent operation.

**REQ-MIGR-05-2 [U]** Quality Gate for Micro persona (Haiku routing):
- **Metric**: Candidate overlap rate between Haiku-generated candidates and a Sonnet 4.6 shadow-run
- **Threshold**: ≥ 85% overlap (매수/매도/관망 top-5 candidates)
- **Measurement**: During Phase D first 5 trading days, run BOTH Haiku (primary) and Sonnet (shadow) for Micro persona. Compare outputs.
- **Cost of shadow**: ~5 days × Sonnet cost for Micro ≈ 5,000원 (acceptable validation cost)

**REQ-MIGR-05-3 [E]** When Micro persona quality gate fails (overlap < 85% for 3 consecutive trading days), the system shall:
1. Auto-revert `micro` routing to `claude-sonnet-4-6`
2. Set `haiku_enabled=false` for micro in `system_state.model_routing`
3. Write `audit_log` event `QUALITY_GATE_HAIKU_REVERT`
4. Emit Telegram alert: "Micro persona Haiku quality gate failed. Auto-reverted to Sonnet."

**REQ-MIGR-05-4 [E]** When the Telegram command `/shadow-test <persona>` is received, the system shall run the next invocation of that persona in dual-model mode (primary + shadow) and report comparison results.

**REQ-MIGR-05-5 [U]** Rollback procedure (any phase):
- Set `haiku_enabled=false` for affected personas via `/haiku <persona> off`
- Set `SEMANTIC_RETRIEVAL_ENABLED=false` via `system_state` update
- System immediately reverts to Sonnet + full `.md` injection
- No data loss: `context_embeddings` table remains for re-activation

**REQ-MIGR-05-6 [N]** The system shall NOT delete the embedding pipeline or `context_embeddings` data during rollback. Rollback only disables semantic mode; re-activation is instant.

**REQ-MIGR-05-7 [U]** Feature flags in `system_state`:
- `SEMANTIC_RETRIEVAL_ENABLED` (BOOLEAN, default: false) — Controls semantic vs full mode
- `model_routing` (JSONB) — Per-persona model configuration (REQ-ROUTER-01-3)
- `SHADOW_TEST_ACTIVE` (BOOLEAN, default: false) — Controls dual-model testing

---

### Non-Functional Requirements

**REQ-NFR-10-1 [U, Cost]** Monthly API cost after full activation (Phase E) shall be ≤ 100,000원. If exceeded for 2 consecutive months, trigger cost review alert.

**REQ-NFR-10-2 [U, Performance]** Semantic search latency (query embedding + pgvector search + result assembly) shall be ≤ 500ms per call. If exceeded, fallback to full mode for that call.

**REQ-NFR-10-3 [U, Performance]** Embedding pipeline (re-chunk + embed + upsert) for a single `.md` file shall complete in ≤ 30 seconds. Run as background task during cron context builds.

**REQ-NFR-10-4 [U, Quality]** Haiku-routed personas shall produce outputs that pass existing validation logic (structured JSON parsing, required fields, signal format) with ≥ 99% success rate.

**REQ-NFR-10-5 [U, Storage]** pgvector storage overhead shall be ≤ 100MB for the projected embedding volume (~2000 chunks × 4 files × 1024 dimensions × 4 bytes/float = ~32MB vectors + metadata).

**REQ-NFR-10-6 [U, Observability]** All model routing decisions, embedding pipeline runs, semantic search invocations, and quality gate evaluations shall be logged to `audit_log` with appropriate event types.

---

## Specifications (Implementation Summary)

### DB Schema Changes (Migration v11)

```sql
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Context embeddings table
CREATE TABLE context_embeddings (
    id BIGSERIAL PRIMARY KEY,
    source_file TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    chunk_tokens INTEGER NOT NULL,
    embedding vector(1024) NOT NULL,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(source_file, chunk_index)
);
CREATE INDEX idx_context_embeddings_source ON context_embeddings(source_file);
CREATE INDEX idx_context_embeddings_vector ON context_embeddings
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20);

-- Model routing in system_state
ALTER TABLE system_state ADD COLUMN model_routing JSONB NOT NULL DEFAULT '{
  "macro": {"model": "claude-opus-4-7", "haiku_eligible": false},
  "micro": {"model": "claude-haiku-4-5", "haiku_eligible": true, "haiku_enabled": true},
  "decision": {"model": "claude-sonnet-4-6", "haiku_eligible": false},
  "risk": {"model": "claude-sonnet-4-6", "haiku_eligible": false},
  "portfolio": {"model": "claude-sonnet-4-6", "haiku_eligible": false},
  "retrospective": {"model": "claude-sonnet-4-6", "haiku_eligible": false},
  "daily_report": {"model": "claude-haiku-4-5", "haiku_eligible": true, "haiku_enabled": true},
  "macro_news": {"model": "claude-haiku-4-5", "haiku_eligible": true, "haiku_enabled": true}
}'::jsonb;

-- Semantic retrieval feature flag
ALTER TABLE system_state ADD COLUMN semantic_retrieval_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE system_state ADD COLUMN shadow_test_active BOOLEAN NOT NULL DEFAULT false;

-- Shadow test results
CREATE TABLE shadow_test_results (
    id BIGSERIAL PRIMARY KEY,
    persona TEXT NOT NULL,
    primary_model TEXT NOT NULL,
    shadow_model TEXT NOT NULL,
    primary_run_id BIGINT REFERENCES persona_runs(id),
    shadow_run_id BIGINT REFERENCES persona_runs(id),
    overlap_score REAL,
    quality_assessment JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### New Module Structure

```
src/trading/embeddings/
├── __init__.py
├── chunker.py           # Split .md files into semantic chunks
├── embedder.py          # Generate embeddings (Voyage AI / OpenAI)
├── indexer.py           # Upsert chunks + embeddings to pgvector
├── searcher.py          # Cosine similarity search
└── config.py            # Embedding model configuration

src/trading/personas/
├── model_router.py      # Per-persona model resolution (NEW)
└── shadow_test.py       # Dual-model comparison testing (NEW)
```

### Modified Modules

- `src/trading/personas/base.py` — `call_persona()` uses Model Router to resolve model
- `src/trading/personas/orchestrator.py` — Passes persona name to Model Router before each invocation
- `src/trading/tools/context_tools.py` — `get_static_context` gains `mode`, `query`, `top_k` parameters
- `src/trading/contexts/build_*.py` — Post-build hook triggers embedding pipeline
- `src/trading/reports/daily_report.py` — Extended cost section with per-model breakdown + semantic stats
- `src/trading/bot/telegram_bot.py` — `/model`, `/haiku`, `/shadow-test`, `/cost` commands
- `src/trading/config.py` — Haiku/Embedding pricing constants, `EMBEDDING_MODEL` env var

### Compatibility with SPEC-008 + SPEC-009

- Prompt Caching (SPEC-008): Tool definitions including enhanced `get_static_context` schema remain cached. Model routing does not affect cache keys (system prompt per persona is model-agnostic).
- Tool-calling (SPEC-009): Semantic search is accessed via the same `get_static_context` tool — LLM decides whether to use `mode="semantic"` or `mode="full"`. No new tools needed.
- Reflection Loop (SPEC-009): Works identically regardless of model — Reflection Loop always uses Decision (Sonnet) + Risk (Sonnet) which are unaffected by Haiku routing.

### Dependent SPECs

This SPEC requires:
- SPEC-TRADING-009 (Tool-calling) — `get_static_context` tool must be implemented before adding semantic mode
- SPEC-TRADING-008 (Prompt Caching) — Cache infrastructure must be active
- SPEC-TRADING-007 (Static Context) — `.md` files and cron builds must be operational

---

## Traceability

| REQ ID | Module | Implementation Location (planned) | Verification (acceptance.md) |
|---|---|---|---|
| REQ-ROUTER-01-1~8 | M1 (Model Router) | `src/trading/personas/model_router.py`, `config.py` | M1 scenarios |
| REQ-PGVEC-02-1~9 | M2 (pgvector) | `src/trading/embeddings/*`, `db/migrations/011_*` | M2 scenarios |
| REQ-SCTX-03-1~7 | M3 (Semantic Context) | `src/trading/tools/context_tools.py`, `embeddings/searcher.py` | M3 scenarios |
| REQ-COST-04-1~5 | M4 (Cost Monitoring) | `src/trading/reports/daily_report.py`, `bot/telegram_bot.py` | M4 scenarios |
| REQ-MIGR-05-1~7 | M5 (Migration) | `src/trading/personas/shadow_test.py`, `system_state` | M5 scenarios |
| REQ-NFR-10-1~6 | Cross-cutting | All modules | NFR scenarios |

---

## Future Scope (Out of Scope for SPEC-TRADING-010)

- **Dynamic model routing based on task complexity** — LLM-assessed complexity score determines Haiku vs Sonnet per-invocation (currently per-persona static)
- **Embedding model fine-tuning** — Train domain-specific embedding model on financial Korean text for improved retrieval
- **Batch embedding** — Use Anthropic/Voyage Batch API for overnight re-indexing at reduced cost
- **Cross-document semantic search** — Query across all 4 `.md` files simultaneously (currently per-file)
- **Memory embeddings** — Embed Dynamic Memory rows (SPEC-007) into pgvector for semantic memory retrieval
- **Haiku for Portfolio persona** — If Haiku proves reliable for Micro, evaluate for Portfolio (currently excluded)
- **Real-time cost optimization agent** — Auto-adjust model routing based on real-time cost trajectory vs target
