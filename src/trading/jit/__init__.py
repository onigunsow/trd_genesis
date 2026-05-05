"""JIT State Reconstruction Pipeline — SPEC-TRADING-011 Modules 1 & 2.

Provides real-time delta event ingestion (WebSocket prices, DART polling, news RSS)
and an O(1) amortized merge engine that combines base snapshots with intraday deltas.
"""
