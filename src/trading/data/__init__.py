"""Market data adapters and Postgres OHLCV cache.

Adapters expose a uniform interface; concrete implementations vary in source.
- pykrx_adapter   : Korean stocks (no API key, public exchange data)
- yfinance_adapter: Global assets (S&P500, VIX, USD/KRW)
- fred_adapter    : US macro (Fed funds rate, CPI, etc.)
- ecos_adapter    : Korea macro (한국은행)
- dart_adapter    : DART disclosures (gracefully degrades if DART_API_KEY missing/short)
"""
