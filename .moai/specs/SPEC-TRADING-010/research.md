# SPEC-TRADING-010 Research — Cost Optimization Phase 2

## Research Basis

### Academic Foundation

This SPEC draws from three key research sources identified via NotebookLM analysis:

#### 1. "Economics of Context" Paper — Hierarchical Memory with Semantic Search

**Key Insight**: The most cost-efficient approach to LLM context management is hierarchical memory with semantic search retrieval, NOT full context injection.

**Application to SPEC-010**:
- Current state: SPEC-007 injects full `.md` files (3000-5000 tokens each) into every persona call
- Problem: ~60-70% of injected tokens are irrelevant to the specific analysis being performed
- Solution: pgvector embedding + cosine similarity retrieval returns only the top-K relevant chunks
- Expected token reduction: 60-70% per `get_static_context` call

**Cost-efficiency hierarchy** (from paper):
1. No context (cheapest, worst quality)
2. Full context injection (expensive, medium quality — irrelevant noise hurts)
3. Semantic retrieval (moderate cost, best quality — focused relevant context)

#### 2. AlphaQuanter Paper — Small Language Model at Fraction of Cost

**Key Insight**: A 7B parameter SLM (Small Language Model) achieves 0.15x cost of full-size model for screening/candidate selection tasks, with minimal quality degradation when the final decision is made by a larger model.

**Application to SPEC-010**:
- Haiku 4.5 pricing is ~0.27x of Sonnet 4.6 (input: $0.80/$3.00, output: $4/$15)
- This maps to AlphaQuanter's principle: use cheap model for screening, expensive model for decisions
- Micro persona (screening) → Haiku: generates candidate list, does NOT make final trading decisions
- Decision persona (Sonnet) → Makes the actual buy/sell decision based on Micro's candidates
- Risk persona (Sonnet) → Validates Decision's signal independently
- This is essentially a 2-tier model architecture: screening tier (Haiku) + decision tier (Sonnet/Opus)

**Critical difference from AlphaQuanter**:
- AlphaQuanter uses a fine-tuned 7B SLM — we cannot fine-tune Haiku
- Our mitigation: quality gate with shadow testing (REQ-MIGR-05-2) ensures Haiku output quality is validated before permanent activation

#### 3. Multi-Agent Cost Optimization Patterns

**Key Insight**: In multi-agent systems, not all agents require the same model capability. Routing based on task complexity achieves near-optimal quality at significantly reduced cost.

**Relevant patterns**:
- **Task decomposition**: Break complex tasks into simple subtasks routed to cheaper models
- **Verification layers**: Cheap model proposes, expensive model validates (our Micro→Decision→Risk chain)
- **Quality gates with rollback**: Continuous quality monitoring with automatic degradation detection

---

## Technical Analysis

### Current Cost Breakdown (Post SPEC-008 + SPEC-009)

Estimated monthly costs after Tool-calling implementation:

| Persona | Calls/month | Avg tokens/call | Model | Est. cost/call | Monthly |
|---|---|---|---|---|---|
| Macro | 4 | ~8000 in + 2000 out | Opus 4.7 | ~4,500원 | ~18,000원 |
| Micro (pre-market) | 22 | ~3000 in + 1500 out | Sonnet 4.6 | ~1,100원 | ~24,200원 |
| Micro (intraday cache) | 88 | ~1500 in + 500 out | Sonnet 4.6 | ~400원 | ~35,200원 |
| Decision | 132 (22×6) | ~2000 in + 800 out | Sonnet 4.6 | ~600원 | ~79,200원 |
| Risk | 132 | ~1500 in + 400 out | Sonnet 4.6 | ~350원 | ~46,200원 |
| Portfolio | 22 | ~1500 in + 500 out | Sonnet 4.6 | ~400원 | ~8,800원 |
| Retrospective | 4 | ~3000 in + 1500 out | Sonnet 4.6 | ~1,100원 | ~4,400원 |
| Daily Report | 22 | ~2000 in + 1000 out | Sonnet 4.6 | ~700원 | ~15,400원 |
| Macro News | 4 | ~2000 in + 800 out | Sonnet 4.6 | ~500원 | ~2,000원 |
| Event triggers | ~50 | ~1500 in + 500 out | Sonnet 4.6 | ~400원 | ~20,000원 |
| **Total** | | | | | **~253,400원** |

