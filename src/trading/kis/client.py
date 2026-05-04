"""KIS REST client base (REQ-KIS-02-1).

Provides a thin wrapper that:
- Resolves base URL from TRADING_MODE
- Attaches auth headers (Bearer + appkey + appsecret)
- Selects tr_id prefix V (paper) vs T (live)
- Auto-retries on rate-limit (rt_cd=1, EGW00201)
- Standardises error handling
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from trading.config import TradingMode, get_settings
from trading.kis.auth import base_url, get_token

LOG = logging.getLogger(__name__)

# KIS rate-limit retry config. KIS paper environment is more aggressive than docs claim.
RATE_LIMIT_RETRIES = 4
RATE_LIMIT_BACKOFF_SECONDS = 1.0
# KIS error codes that signal "wait and retry"
RATE_LIMIT_MSG_CODES = {"EGW00201"}     # 초당 거래건수 초과


@dataclass
class KisResponse:
    status_code: int
    rt_cd: str          # KIS response status code "0" = success, others = error
    msg_cd: str
    msg: str
    output: dict[str, Any] | list[dict[str, Any]]
    raw: dict[str, Any]


class KisError(RuntimeError):
    """KIS API returned a non-success rt_cd."""

    def __init__(self, response: KisResponse):
        self.response = response
        super().__init__(f"KIS error rt_cd={response.rt_cd} msg={response.msg!r}")


class KisClient:
    """Reusable KIS REST client. One instance per trading mode is sufficient."""

    def __init__(self, mode: TradingMode | None = None):
        s = get_settings()
        self.mode = mode if mode is not None else s.trading_mode
        self.base = base_url(self.mode)
        # Pin credentials for the configured mode.
        if self.mode == TradingMode.LIVE:
            self._appkey = s.kis.live_app_key.get_secret_value()
            self._appsecret = s.kis.live_app_secret.get_secret_value()
            self._account_full = s.kis.live_account
        else:
            self._appkey = s.kis.paper_app_key.get_secret_value()
            self._appsecret = s.kis.paper_app_secret.get_secret_value()
            self._account_full = s.kis.paper_account

    @property
    def account_prefix(self) -> str:
        """First 8 digits of account number (CANO)."""
        return self._account_full.split("-")[0]

    @property
    def account_suffix(self) -> str:
        """2-digit product code (ACNT_PRDT_CD)."""
        return self._account_full.split("-")[1] if "-" in self._account_full else "01"

    def tr_id(self, paper_id: str, live_id: str) -> str:
        """Return correct tr_id for current mode."""
        return live_id if self.mode == TradingMode.LIVE else paper_id

    def _headers(self, tr_id: str, hashkey: str | None = None) -> dict[str, str]:
        token = get_token(self.mode).access_token
        h = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self._appkey,
            "appsecret": self._appsecret,
            "tr_id": tr_id,
        }
        if hashkey:
            h["hashkey"] = hashkey
        return h

    def _is_rate_limited(self, resp: KisResponse) -> bool:
        return resp.rt_cd == "1" and (
            resp.msg_cd in RATE_LIMIT_MSG_CODES or "초당 거래건수" in resp.msg
        )

    def get(
        self,
        path: str,
        tr_id: str,
        params: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> KisResponse:
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            with httpx.Client(timeout=timeout) as client:
                r = client.get(f"{self.base}{path}", params=params, headers=self._headers(tr_id))
            resp = self._parse(r)
            if not self._is_rate_limited(resp) or attempt == RATE_LIMIT_RETRIES:
                return resp
            backoff = RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1)
            LOG.warning("KIS rate limited (attempt %d), sleeping %.1fs", attempt + 1, backoff)
            time.sleep(backoff)
        return resp  # unreachable, but keeps type checker happy

    def post(
        self,
        path: str,
        tr_id: str,
        body: dict[str, Any],
        timeout: float = 10.0,
    ) -> KisResponse:
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            with httpx.Client(timeout=timeout) as client:
                r = client.post(f"{self.base}{path}", json=body, headers=self._headers(tr_id))
            resp = self._parse(r)
            if not self._is_rate_limited(resp) or attempt == RATE_LIMIT_RETRIES:
                return resp
            backoff = RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1)
            LOG.warning("KIS rate limited (attempt %d), sleeping %.1fs", attempt + 1, backoff)
            time.sleep(backoff)
        return resp

    @staticmethod
    def _parse(r: httpx.Response) -> KisResponse:
        try:
            data = r.json()
        except ValueError:
            raise RuntimeError(f"KIS non-JSON response (status {r.status_code}): {r.text[:200]}")
        return KisResponse(
            status_code=r.status_code,
            rt_cd=data.get("rt_cd", ""),
            msg_cd=data.get("msg_cd", ""),
            msg=data.get("msg1", ""),
            output=data.get("output", data.get("output1", {})),
            raw=data,
        )
