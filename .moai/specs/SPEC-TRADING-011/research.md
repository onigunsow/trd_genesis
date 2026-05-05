# SPEC-TRADING-011 Research — JIT State Reconstruction + ProtoHedge Risk Management

## Research Summary

This document captures the research basis, academic foundations, codebase analysis, and architectural decisions that inform SPEC-TRADING-011.

---

## 1. Academic & Industry Research

### 1.1 HSTR (Historical State Reconstruction)

**Core Concept**: Pre-compute periodic snapshots and accumulate delta updates between snapshots. At query time, reconstruct current state by merging latest snapshot with all pending deltas.

**Key Properties**:
- **O(1) amortized query**: Cache the merged result; invalidate only when new delta arrives
- **Append-only deltas**: No destructive updates — full audit trail
- **Bounded storage**: Compact deltas into next snapshot periodically
- **Repeatable reconstruction**: Same snapshot + same deltas = same result (deterministic)

**Application to Trading System**:
- Base snapshot = cron-generated .md files (SPEC-007, existing)
- Delta events = real-time price changes (KIS WebSocket), new disclosures (DART), news headlines
- Query time = persona invocation (pre-market, intraday, event trigger)
- Currently personas see stale data between cron cycles (e.g., 06:30 snapshot is 3+ hours old at 09:30 intraday call)

**Adaptation Notes**:
- Trading system has low delta volume (~5000/day) making in-memory merge trivial
- 10-second cache TTL balances freshness vs DB query frequency
- Delta retention of 7 days prevents unbounded table growth
- Cron continues as before — no replacement, only augmentation

### 1.2 ProtoHedge (Prototype-based Risk Management)

**Core Concept**: Build a library of "market prototypes" — characterized historical scenarios with known outcomes. Compare current market conditions to prototype library using embedding similarity. Provide interpretable risk recommendations based on which historical scenario the current market most resembles.

**Key Properties**:
- **Interpretable**: "82% similar to 2024-08 crash" is more actionable than abstract risk scores
- **Historical grounding**: Recommendations based on actual past outcomes, not theoretical models
- **Dynamic adjustment**: Risk posture changes as market similarity shifts
- **Human-verifiable**: User can inspect prototype definitions and validate reasoning
- **Complementary to static limits**: Prototypes tighten, never loosen, the HARD floor

**Application to Trading System**:
- Risk persona currently evaluates based on: position limits, sector concentration, correlation
- ProtoHedge adds scenario-awareness: "this market environment historically led to X% drawdown"
- Output format aligned with existing Risk persona communication style
- Korean market has distinctive patterns (foreign net sell cycles, credit events) worth capturing

**Design Decisions**:
- Embedding-based similarity (vs rule-based matching) — more flexible, handles novel combinations
- Advisory to Risk persona (vs auto-execution) — maintains SoD principle, human-in-loop
- Static limits as HARD floor — ProtoHedge never overrides SPEC-001 safety rules
- Initial 10 prototypes cover major Korean market events 2020-2024

---

## 2. Current System Analysis

### 2.1 Current Data Flow (Cron-based)

```
06:00  build_macro_context.md  → data/contexts/macro_context.md
06:30  build_micro_context.md  → data/contexts/micro_context.md
06:45  build_micro_news.md     → data/contexts/micro_news.md
       (Fri 16:30 build_macro_news.md → data/contexts/macro_news.md)

07:30  Micro persona reads: micro_context.md (3+ hours stale for intraday)
09:30  Decision persona reads: macro_context.md + micro_context.md (3.5+ hours stale)
11:00  Decision persona reads: same stale files
13:30  Decision persona reads: same stale files (7+ hours stale)
14:30  Decision persona reads: same stale files (8+ hours stale)
```

**Problem**: Between 06:30 (micro context build) and 15:30 (market close), the market changes significantly. Personas make decisions based on pre-market data even during intraday calls.

**With SPEC-009 Tool-calling**: Tools like `get_ticker_technicals` CAN fetch fresh data from KIS, but only when the persona actively calls the tool. The base context (macro/micro .md files) injected via `get_static_context` remains stale.

**With SPEC-011 JIT**: Tools transparently return merged (snapshot + intraday deltas) data. The persona doesn't need to know about the merge — it just gets fresh data.