Note: These estimates assume SPEC-009 reduces tokens by ~80% from SPEC-007 baseline, plus SPEC-008 cache hits (~40% savings on cacheable portions). Actual post-SPEC-009 cost may be lower.

### Projected Cost After SPEC-010

| Persona | Model Change | Token Change | New cost/call | Monthly |
|---|---|---|---|---|
| Macro | Opus (keep) | Semantic -30% | ~3,800원 | ~15,200원 |
| Micro (pre-market) | **Haiku** | Semantic -50% | ~160원 | ~3,520원 |
| Micro (intraday) | **Haiku** | Semantic -50% | ~60원 | ~5,280원 |
| Decision | Sonnet (keep) | Semantic -30% | ~450원 | ~59,400원 |
| Risk | Sonnet (keep) | Minimal change | ~330원 | ~43,560원 |
| Portfolio | Sonnet (keep) | Semantic -30% | ~300원 | ~6,600원 |
| Retrospective | Sonnet (keep) | Minimal | ~1,050원 | ~4,200원 |
| Daily Report | **Haiku** | Semantic -40% | ~100원 | ~2,200원 |
| Macro News | **Haiku** | N/A (RSS input) | ~80원 | ~320원 |
| Event triggers | Sonnet (keep) | Semantic -30% | ~300원 | ~15,000원 |
| Embedding costs | — | ~10K/month | — | ~800원 |
| **Total** | | | | **~156,080원** |

**Gap analysis**: ~156K vs target 100K. The above is a conservative estimate. Additional savings from:
- Cache hit rate improvement (semantic queries are more cacheable): -15%
- Intraday Micro runs can be further reduced (currently 88/month): operational adjustment
- Haiku costs will likely be even lower in practice (shorter outputs for screening)

**Realistic target**: With operational optimizations (reduce intraday calls from 4 to 2 when market is stable), target ~100K is achievable.

### pgvector Architecture Design

#### Chunking Strategy

```
macro_context.md (~3500 tokens, ~15 chunks)
├── Section: "Fed Policy" → chunks 1-3
├── Section: "Global Assets" → chunks 4-7
├── Section: "Korean Market" → chunks 8-11
└── Section: "Policy Calendar" → chunks 12-15

micro_context.md (~5000 tokens, ~20 chunks)
├── Section: "Watchlist Summary" → chunks 1-2
├── Per-ticker sections (1-2 chunks each)
│   ├── "005930 Samsung" → chunk 3
│   ├── "000660 SK Hynix" → chunk 4
│   └── ... (15-18 tickers)
└── Section: "Sector Overview" → chunk 19-20
```

#### Embedding Model Comparison

| Model | Dimensions | Cost | Quality (MTEB) | Batch Support |
|---|---|---|---|---|
| voyage-3 | 1024 | $0.06/M tok | 67.5 | Yes (128) |
| text-embedding-3-small | 1536 | $0.02/M tok | 62.3 | Yes (2048) |
| voyage-finance-2 | 1024 | $0.12/M tok | 70.1 (finance) | Yes (128) |

**Recommendation**: Start with `voyage-3` (good quality, Anthropic partnership, reasonable cost). If budget allows, evaluate `voyage-finance-2` for potential quality uplift in financial domain retrieval.

#### Retrieval Flow

```
Persona Tool Call: get_static_context(name="macro_context", mode="semantic", query="Fed rate cut expectations")
                                    │
                                    ▼
                    ┌──────────────────────────────┐
                    │ Embedding API                 │
                    │ query → 1024-dim vector       │
                    │ Latency: ~100ms               │
                    └──────────────────────────────┘
                                    │
                                    ▼
                    ┌──────────────────────────────┐
                    │ pgvector cosine search        │
                    │ SELECT ... ORDER BY           │
                    │   embedding <=> query_vec     │
                    │ LIMIT 7                       │
                    │ Latency: ~20ms                │
                    └──────────────────────────────┘
                                    │
                                    ▼
                    ┌──────────────────────────────┐
                    │ Return top-7 chunks           │
                    │ ~700-1500 tokens              │
                    │ (vs full file ~3500 tokens)   │
                    │ Savings: ~60-70%              │
                    └──────────────────────────────┘
```

