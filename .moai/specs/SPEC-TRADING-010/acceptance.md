# SPEC-TRADING-010 Acceptance Criteria

## Module 1 — Model Router

### Scenario M1-1: Basic Model Routing

```gherkin
Given the system_state.model_routing has micro configured as {"model": "claude-haiku-4-5", "haiku_eligible": true, "haiku_enabled": true}
When the orchestrator invokes the Micro persona
Then the Model Router shall resolve the model to "claude-haiku-4-5"
And the persona_runs record shall show model="claude-haiku-4-5"
```

### Scenario M1-2: Haiku Disabled Fallback

```gherkin
Given the system_state.model_routing has micro configured as {"model": "claude-haiku-4-5", "haiku_eligible": true, "haiku_enabled": false}
When the orchestrator invokes the Micro persona
Then the Model Router shall resolve the model to "claude-sonnet-4-6" (fallback)
And the persona_runs record shall show model="claude-sonnet-4-6"
```

### Scenario M1-3: Non-Eligible Persona Protection

```gherkin
Given the system_state.model_routing has decision configured as {"model": "claude-sonnet-4-6", "haiku_eligible": false}
When a Telegram command "/haiku decision on" is received from chat_id=60443392
Then the system shall reject the command
And reply with error message "Decision/Risk personas require Sonnet or higher for quality assurance."
And no audit_log entry MODEL_ROUTING_CHANGED shall be written
```

### Scenario M1-4: Telegram Model Change

```gherkin
Given the system_state.model_routing has micro configured with haiku_enabled=true
When a Telegram command "/haiku micro off" is received from chat_id=60443392
Then the system shall set haiku_enabled=false for micro
And write audit_log event MODEL_ROUTING_CHANGED with details {"persona": "micro", "change": "haiku_enabled: true -> false"}
And confirm via Telegram within 5 seconds
```

### Scenario M1-5: Cost Report Per-Model Breakdown

```gherkin
Given the system has completed 10 persona runs today (3 Opus, 5 Sonnet, 2 Haiku)
When the daily report is generated
Then the cost section shall include per-model breakdown:
  - Opus: 3건 x cost
  - Sonnet: 5건 x cost
  - Haiku: 2건 x cost
And total_cost_today shall equal the sum of all model costs
```

---

## Module 2 — pgvector Semantic Context Retrieval

### Scenario M2-1: Extension and Table Creation

```gherkin
Given the PostgreSQL database is running (postgres:16-alpine)
When migration v11 is applied
Then the vector extension shall be created (CREATE EXTENSION vector)
And the context_embeddings table shall exist with columns: id, source_file, chunk_index, chunk_text, chunk_tokens, embedding (vector(1024)), metadata, created_at, updated_at
And the UNIQUE constraint on (source_file, chunk_index) shall be active
And the ivfflat index on embedding shall be created
```

### Scenario M2-2: Chunking Strategy

```gherkin
Given a macro_context.md file with content containing 4 sections (## Fed Policy, ## Global Assets, ## Korean Market, ## Policy Calendar) totaling ~3500 tokens
When the chunker processes this file
Then it shall produce 12-18 chunks
And each chunk shall be 200-500 tokens (hard limit: 500)
And section headers shall be primary split boundaries
And table rows shall never be split mid-row
And adjacent chunks shall have 50-token overlap
And each chunk shall have metadata containing section_header
```

### Scenario M2-3: Embedding Generation

```gherkin
Given 15 new chunks from macro_context.md
And EMBEDDING_MODEL=voyage-3
When the embedding pipeline processes the chunks
Then it shall call the Voyage AI API with batch size ≤ 50
And generate 15 embeddings of dimension 1024
And rate limit at ≤ 100 requests/second
And log to audit_log: chunks_processed=15, embeddings_generated=15
```

### Scenario M2-4: Incremental Upsert