### 2.2 Current Risk Management

```python
# src/trading/risk/limits.py (existing)
DAILY_MAX_LOSS_PCT = -1.0
MAX_POSITION_PCT = 20.0
MAX_TOTAL_EXPOSURE_PCT = 80.0
MAX_SINGLE_ORDER_PCT = 10.0
MAX_DAILY_ORDERS = 10
```

These are HARD-coded limits that cannot be overridden. The Risk persona additionally evaluates:
- Sector concentration
- Correlation between holdings
- Downside scenario estimation (qualitative)
- Persona response consistency check

**Gap**: No systematic reference to historical market scenarios. Risk persona relies on LLM's training data knowledge of past crashes/rallies rather than structured, comparable data.

**With SPEC-011 ProtoHedge**: Risk persona receives explicit similarity context: "Current market is X% similar to Y scenario which resulted in Z% drawdown." This grounds the risk assessment in structured historical data.

### 2.3 Existing Infrastructure (Reuse Analysis)

| Component | SPEC Origin | Reuse in SPEC-011 |
|---|---|---|
| PostgreSQL 16 + pgvector | SPEC-010 | Prototype embeddings, delta_events table |
| Embedding pipeline | SPEC-010 | Generate prototype embeddings, current state embeddings |
| Tool Registry | SPEC-009 | New tools: get_delta_events, get_market_prototype_similarity |
| APScheduler | SPEC-001 | Start/stop JIT pipeline at market open/close |
| KIS client (REST) | SPEC-001 M2 | KIS WebSocket uses same auth token mechanism |
| DART adapter | SPEC-001 M3 | DART poller reuses existing dart_adapter.py |
| Telegram bot | SPEC-001 M5 | New commands: /jit, /prototype, /prototype-status |
| audit_log | SPEC-001 | All new events logged through existing pattern |
| structlog | SPEC-001 | Structured logging for JIT pipeline |

---

## 3. Technology Selection

### 3.1 KIS WebSocket vs REST Polling

| Approach | Latency | API Calls | Complexity |
|---|---|---|---|
| WebSocket (chosen) | < 1s real-time | 1 connection | Medium (reconnection logic) |
| REST polling (30s) | 30s stale | ~780 calls/session | Low |
| REST polling (5s) | 5s stale | ~4680 calls/session | Low but rate limit risk |

**Decision**: WebSocket for price data. KIS paper mode supports WebSocket at `ws://ops.koreainvestment.com:21000` with up to 40 ticker subscriptions per connection.

**Risk**: WebSocket can disconnect. Mitigation: exponential backoff reconnection (max 10 attempts/day).

### 3.2 DART Polling Interval

DART API daily limit: 1000 requests. Market hours: 6.5 hours = 390 minutes.

| Interval | Requests/session | % of daily limit | Staleness |
|---|---|---|---|
| 1 min | 390 | 39% | 1 min |
| 5 min (chosen) | 78 | 7.8% | 5 min |
| 15 min | 26 | 2.6% | 15 min |

**Decision**: 5-minute polling balances freshness vs API budget. Most material disclosures have impact over hours, not minutes.

### 3.3 Prototype Embedding Strategy

| Approach | Pros | Cons |
|---|---|---|
| Full text embedding (chosen) | Simple, captures narrative context | Less structured |
| Indicator vector distance | Mathematically precise | Loses context, requires normalization |
| Hybrid (embedding + rules) | Best accuracy | Highest complexity |

**Decision**: Full text embedding using SPEC-010's configured model (voyage-3 / text-embedding-3-small). The prototype description + indicators are concatenated into text and embedded. Cosine similarity provides a single interpretable score. Simple and extensible.

### 3.4 Merge Engine Architecture

| Approach | Read Latency | Memory | Complexity |
|---|---|---|---|
| Materialized view (DB) | ~10ms | Low | Medium |
| In-memory cache (chosen) | < 1ms | Medium (~50MB) | Low |
| Event sourcing (full) | Varies | High | High |

**Decision**: In-memory cache with TTL. Trading system has modest data volume (40 tickers, ~5000 events/day). Full in-memory merge is trivial at this scale. Cache invalidation on new delta arrival with 10-second TTL prevents stale reads while avoiding constant recomputation.