### Haiku Quality Assessment

#### Tasks Suitable for Haiku 4.5

| Task | Why Haiku Works | Risk if Quality Degrades |
|---|---|---|
| Micro screening | Pattern matching against predefined criteria | Decision persona compensates — it re-evaluates all candidates |
| Daily Report | Data aggregation + formatting from DB | No decision impact — informational only |
| Macro News | RSS headline summarization | Macro persona (Opus) does real analysis — news is just input |

#### Tasks NOT Suitable for Haiku

| Task | Why Sonnet/Opus Required | Risk if Downgraded |
|---|---|---|
| Decision | Nuanced judgment combining multiple signals | Direct capital loss — Park Sehoon's persona quality |
| Risk | Independent validation, contradiction detection | SoD failure — capital protection compromised |
| Macro | Deep geopolitical + economic synthesis | Shallow analysis → bad weekly guidance → cascading errors |
| Retrospective | Meta-learning, strategic pattern recognition | System improvement stagnates |

#### Quality Gate Design

The shadow testing approach (REQ-MIGR-05-2) measures:

1. **Candidate overlap** (Micro persona):
   - Run Haiku and Sonnet on identical inputs
   - Compare top-5 매수/매도/관망 candidates
   - Overlap ≥ 85% = acceptable quality
   - Overlap < 85% for 3 consecutive days = auto-revert

2. **Format compliance** (Daily Report, Macro News):
   - Structured output parsing success rate
   - Required fields present
   - ≥ 99% compliance = acceptable

3. **Decision impact** (indirect measure):
   - Track whether Decision persona's signal quality changes after Micro switches to Haiku
   - Long-term metric: compare trade outcomes before/after Haiku activation
   - This is observational only — does not trigger auto-revert

---

## Implementation Strategy

### Priority Order

1. **Module 2 (pgvector)** — Foundation: embedding pipeline needed before semantic search
2. **Module 3 (get_static_context enhancement)** — Depends on Module 2
3. **Module 1 (Model Router)** — Independent, but test after semantic retrieval is active
4. **Module 4 (Cost Monitoring)** — Enhance existing reporting
5. **Module 5 (Migration)** — Quality gates and phased rollout

### Risk Assessment

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Haiku quality insufficient for Micro screening | Medium | High | Quality gate + auto-revert (REQ-MIGR-05-3) |
| pgvector retrieval misses critical context | Low | Medium | top-K tuning + fallback to full mode |
| Embedding API downtime | Low | Low | Fallback to full .md injection |
| Monthly cost still exceeds target | Medium | Medium | Operational adjustments (reduce call frequency) |
| Haiku output format failures | Low | Low | Retry + fallback to Sonnet for that call |

### Dependency Chain

```
SPEC-007 (Static Context) ──┐
SPEC-008 (Prompt Caching) ──┼── SPEC-009 (Tool-calling) ── SPEC-010 (This SPEC)
SPEC-001 (5-Persona System)─┘
```

All three predecessor SPECs must be fully operational before SPEC-010 activation.

---

## Configuration Requirements

### New Environment Variables

```bash
# Embedding configuration
EMBEDDING_MODEL=voyage-3              # voyage-3 | text-embedding-3-small
VOYAGE_API_KEY=...                    # Required if EMBEDDING_MODEL=voyage-3
OPENAI_API_KEY=...                    # Required if EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1024             # Match model output dimensions

# Cost targets
MONTHLY_COST_TARGET_KRW=100000        # Target: 10만원/month
MONTHLY_COST_WARNING_KRW=200000       # Hard limit warning (SPEC-008)
```

### New Telegram Commands

| Command | Description | Example |
|---|---|---|
| `/model <persona> <model>` | Set persona model | `/model micro claude-haiku-4-5` |
| `/haiku <persona> on\|off` | Toggle Haiku routing | `/haiku micro off` |
| `/shadow-test <persona>` | Run dual-model comparison | `/shadow-test micro` |
| `/cost` | Show cost summary | `/cost` |
| `/semantic on\|off` | Toggle semantic retrieval | `/semantic on` |