```gherkin
Given context_embeddings has 15 existing chunks for source_file="macro_context"
And the macro_context.md file is regenerated with 2 chunks changed, 1 added, 1 removed
When the embedding pipeline runs
Then it shall:
  - Generate embeddings for 3 chunks only (2 changed + 1 new)
  - Upsert 3 rows into context_embeddings
  - Delete 1 stale row no longer in the file
  - Leave 12 unchanged rows untouched
And audit_log shall record: upserts=3, deletes=1
```

### Scenario M2-5: Cron Trigger Integration

```gherkin
Given the 06:00 cron job regenerates macro_context.md (SPEC-007 REQ-CTX-01-2)
When the context build completes successfully
Then the embedding pipeline shall be triggered as a post-build hook
And complete within 30 seconds
And update context_embeddings for source_file="macro_context"
```

### Scenario M2-6: Embedding API Failure

```gherkin
Given the Voyage AI API returns HTTP 429 (rate limit)
When the embedder encounters this error
Then it shall apply exponential backoff (1s, 2s, 4s, max 30s)
And retry up to 5 times
And if all retries fail, log error to audit_log
And emit Telegram alert "Embedding pipeline failed: Voyage API rate limit exceeded"
And existing embeddings remain unchanged (no data loss)
```

---

## Module 3 — get_static_context Tool Enhancement

### Scenario M3-1: Semantic Mode Query

```gherkin
Given context_embeddings has 15 chunks for source_file="macro_context"
And SEMANTIC_RETRIEVAL_ENABLED=true
When a persona calls get_static_context(name="macro_context", mode="semantic", query="Fed interest rate impact on Korean won", top_k=7)
Then the system shall:
  1. Generate embedding for the query text
  2. Execute cosine similarity search against chunks where source_file="macro_context"
  3. Return top-7 chunks ordered by similarity descending
And the response shall include: source, mode, query, results (with chunk_index, text, similarity, metadata), total_chunks, returned_chunks, estimated_tokens
And total response tokens shall be ≤ 1500 (vs full file ~3500)
```

### Scenario M3-2: Full Mode Backward Compatibility

```gherkin
Given SEMANTIC_RETRIEVAL_ENABLED=true
When a persona calls get_static_context(name="macro_context", mode="full")
Then the system shall return the entire macro_context.md file content
And behavior shall be identical to SPEC-009 pre-enhancement
```

### Scenario M3-3: No Query Defaults to Full

```gherkin
Given SEMANTIC_RETRIEVAL_ENABLED=true
When a persona calls get_static_context(name="macro_context") without mode or query parameters
Then the system shall default to mode="full"
And return the entire file content (backward compatible)
```

### Scenario M3-4: Cold Start Fallback

```gherkin
Given context_embeddings has 0 rows for source_file="micro_news" (never embedded or embedding failed)
And SEMANTIC_RETRIEVAL_ENABLED=true
When a persona calls get_static_context(name="micro_news", mode="semantic", query="Samsung earnings")
Then the system shall automatically fallback to mode="full"
And return the entire micro_news.md file content
And log audit_log event SEMANTIC_FALLBACK_NO_EMBEDDINGS
```

### Scenario M3-5: Semantic Retrieval Disabled

```gherkin
Given SEMANTIC_RETRIEVAL_ENABLED=false
When a persona calls get_static_context(name="macro_context", mode="semantic", query="...")
Then the system shall ignore mode="semantic"
And return the entire file content (full mode forced)
And NOT generate any embedding queries
```

### Scenario M3-6: Latency SLA

```gherkin
Given context_embeddings has 20 chunks for source_file="micro_context"
When a semantic search is executed
Then the total latency (query embedding + pgvector search + result assembly) shall be ≤ 500ms
And if latency exceeds 500ms, the system shall fallback to full mode for that call
And log audit_log event SEMANTIC_SEARCH_SLOW
```

---

## Module 4 — Cost Monitoring Dashboard

### Scenario M4-1: Extended Daily Report