---

## 4. Risk Analysis

### 4.1 Technical Risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| KIS WebSocket instability | Medium | Medium (stale prices) | Auto-reconnect + fallback to static |
| Merge engine memory leak | Low | High (container crash) | Bounded cache size + 7-day retention |
| Prototype similarity false positive | Medium | Medium (overly conservative) | Human review + /prototype off command |
| Prototype similarity false negative | Low | High (missed risk) | Multiple prototypes cover diverse scenarios |
| pgvector query slowness | Low | Low (fallback to static) | IVFFlat index + small table size |
| WebSocket data format change | Low | High (broken ingestion) | Schema validation + alerting |

### 4.2 Operational Risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Prototype library too small | Medium | Medium (low coverage) | Start with 10, grow incrementally |
| False crash similarity triggers | Medium | Low (advisory only) | Threshold tuning + human override |
| Delta table unbounded growth | Low | Medium (DB storage) | 7-day retention + nightly cleanup |
| Feature flag confusion | Low | Low (revertible) | Clear Telegram commands + audit trail |

### 4.3 Cost Impact

| Item | Monthly Cost |
|---|---|
| Embedding API (prototypes + current state) | < 100 KRW |
| KIS WebSocket connection | Free (included) |
| DART polling | Free (within limits) |
| Additional DB storage (delta_events) | Negligible (< 50MB) |
| **Total additional monthly cost** | **< 100 KRW** |

This SPEC has effectively zero ongoing cost impact — the upgrades are infrastructure-level with no additional LLM API charges.

---

## 5. Design Decisions Log

| Decision | Options Considered | Chosen | Rationale |
|---|---|---|---|
| Delta source: WebSocket vs polling | WebSocket, REST 30s, REST 5s | WebSocket | Real-time latency with single connection |
| Merge strategy: DB vs memory | Materialized view, in-memory | In-memory + TTL cache | < 1ms reads for small data volume |
| Prototype matching: embedding vs rules | Embedding, rule-based, hybrid | Embedding similarity | Simple, extensible, interpretable |
| Prototype authority: auto vs advisory | Auto-execute, advisory to Risk | Advisory to Risk persona | Maintains SoD, human-in-loop |
| Static limits interaction | Override, tighten-only, ignore | Tighten-only (dynamic ceiling) | Safety: never loosen HARD limits |
| Docker topology: new container vs thread | Separate container, background thread | Background thread in app container | Simpler, lower resource, sufficient for single-user |
| Prototype creation: auto vs manual | Fully automated, manual only, hybrid | Manual + optional auto-extract | Safety: human review before activation |
| Delta retention: days | 3, 7, 14, 30 | 7 days | Balance: enough for debugging, bounded storage |

---

## 6. Integration Architecture

```
                    ┌─────────────────────────────────────────────────────┐
                    │              JIT State Reconstruction                │
                    │                                                       │
  06:00-06:45      │   ┌─────────┐     ┌──────────────┐                   │
  Cron builds ────►│   │Snapshot │────►│   Merge      │──► Merged State   │
                    │   │ Table   │     │   Engine     │    (cached)       │
  Market hours     │   └─────────┘     │              │                   │
  09:00-15:30      │                   │  snapshot +  │                   │
                    │   ┌─────────┐    │  deltas =    │                   │
  KIS WebSocket ──►│   │ Delta   │───►│  current     │                   │
  DART Polling ───►│   │ Events  │    │  state       │                   │
  News RSS ───────►│   │ Table   │    └──────────────┘                   │
                    │   └─────────┘                                        │
                    └──────────────────────────┬──────────────────────────┘
                                               │
                                               ▼
                    ┌─────────────────────────────────────────────────────┐
                    │              SPEC-009 Tool Layer                      │
                    │                                                       │
                    │  get_ticker_technicals ──► Merged price + indicators │
                    │  get_portfolio_status ───► Real-time unrealized P&L  │
                    │  get_delta_events ───────► Recent intraday events    │
                    │  get_market_prototype_similarity ──► ProtoHedge      │
                    └──────────────────────────┬──────────────────────────┘
                                               │
                                               ▼
                    ┌─────────────────────────────────────────────────────┐
                    │              ProtoHedge Risk Layer                    │
                    │                                                       │
                    │  ┌────────────────┐    ┌──────────────────────────┐  │
                    │  │Market Prototype│    │ Similarity + Exposure    │  │
                    │  │   Library      │───►│    Engine                │  │
                    │  │(pgvector + DB) │    │                          │  │
                    │  └────────────────┘    │ Current state embedding  │  │
                    │                        │ vs prototype embeddings  │  │
                    │                        │ → similarity score       │  │
                    │                        │ → dynamic ceiling        │  │
                    │                        └──────────────────────────┘  │
                    └──────────────────────────┬──────────────────────────┘
                                               │
                                               ▼
                    ┌─────────────────────────────────────────────────────┐
                    │              Risk Persona Input                       │
                    │                                                       │
                    │  Standard: signal + positions + limits                │
                    │  + ProtoHedge: "82% similar to 2024-08-crash,        │
                    │    ceiling 50%"                                       │
                    │  → Risk APPROVE/HOLD/REJECT                          │
                    └─────────────────────────────────────────────────────┘
```

