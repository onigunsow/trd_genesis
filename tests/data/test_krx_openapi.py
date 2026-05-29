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