```gherkin
Given today had 20 persona runs: 1 Opus (5,100원), 8 Sonnet (7,600원), 11 Haiku (2,860원), 500 embeddings (30원)
When the daily report is generated at 16:00
Then the cost section shall include:
  - Today total: 15,590원
  - Per-model breakdown (Opus/Sonnet/Haiku/Embedding)
  - This week cumulative
  - This month cumulative
  - Cache hit rate
  - Semantic retrieval savings percentage
  - Haiku routing savings estimate
```

### Scenario M4-2: Monthly Target Warning

```gherkin
Given this month's cumulative cost is 98,000원
And today's runs will add 3,500원 (total: 101,500원)
When the cost exceeds 100,000원 for the first time this month
Then the system shall emit Telegram warning: "Monthly cost target exceeded: 101,500원 / 100,000원 target"
And this warning shall ignore silent_mode
And this warning shall be sent once per month only
```

### Scenario M4-3: CLI Cost Report

```gherkin
Given the month of 2026-05 has 15 trading days completed
When the user runs "trading cost-report --month 2026-05"
Then the output shall show:
  - Per-persona call counts and costs
  - Per-model distribution (Opus/Sonnet/Haiku/Embedding)
  - Haiku savings estimate: "Haiku routing saves X원 vs all-Sonnet"
  - Semantic savings: "Average Y% token reduction (full: A tok → semantic: B tok)"
  - Monthly total vs target progress
```

### Scenario M4-4: Telegram /cost Command

```gherkin
Given today is May 15 with 5,200원 spent today and 72,000원 this month
When the command "/cost" is received from chat_id=60443392
Then the system shall reply within 5 seconds with:
  "Today: 5,200원 | Month: 72,000원 / 100,000원 (72%)"
```

---

## Module 5 — Migration, Rollback & Quality Gates

### Scenario M5-1: Phase A Deployment

```gherkin
Given SPEC-009 tool-calling is active (TOOL_CALLING_ENABLED=true)
When Phase A is deployed (migration v11 applied)
Then pgvector extension shall be active
And context_embeddings table shall be created
And initial embeddings shall be generated for all 4 .md files
And SEMANTIC_RETRIEVAL_ENABLED shall remain false (not yet active)
And all existing persona invocations shall continue unchanged
```

### Scenario M5-2: Phase D Shadow Testing (Micro Persona)

```gherkin
Given Phase D is active (Haiku enabled for Micro persona)
And shadow testing is enabled for the first 5 trading days
When the Micro persona is invoked during pre-market
Then the system shall:
  1. Run primary invocation with Haiku 4.5
  2. Run shadow invocation with Sonnet 4.6 (same inputs)
  3. Compare top-5 candidates from both outputs
  4. Record overlap_score in shadow_test_results table
And both runs shall be recorded in persona_runs (primary + shadow)
```

### Scenario M5-3: Quality Gate Pass

```gherkin
Given 5 consecutive trading days of shadow testing for Micro persona
And overlap_scores are: [0.88, 0.92, 0.86, 0.90, 0.87]
When the quality gate is evaluated (all scores ≥ 0.85)
Then the quality gate shall PASS
And shadow testing shall be disabled (SHADOW_TEST_ACTIVE=false)
And Haiku routing for Micro shall be permanently active
And audit_log event QUALITY_GATE_PASSED shall be recorded
```

### Scenario M5-4: Quality Gate Failure — Auto Revert

```gherkin
Given shadow testing is active for Micro persona
And overlap_scores for last 3 consecutive days are: [0.82, 0.79, 0.83] (all < 0.85)
When the quality gate is evaluated
Then the system shall:
  1. Set haiku_enabled=false for micro in system_state.model_routing
  2. Write audit_log event QUALITY_GATE_HAIKU_REVERT
  3. Emit Telegram alert: "Micro persona Haiku quality gate failed (avg overlap: 0.81). Auto-reverted to Sonnet."
  4. Stop shadow testing
And subsequent Micro invocations shall use claude-sonnet-4-6
```

### Scenario M5-5: Manual Rollback via Telegram

```gherkin
Given Haiku is active for daily_report
And the user observes quality issues
When the command "/haiku daily_report off" is received
Then haiku_enabled shall be set to false for daily_report
And subsequent daily report invocations shall use claude-sonnet-4-6
And audit_log event MODEL_ROUTING_CHANGED shall be recorded
And the system shall confirm: "Daily report reverted to Sonnet 4.6"
```

