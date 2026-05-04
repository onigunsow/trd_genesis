"""KIS Developers REST API client (direct httpx, no third-party SDK).

Endpoints (per tech.md):
- Paper: https://openapivts.koreainvestment.com:29443  (tr_id prefix V)
- Live:  https://openapi.koreainvestment.com:9443      (tr_id prefix T)

Modules:
- auth     : OAuth token issuance + caching (REQ-KIS-02-2)
- client   : REST client base, paper/live mode dispatch
- market   : current price, daily candles
- account  : balance, buyable amount
- order    : buy/sell/modify/cancel (live mode blocked unless live_unlocked=true)
"""
