"""5-persona AI trading system (M4+).

Personas:
- macro         : Opus 4.7, weekly Friday 17:00, market regime + watchlist
- micro         : Sonnet 4.6, pre-market 07:30 + intraday cache reuse, ticker analysis
- decision      : Sonnet 4.6 (박세훈 페르소나), pre-market 07:50 + intraday + event
- risk          : Sonnet 4.6, on every Decision signal, SoD verifier
- portfolio     : (M5+) Sonnet 4.6, when holdings ≥ 5
- retrospective : (M5+) Sonnet 4.6, weekly Sunday

Every invocation persists to persona_runs with token cost (REQ-PERSONA-04-2).
"""