### Scenario M5-6: Full Rollback (Semantic + Haiku)

```gherkin
Given both semantic retrieval and Haiku routing are active
When the user decides to rollback all SPEC-010 changes
Then setting SEMANTIC_RETRIEVAL_ENABLED=false reverts all semantic search to full mode
And setting haiku_enabled=false for all eligible personas reverts to Sonnet
And context_embeddings table data is preserved (not deleted)
And the system operates identically to pre-SPEC-010 state
And re-activation is instant by toggling flags back
```

### Scenario M5-7: Telegram Shadow Test Command

```gherkin
Given the user wants to validate Micro persona quality
When the command "/shadow-test micro" is received from chat_id=60443392
Then the system shall set SHADOW_TEST_ACTIVE=true
And the next Micro invocation shall run in dual-model mode
And after completion, report to Telegram:
  "Shadow test complete: Haiku candidates [list], Sonnet candidates [list], Overlap: X%"
And SHADOW_TEST_ACTIVE shall reset to false after one test
```

---

## Non-Functional Requirements

### Scenario NFR-1: Monthly Cost Target

```gherkin
Given all SPEC-010 optimizations are active (Phase E)
When a full month of trading operations completes (~22 trading days)
Then the total monthly Anthropic API cost shall be ≤ 100,000원
And if exceeded for 2 consecutive months, a cost review alert shall be emitted
```

### Scenario NFR-2: Semantic Search Latency

```gherkin
Given context_embeddings has up to 100 chunks across all source_files
When any semantic search query is executed
Then total latency (embedding generation + pgvector query + response assembly) shall be ≤ 500ms
And the 95th percentile latency shall be ≤ 400ms
```

### Scenario NFR-3: Embedding Pipeline Performance

```gherkin
Given a .md file changes and requires re-embedding
When the embedding pipeline processes the file (~20-30 chunks)
Then it shall complete within 30 seconds
And run as a background task that does not block persona invocations
```

### Scenario NFR-4: Haiku Output Compliance

```gherkin
Given the Micro persona is routed to Haiku 4.5
When 100 consecutive invocations are completed
Then structured JSON parsing success rate shall be ≥ 99%
And all required output fields (candidates, rationale, signals) shall be present in ≥ 99% of responses
```

### Scenario NFR-5: Storage Overhead

```gherkin
Given 4 .md files with ~80 total chunks embedded (1024 dimensions each)
When storage is measured
Then total pgvector storage shall be ≤ 100MB
And storage growth shall be < 1MB/month under normal operations
```

### Scenario NFR-6: Observability Coverage

```gherkin
Given any of the following events occurs:
  - Model routing decision
  - Embedding pipeline run
  - Semantic search invocation
  - Quality gate evaluation
  - Haiku auto-revert
When the event completes
Then an audit_log entry shall be created with appropriate event_type
And sufficient details for post-mortem analysis shall be included
```

---

## Definition of Done

- [ ] All Module 1 scenarios (M1-1 through M1-5) passing
- [ ] All Module 2 scenarios (M2-1 through M2-6) passing
- [ ] All Module 3 scenarios (M3-1 through M3-6) passing
- [ ] All Module 4 scenarios (M4-1 through M4-4) passing
- [ ] All Module 5 scenarios (M5-1 through M5-7) passing
- [ ] All NFR scenarios (NFR-1 through NFR-6) passing
- [ ] Migration v11 applied and verified
- [ ] Phase A through Phase E rollout plan documented
- [ ] Quality gate threshold (85% overlap) validated via shadow testing
- [ ] Monthly cost ≤ 100,000원 achieved in first full month
- [ ] All new code has ≥ 85% test coverage
- [ ] All audit_log events fire correctly
- [ ] Telegram commands (/model, /haiku, /shadow-test, /cost, /semantic) functional
- [ ] Rollback procedure tested and documented
