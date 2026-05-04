"""KIS OAuth token issuance with on-disk cache (REQ-KIS-02-2).

The KIS Developers token endpoint enforces a 1-minute reissue cooldown. Each
trading mode (paper/live) has its own cache file under ~/trading/data/.
Cached tokens are reused until 5 minutes before expiry (KIS issues 24h tokens).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import httpx

from trading.config import TradingMode, get_settings, project_root

PAPER_BASE: Final = "https://openapivts.koreainvestment.com:29443"
LIVE_BASE: Final = "https://openapi.koreainvestment.com:9443"

# Refresh 5 minutes before nominal expiry to be safe.
REFRESH_LEEWAY_SECONDS: Final = 300


@dataclass
class Token:
    access_token: str
    expires_at: float  # Unix timestamp

    @property
    def is_fresh(self) -> bool:
        return self.expires_at - time.time() > REFRESH_LEEWAY_SECONDS


def base_url(mode: TradingMode) -> str:
    return LIVE_BASE if mode == TradingMode.LIVE else PAPER_BASE


def cache_file(mode: TradingMode) -> Path:
    return project_root() / "data" / f".kis_token_{mode.value}.json"


def load_cached(mode: TradingMode) -> Token | None:
    f = cache_file(mode)
    if not f.exists():
        return None
    try:
        raw = json.loads(f.read_text())
        token = Token(access_token=raw["access_token"], expires_at=float(raw["expires_at"]))
        return token if token.is_fresh else None
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def save_cached(mode: TradingMode, token: Token) -> None:
    f = cache_file(mode)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"access_token": token.access_token, "expires_at": token.expires_at}))
    # Tighten permission — token file holds bearer credentials.
    try:
        f.chmod(0o600)
    except OSError:
        pass


def request_new_token(mode: TradingMode) -> Token:
    """Call /oauth2/tokenP. KIS enforces 1-minute reissue cooldown — only call when cache stale."""
    s = get_settings()
    if mode == TradingMode.LIVE:
        app_key = s.kis.live_app_key.get_secret_value()
        app_secret = s.kis.live_app_secret.get_secret_value()
    else:
        app_key = s.kis.paper_app_key.get_secret_value()
        app_secret = s.kis.paper_app_secret.get_secret_value()

    url = f"{base_url(mode)}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "appsecret": app_secret,
    }
    with httpx.Client(timeout=10.0) as client:
        r = client.post(url, json=body)
    r.raise_for_status()
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"KIS token response missing access_token: {data}")
    expires_in = int(data.get("expires_in", 86400))
    token = Token(access_token=data["access_token"], expires_at=time.time() + expires_in)
    save_cached(mode, token)
    return token


def get_token(mode: TradingMode | None = None) -> Token:
    """Return a fresh token, using cache when available (REQ-KIS-02-2)."""
    if mode is None:
        mode = get_settings().trading_mode
    cached = load_cached(mode)
    if cached is not None:
        return cached
    return request_new_token(mode)
