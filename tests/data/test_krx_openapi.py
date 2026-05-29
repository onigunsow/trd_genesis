"""SPEC-TRADING-036 REQ-036-1(c) — KRX OpenAPI V-KOSPI fetcher tests.

The fetcher must:
- return a numeric VKOSPI value when the KRX OpenAPI 파생상품지수 service responds
  with the 코스피200 변동성지수 row (post-approval path),
- return ``None`` (graceful) on HTTP 401 "Unauthorized API Call" (current
  pre-approval state) and on any exception / timeout / missing key,
- never raise.

@MX:SPEC: SPEC-TRADING-036
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx

from trading.data import krx_openapi


def _settings_with_key(key: str | None):
    secret = SimpleNamespace(get_secret_value=lambda: key) if key is not None else None
    return SimpleNamespace(data_apis=SimpleNamespace(krx_openapi_key=secret))


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err", request=MagicMock(), response=MagicMock(status_code=self.status_code)
            )


class _FakeClient:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, *_a, **_k):
        return self._response


def _fake_client(payload, status_code=200):
    return _FakeClient(_FakeResponse(payload, status_code=status_code))


class _SequencedClient:
    """Returns a different response per ``basDd`` (keyed by the param)."""

    def __init__(self, by_day):
        # by_day: dict[str basDd -> _FakeResponse]; missing day -> empty 200.
        self._by_day = by_day
        self.queried_days: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, _url, *, headers=None, params=None):
        day = (params or {}).get("basDd", "")
        self.queried_days.append(day)
        return self._by_day.get(day, _FakeResponse({"OutBlock_1": []}))


# ---------------------------------------------------------------------------
# Post-approval happy path
# ---------------------------------------------------------------------------
class TestVkospiFetchSuccess:
    def test_returns_vkospi_value_from_derivative_index_rows(self):
        payload = {
            "OutBlock_1": [
                {"IDX_NM": "코스피 200", "CLSPRC_IDX": "412.10"},
                {"IDX_NM": "코스피 200 변동성지수", "CLSPRC_IDX": "18.42"},
                {"IDX_NM": "코스피 200 선물지수", "CLSPRC_IDX": "100.0"},
            ]
        }
        with (
            patch.object(krx_openapi, "get_settings", return_value=_settings_with_key("KEY")),
            patch.object(krx_openapi.httpx, "Client", return_value=_fake_client(payload)),
        ):
            val = krx_openapi.fetch_vkospi()
        assert val == 18.42

    def test_marker_returns_value_string_when_available(self):
        payload = {"OutBlock_1": [{"IDX_NM": "코스피200 변동성지수", "CLSPRC_IDX": "21.0"}]}
        with (
            patch.object(krx_openapi, "get_settings", return_value=_settings_with_key("KEY")),
            patch.object(krx_openapi.httpx, "Client", return_value=_fake_client(payload)),
        ):
            marker = krx_openapi.vkospi_marker()
        assert "21.0" in marker
        assert "unavailable" not in marker


# ---------------------------------------------------------------------------
# Graceful failure paths (current state + defensive)
# ---------------------------------------------------------------------------
class TestVkospiGraceful:
    def test_401_returns_none_does_not_raise(self):
        with (
            patch.object(krx_openapi, "get_settings", return_value=_settings_with_key("KEY")),
            patch.object(krx_openapi.httpx, "Client", return_value=_fake_client({}, 401)),
        ):
            assert krx_openapi.fetch_vkospi() is None

    def test_401_marker_is_unavailable(self):
        with (
            patch.object(krx_openapi, "get_settings", return_value=_settings_with_key("KEY")),
            patch.object(krx_openapi.httpx, "Client", return_value=_fake_client({}, 401)),
        ):
            marker = krx_openapi.vkospi_marker()
        assert "unavailable" in marker

    def test_missing_key_returns_none(self):
        with patch.object(krx_openapi, "get_settings", return_value=_settings_with_key(None)):
            assert krx_openapi.fetch_vkospi() is None

    def test_network_exception_returns_none(self):
        def _boom(*_a, **_k):
            raise httpx.ConnectError("down")

        with (
            patch.object(krx_openapi, "get_settings", return_value=_settings_with_key("KEY")),
            patch.object(krx_openapi.httpx, "Client", side_effect=_boom),
        ):
            assert krx_openapi.fetch_vkospi() is None

    def test_vkospi_row_absent_returns_none(self):
        payload = {"OutBlock_1": [{"IDX_NM": "코스피 200", "CLSPRC_IDX": "412.10"}]}
        with (
            patch.object(krx_openapi, "get_settings", return_value=_settings_with_key("KEY")),
            patch.object(krx_openapi.httpx, "Client", return_value=_fake_client(payload)),
        ):
            assert krx_openapi.fetch_vkospi() is None

    def test_unparseable_value_returns_none(self):
        payload = {"OutBlock_1": [{"IDX_NM": "코스피200 변동성지수", "CLSPRC_IDX": "-"}]}
        with (
            patch.object(krx_openapi, "get_settings", return_value=_settings_with_key("KEY")),
            patch.object(krx_openapi.httpx, "Client", return_value=_fake_client(payload)),
        ):
            assert krx_openapi.fetch_vkospi() is None

    def test_marker_wording_is_neutral_not_401(self):
        # The cause may be intraday-no-data / timeout / approval — not specifically
        # 401, so the marker must NOT hardcode "401" / "승인 대기".
        with (
            patch.object(krx_openapi, "get_settings", return_value=_settings_with_key("KEY")),
            patch.object(krx_openapi.httpx, "Client", return_value=_fake_client({}, 401)),
        ):
            marker = krx_openapi.vkospi_marker()
        assert "401" not in marker
        assert "승인" not in marker
        assert "V-KOSPI" in marker


# ---------------------------------------------------------------------------
# REQ (date-default fix): no-arg fetch walks back to the most recent published
# trading day; explicit date queries exactly one day (unchanged behaviour).
# ---------------------------------------------------------------------------
class TestVkospiDateFallback:
    def test_no_arg_falls_back_to_previous_published_day(self):
        from datetime import date

        today = date(2026, 5, 29)
        yday = date(2026, 5, 28)
        # today (5/29) -> empty (EOD not published intraday); 5/28 -> value 71.6.
        by_day = {
            yday.strftime("%Y%m%d"): _FakeResponse(
                {"OutBlock_1": [{"IDX_NM": "코스피 200 변동성지수", "CLSPRC_IDX": "71.60"}]}
            ),
            today.strftime("%Y%m%d"): _FakeResponse({"OutBlock_1": []}),
        }
        seq = _SequencedClient(by_day)
        with (
            patch.object(krx_openapi, "get_settings", return_value=_settings_with_key("KEY")),
            patch.object(krx_openapi.httpx, "Client", return_value=seq),
            patch.object(krx_openapi, "_today", return_value=today),
        ):
            val = krx_openapi.fetch_vkospi()
        assert val == 71.6
        # Queried today first, then walked back to 5/28.
        assert seq.queried_days[0] == today.strftime("%Y%m%d")
        assert yday.strftime("%Y%m%d") in seq.queried_days

    def test_explicit_date_queries_only_that_day(self):
        from datetime import date

        target = date(2026, 5, 28)
        by_day = {
            target.strftime("%Y%m%d"): _FakeResponse(
                {"OutBlock_1": [{"IDX_NM": "코스피 200 변동성지수", "CLSPRC_IDX": "71.60"}]}
            ),
        }
        seq = _SequencedClient(by_day)
        with (
            patch.object(krx_openapi, "get_settings", return_value=_settings_with_key("KEY")),
            patch.object(krx_openapi.httpx, "Client", return_value=seq),
        ):
            val = krx_openapi.fetch_vkospi(target)
        assert val == 71.6
        # Exactly one day queried (no fallback walk for explicit date).
        assert seq.queried_days == [target.strftime("%Y%m%d")]

    def test_lookback_is_bounded_when_service_dead(self):
        from datetime import date

        today = date(2026, 5, 29)
        # Every day returns empty -> must give up after the bounded lookback.
        seq = _SequencedClient({})
        with (
            patch.object(krx_openapi, "get_settings", return_value=_settings_with_key("KEY")),
            patch.object(krx_openapi.httpx, "Client", return_value=seq),
            patch.object(krx_openapi, "_today", return_value=today),
        ):
            val = krx_openapi.fetch_vkospi()
        assert val is None
        # Bounded: at most ~8 days probed (today + 7 lookback), never unbounded.
        assert 1 <= len(seq.queried_days) <= 8