---

## 7. Prototype Library Seed Data (Detailed)

### 7.1 2024-08-crash (Primary Reference)

- **Period**: 2024-08-01 ~ 2024-08-15
- **Trigger**: Bank of Japan rate hike → Yen carry trade unwind → global equity selloff
- **KOSPI impact**: -11.3% peak-to-trough
- **Key signals**: VIX > 30, USD/KRW > 1360, foreign net sell 10+ consecutive days
- **Recovery**: ~45 days to previous high
- **Lesson**: Tech/growth sectors hit hardest; utilities/healthcare outperformed
- **Recommended ceiling**: 30-50% depending on similarity

### 7.2 2024-11-rally (US Election Effect)

- **Period**: 2024-11-01 ~ 2024-12-15
- **Trigger**: Trump election → deregulation/tariff expectations
- **KOSPI impact**: +8.5% (selective — exporters benefited, domestic hurt by tariff fears)
- **Key signals**: VIX < 15, strong foreign buying in semis/autos
- **Lesson**: Sector rotation matters more than market direction
- **Recommended ceiling**: 90% (expanded exposure for upside capture)

### 7.3 2020-03-covid-crash

- **Period**: 2020-02-20 ~ 2020-03-23
- **Trigger**: Global COVID pandemic, lockdown announcements
- **KOSPI impact**: -35% in 5 weeks
- **Key signals**: VIX > 60, circuit breakers triggered, global correlation = 1.0
- **Recovery**: V-shaped, 6 months to previous high
- **Lesson**: Extreme crash — cash is king. Recovery opportunities massive.
- **Recommended ceiling**: 20% (absolute minimum during extreme events)

---

## 8. Implementation Priority

| Priority | Module | Rationale |
|---|---|---|
| Primary | M1 (Delta Pipeline) + M2 (Merge Engine) | Foundation for real-time data. Other modules depend on this. |
| Primary | M6 (Migration & Feature Flags) | Required for safe incremental rollout |
| Secondary | M3 (Prototype Library) + M4 (Dynamic Risk) | High value but independent of delta pipeline |
| Secondary | M5 (Tool Integration) | Depends on M1+M2 being operational |
| Final | Prototype seed data + validation | Manual review of 10 initial prototypes |

---

## 9. Open Questions

1. **WebSocket ticker subscription management**: When watchlist changes intraday (new candidate from Micro persona), should WebSocket dynamically resubscribe? Or use fixed set from pre-market?
   - Recommendation: Fixed pre-market set + allow 1 dynamic resubscription per intraday cycle (max 5/day)

2. **Prototype library growth**: How frequently should new prototypes be added?
   - Recommendation: Quarterly review, add 2-3 per quarter as market events occur

3. **Exposure ceiling notification threshold**: When should user be alerted about dynamic ceiling changes?
   - Recommendation: Alert when ceiling drops below 60% (significant restriction). Daily report covers all changes.

4. **Delta conflict resolution**: If WebSocket price and DART data conflict (e.g., stock suspended but WS still shows last price)?
   - Recommendation: DART disclosure overrides — if suspension detected, mark ticker as `trading_halted` in merged state
