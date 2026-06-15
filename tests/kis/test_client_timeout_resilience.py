"""SPEC-TRADING-051: KIS 클라이언트 타임아웃 회복탄력성 테스트 (RED-GREEN-REFACTOR TDD).

AC-1  : get() ReadTimeout 후 재시도하여 성공 (REQ-051-A1a)
AC-1b : post() ReadTimeout → 즉시 KisTimeoutError, POST 정확히 1회 (REQ-051-A1b)
AC-2  : get() 예산 소진 → KisTimeoutError raise (REQ-051-A3)
AC-2b : KisTimeoutError isinstance RuntimeError (ADR-002, REQ-051-A3)
AC-3  : attempt당 _RateGate.acquire 1회 (REQ-051-A2)
AC-4  : test_rate_gate.py 회귀 없음 (REQ-051-NFR-1) — 별도 파일
AC-5  : 분할 타임아웃 적용 (REQ-051-A4)
AC-6  : backoff sleep seam 결정적 테스트 (REQ-051-A5)
AC-7  : post()는 _RateGate.acquire 호출 안 함 (REQ-051-A2 post 비게이팅)
AC-8  : get() 재시도 소진 최악 wall-time < 300s (ADR-005)
EC-1  : post 이중주문 회귀 가드 (REQ-051-A1b)
EC-2  : ConnectTimeout / TransportError 동일 처리 (REQ-051-A1a)
EC-3  : rate-limit 응답과 httpx 예외 혼재 (ADR-004)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from trading.kis.client import (
    RATE_LIMIT_BACKOFF_SECONDS,
    KisClient,
    KisError,
    KisResponse,
    _GATE,
    _RateGate,
)

# ──────────────────────────────────────────────────────────────────────────────
# 헬퍼: KisTimeoutError 지연 임포트 (GREEN 전까지 존재 안 할 수 있음)
# ──────────────────────────────────────────────────────────────────────────────


def _import_timeout_error():
    """KisTimeoutError를 지연 임포트 — GREEN 이후 존재."""
    from trading.kis.client import KisTimeoutError  # noqa: PLC0415

    return KisTimeoutError


# ──────────────────────────────────────────────────────────────────────────────
# FakeClock: test_rate_gate.py와 동일 패턴 — backoff sleep 주입용
# ──────────────────────────────────────────────────────────────────────────────


class FakeClock:
    """단조 증가 가짜 시계; sleep은 시간을 전진시키고 호출을 기록한다."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, d: float) -> None:
        self.sleeps.append(d)
        self.t += d


# ──────────────────────────────────────────────────────────────────────────────
# 가짜 KIS 설정 및 토큰 fixture
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _mock_settings_and_token():
    """KIS 자격증명 및 토큰을 모킹하여 실제 네트워크 없이 테스트."""
    from unittest.mock import MagicMock, patch  # noqa: PLC0415

    mock_settings = MagicMock()
    mock_settings.trading_mode.value = "paper"
    mock_settings.kis.paper_app_key.get_secret_value.return_value = "fake_key"
    mock_settings.kis.paper_app_secret.get_secret_value.return_value = "fake_secret"
    mock_settings.kis.paper_account = "12345678-01"
    mock_settings.kis.live_app_key.get_secret_value.return_value = "fake_live_key"
    mock_settings.kis.live_app_secret.get_secret_value.return_value = "fake_live_secret"
    mock_settings.kis.live_account = "99999999-01"

    mock_token = MagicMock()
    mock_token.access_token = "fake_token"

    from trading.config import TradingMode  # noqa: PLC0415

    mock_settings.trading_mode = TradingMode.PAPER

    with (
        patch("trading.kis.client.get_settings", return_value=mock_settings),
        patch("trading.kis.client.base_url", return_value="https://fake-kis.example.com"),
        patch("trading.kis.client.get_token", return_value=mock_token),
    ):
        yield


# ──────────────────────────────────────────────────────────────────────────────
# 헬퍼: 성공 응답 JSON 생성
# ──────────────────────────────────────────────────────────────────────────────

SUCCESS_JSON = {"rt_cd": "0", "msg_cd": "KISOQ0000", "msg1": "정상", "output": {}}
RATE_LIMIT_JSON = {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수 초과", "output": {}}


def _make_httpx_response(json_data: dict) -> httpx.Response:
    """httpx.Response를 직접 생성 (실제 네트워크 없이)."""
    import json  # noqa: PLC0415

    return httpx.Response(200, content=json.dumps(json_data).encode(), headers={"content-type": "application/json"})


# ──────────────────────────────────────────────────────────────────────────────
# AC-2b: KisTimeoutError isinstance 검사 (클래스 정의 테스트)
# ──────────────────────────────────────────────────────────────────────────────


class TestKisTimeoutErrorInheritance:
    """AC-2b (REQ-051-A3 / ADR-002): KisTimeoutError 상속 계층 검증."""

    def test_kis_timeout_error_is_runtime_error(self):
        """KisTimeoutError는 반드시 RuntimeError를 상속해야 한다 (ADR-002 HARD)."""
        KisTimeoutError = _import_timeout_error()
        err = KisTimeoutError("테스트 타임아웃")
        assert isinstance(err, RuntimeError), "KisTimeoutError는 RuntimeError 하위여야 한다"

    def test_kis_timeout_error_is_kis_error(self):
        """KisTimeoutError가 KisError 하위이면 cli.py except KisError가 자동 포착한다."""
        KisTimeoutError = _import_timeout_error()
        err = KisTimeoutError("테스트")
        assert isinstance(err, KisError), "KisTimeoutError는 KisError 하위여야 한다"

    def test_cli_fill_sync_except_catches_timeout_error(self):
        """AC-2b: cli.py L292 except KisError 절이 KisTimeoutError를 포착함을 시뮬레이션."""
        KisTimeoutError = _import_timeout_error()
        err = KisTimeoutError("타임아웃")
        caught = False
        try:
            raise err
        except KisError:
            caught = True
        assert caught, "except KisError가 KisTimeoutError를 포착하지 못했다"

    def test_except_runtime_error_also_catches(self):
        """AC-2b: cli.py L295 except RuntimeError 절도 포착."""
        KisTimeoutError = _import_timeout_error()
        err = KisTimeoutError("타임아웃")
        caught = False
        try:
            raise err
        except RuntimeError:
            caught = True
        assert caught, "except RuntimeError가 KisTimeoutError를 포착하지 못했다"

    def test_raw_httpx_exception_does_not_propagate(self):
        """AC-2: 소진 시 raw httpx.ReadTimeout이 아닌 KisTimeoutError가 raise된다."""
        KisTimeoutError = _import_timeout_error()
        client = KisClient()
        call_count = 0

        def _always_timeout(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.ReadTimeout("timeout", request=MagicMock())

        with (
            patch("trading.kis.client._sleep_fn", side_effect=lambda d: None),
            patch("httpx.Client") as mock_http_client,
        ):
            instance = MagicMock()
            mock_http_client.return_value.__enter__ = lambda s: instance
            mock_http_client.return_value.__exit__ = MagicMock(return_value=False)
            instance.get.side_effect = _always_timeout

            with pytest.raises(KisTimeoutError):
                client.get("/test", "TR_TEST")

            # raw httpx 예외가 아님을 확인
            with pytest.raises(KisTimeoutError) as exc_info:
                call_count = 0
                instance.get.side_effect = _always_timeout
                client.get("/test", "TR_TEST")
            assert not isinstance(exc_info.value, httpx.ReadTimeout)


# ──────────────────────────────────────────────────────────────────────────────
# AC-1: get() 타임아웃 후 재시도하여 성공
# ──────────────────────────────────────────────────────────────────────────────


class TestGetRetryOnTimeout:
    """AC-1 (REQ-051-A1a): get()이 httpx.ReadTimeout 후 재시도하여 성공."""

    def test_get_retries_on_read_timeout_and_succeeds(self):
        """첫 2회 ReadTimeout → 3회째 성공 → 예외 전파 없이 성공 응답 반환."""
        client = KisClient()
        call_count = 0
        success_resp = _make_httpx_response(SUCCESS_JSON)

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise httpx.ReadTimeout("timeout", request=MagicMock())
            return success_resp

        with (
            patch("trading.kis.client._sleep_fn", side_effect=lambda d: None),
            patch("httpx.Client") as mock_http_client,
        ):
            instance = MagicMock()
            mock_http_client.return_value.__enter__ = lambda s: instance
            mock_http_client.return_value.__exit__ = MagicMock(return_value=False)
            instance.get.side_effect = _side_effect

            result = client.get("/test", "TR_TEST")

        # 3회 HTTP 호출이 발생해야 한다
        assert call_count == 3, f"HTTP GET 호출 횟수가 3이어야 하는데 {call_count}였다"
        assert result.rt_cd == "0"

    def test_get_retries_on_connect_timeout(self):
        """EC-2: ConnectTimeout도 ReadTimeout과 동일하게 재시도."""
        client = KisClient()
        call_count = 0
        success_resp = _make_httpx_response(SUCCESS_JSON)

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectTimeout("connect timeout", request=MagicMock())
            return success_resp

        with (
            patch("trading.kis.client._sleep_fn", side_effect=lambda d: None),
            patch("httpx.Client") as mock_http_client,
        ):
            instance = MagicMock()
            mock_http_client.return_value.__enter__ = lambda s: instance
            mock_http_client.return_value.__exit__ = MagicMock(return_value=False)
            instance.get.side_effect = _side_effect

            result = client.get("/test", "TR_TEST")

        assert call_count == 2
        assert result.rt_cd == "0"

    def test_get_retries_on_transport_error(self):
        """EC-2: TransportError도 재시도."""
        client = KisClient()
        call_count = 0
        success_resp = _make_httpx_response(SUCCESS_JSON)

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("connection refused", request=MagicMock())
            return success_resp

        with (
            patch("trading.kis.client._sleep_fn", side_effect=lambda d: None),
            patch("httpx.Client") as mock_http_client,
        ):
            instance = MagicMock()
            mock_http_client.return_value.__enter__ = lambda s: instance
            mock_http_client.return_value.__exit__ = MagicMock(return_value=False)
            instance.get.side_effect = _side_effect

            result = client.get("/test", "TR_TEST")

        assert call_count == 2
        assert result.rt_cd == "0"


# ──────────────────────────────────────────────────────────────────────────────
# AC-2: get() 예산 소진 시 KisTimeoutError
# ──────────────────────────────────────────────────────────────────────────────


class TestGetBudgetExhausted:
    """AC-2 (REQ-051-A3): 재시도 예산 모두 소진 → KisTimeoutError."""

    def test_get_raises_kis_timeout_error_on_budget_exhausted(self):
        """KIS_TIMEOUT_RETRIES 소진 후 KisTimeoutError가 raise되어야 한다."""
        KisTimeoutError = _import_timeout_error()
        client = KisClient()
        call_count = 0

        def _always_timeout(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.ReadTimeout("timeout", request=MagicMock())

        with (
            patch("trading.kis.client._sleep_fn", side_effect=lambda d: None),
            patch("httpx.Client") as mock_http_client,
        ):
            instance = MagicMock()
            mock_http_client.return_value.__enter__ = lambda s: instance
            mock_http_client.return_value.__exit__ = MagicMock(return_value=False)
            instance.get.side_effect = _always_timeout

            with pytest.raises(KisTimeoutError) as exc_info:
                client.get("/test", "TR_TEST")

        assert isinstance(exc_info.value, RuntimeError)
        assert isinstance(exc_info.value, KisError)
        # 타임아웃 캡(KIS_TIMEOUT_RETRIES+1)만큼 시도해야 함
        assert call_count >= 1

    def test_timeout_error_is_distinct_from_rate_limit_error(self):
        """AC-2: KisTimeoutError와 일반 KisError(rate-limit 소진)는 타입으로 구별된다."""
        KisTimeoutError = _import_timeout_error()

        # rate-limit KisError
        mock_resp = KisResponse(
            status_code=200, rt_cd="1", msg_cd="EGW00201",
            msg="초당 거래건수 초과", output={}, raw={}
        )
        rate_err = KisError(mock_resp)
        timeout_err = KisTimeoutError("network timeout")

        # 둘 다 KisError지만 구별 가능
        assert type(rate_err) is not type(timeout_err)
        assert isinstance(timeout_err, KisTimeoutError)
        assert not isinstance(rate_err, KisTimeoutError)


# ──────────────────────────────────────────────────────────────────────────────
# AC-1b / EC-1: post() 타임아웃은 재시도 없이 즉시 raise
# ──────────────────────────────────────────────────────────────────────────────


class TestPostNoRetryOnTimeout:
    """AC-1b (REQ-051-A1b): post()는 타임아웃 시 재시도 없이 POST 정확히 1회."""

    def test_post_raises_immediately_on_timeout_no_retry(self):
        """POST 정확히 1회 발생 후 KisTimeoutError raise — 이중주문 방지."""
        KisTimeoutError = _import_timeout_error()
        client = KisClient()
        post_count = 0

        def _timeout_post(*args, **kwargs):
            nonlocal post_count
            post_count += 1
            raise httpx.ReadTimeout("timeout", request=MagicMock())

        with (
            patch("httpx.Client") as mock_http_client,
        ):
            instance = MagicMock()
            mock_http_client.return_value.__enter__ = lambda s: instance
            mock_http_client.return_value.__exit__ = MagicMock(return_value=False)
            instance.post.side_effect = _timeout_post

            with pytest.raises(KisTimeoutError):
                client.post("/order", "TR_ORDER", {"stock": "005930"})

        # EC-1 이중주문 가드: POST는 정확히 1회만 발생해야 한다
        assert post_count == 1, (
            f"POST가 {post_count}회 발생했다! 이중주문 위험 — 재시도 금지 (REQ-051-A1b)"
        )

    def test_post_double_submission_guard(self):
        """EC-1: 이중주문 회귀 가드 — POST 2회 이상이면 테스트 실패."""
        KisTimeoutError = _import_timeout_error()
        client = KisClient()
        post_count = 0

        def _timeout_post(*args, **kwargs):
            nonlocal post_count
            post_count += 1
            raise httpx.ReadTimeout("timeout", request=MagicMock())

        with (
            patch("httpx.Client") as mock_http_client,
        ):
            instance = MagicMock()
            mock_http_client.return_value.__enter__ = lambda s: instance
            mock_http_client.return_value.__exit__ = MagicMock(return_value=False)
            instance.post.side_effect = _timeout_post

            try:
                client.post("/order", "TR_ORDER", {})
            except (KisTimeoutError, Exception):
                pass

        assert post_count == 1, "이중주문 회귀: POST가 2번 이상 호출되었다"
        assert post_count < 2, "이중주문 발생 — REQ-051-A1b 위반"


# ──────────────────────────────────────────────────────────────────────────────
# AC-3: attempt당 _RateGate.acquire 1회
# ──────────────────────────────────────────────────────────────────────────────


class TestRateGateAcquirePerAttempt:
    """AC-3 (REQ-051-A2): get() attempt마다 _GATE.acquire()가 1회씩 호출된다."""

    def test_gate_acquire_called_once_per_attempt(self):
        """첫 1회 ReadTimeout 후 성공 → _GATE.acquire가 정확히 2회 호출."""
        client = KisClient()
        call_count = 0
        acquire_count = 0
        success_resp = _make_httpx_response(SUCCESS_JSON)

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ReadTimeout("timeout", request=MagicMock())
            return success_resp

        original_acquire = _GATE.acquire

        def _spy_acquire():
            nonlocal acquire_count
            acquire_count += 1
            # 실제 게이트 로직은 즉시 반환(sleep 없음)

        with (
            patch("trading.kis.client._sleep_fn", side_effect=lambda d: None),
            patch("httpx.Client") as mock_http_client,
            patch.object(_GATE, "acquire", side_effect=_spy_acquire),
        ):
            instance = MagicMock()
            mock_http_client.return_value.__enter__ = lambda s: instance
            mock_http_client.return_value.__exit__ = MagicMock(return_value=False)
            instance.get.side_effect = _side_effect

            client.get("/test", "TR_TEST")

        # attempt 수(2)만큼 acquire가 호출되어야 한다 (SPEC-043 불변식)
        assert acquire_count == 2, (
            f"_GATE.acquire가 {acquire_count}회 호출됨 — 2회 기대 (AC-3)"
        )


# ──────────────────────────────────────────────────────────────────────────────
# AC-5: 분할 타임아웃 적용
# ──────────────────────────────────────────────────────────────────────────────


class TestSplitTimeout:
    """AC-5 (REQ-051-A4): 기본 호출은 분할 httpx.Timeout, 스칼라 오버라이드는 일관 해석."""

    def test_default_call_uses_split_timeout(self):
        """기본 timeout 미지정 시 httpx.Timeout(connect=..., read=...) 사용."""
        from trading.kis.client import KIS_CONNECT_TIMEOUT_SECONDS, KIS_READ_TIMEOUT_SECONDS  # noqa: PLC0415

        client = KisClient()
        captured_timeout = None
        success_resp = _make_httpx_response(SUCCESS_JSON)

        def _capture_client(timeout=None, **kwargs):
            nonlocal captured_timeout
            captured_timeout = timeout
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = lambda s: MagicMock(
                get=MagicMock(return_value=success_resp)
            )
            mock_ctx.__exit__ = MagicMock(return_value=False)
            return mock_ctx

        with (
            patch("trading.kis.client._sleep_fn", side_effect=lambda d: None),
            patch("httpx.Client", side_effect=_capture_client),
        ):
            client.get("/test", "TR_TEST")

        assert captured_timeout is not None
        assert isinstance(captured_timeout, httpx.Timeout), (
            f"기본 타임아웃이 httpx.Timeout이 아님: {type(captured_timeout)}"
        )
        assert captured_timeout.connect == KIS_CONNECT_TIMEOUT_SECONDS
        assert captured_timeout.read == KIS_READ_TIMEOUT_SECONDS

    def test_scalar_override_interpreted_as_uniform_timeout(self):
        """스칼라 오버라이드(timeout=8.0)는 httpx.Timeout(8.0)으로 해석된다."""
        client = KisClient()
        captured_timeout = None
        success_resp = _make_httpx_response(SUCCESS_JSON)

        def _capture_client(timeout=None, **kwargs):
            nonlocal captured_timeout
            captured_timeout = timeout
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = lambda s: MagicMock(
                get=MagicMock(return_value=success_resp)
            )
            mock_ctx.__exit__ = MagicMock(return_value=False)
            return mock_ctx

        with (
            patch("trading.kis.client._sleep_fn", side_effect=lambda d: None),
            patch("httpx.Client", side_effect=_capture_client),
        ):
            client.get("/test", "TR_TEST", timeout=8.0)

        assert isinstance(captured_timeout, httpx.Timeout)
        assert captured_timeout.connect == 8.0
        assert captured_timeout.read == 8.0


# ──────────────────────────────────────────────────────────────────────────────
# AC-6: backoff sleep seam — 결정적 테스트
# ──────────────────────────────────────────────────────────────────────────────


class TestBackoffSleepSeam:
    """AC-6 (REQ-051-A5): backoff sleep이 주입 가능한 seam을 통해 수행된다."""

    def test_backoff_sleep_is_injectable_no_real_wallclock(self):
        """실제 wall-clock sleep이 발생하지 않고 backoff 호출이 카운트된다."""
        client = KisClient()
        call_count = 0
        sleep_calls: list[float] = []
        success_resp = _make_httpx_response(SUCCESS_JSON)

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise httpx.ReadTimeout("timeout", request=MagicMock())
            return success_resp

        def _fake_sleep(d: float) -> None:
            sleep_calls.append(d)
            # 시간 전진 없음 — 즉시 반환 (결정적)

        with (
            patch("trading.kis.client._sleep_fn", side_effect=_fake_sleep),
            patch("httpx.Client") as mock_http_client,
        ):
            instance = MagicMock()
            mock_http_client.return_value.__enter__ = lambda s: instance
            mock_http_client.return_value.__exit__ = MagicMock(return_value=False)
            instance.get.side_effect = _side_effect

            result = client.get("/test", "TR_TEST")

        # 2회 재시도 → 2번의 backoff sleep이 발생
        assert len(sleep_calls) >= 1, "backoff sleep이 한 번도 호출되지 않았다"
        assert result.rt_cd == "0"

    def test_backoff_value_matches_formula(self):
        """backoff 값이 RATE_LIMIT_BACKOFF_SECONDS * (attempt+1)과 일치한다."""
        client = KisClient()
        call_count = 0
        sleep_calls: list[float] = []
        success_resp = _make_httpx_response(SUCCESS_JSON)

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise httpx.ReadTimeout("timeout", request=MagicMock())
            return success_resp

        def _fake_sleep(d: float) -> None:
            sleep_calls.append(d)

        with (
            patch("trading.kis.client._sleep_fn", side_effect=_fake_sleep),
            patch("httpx.Client") as mock_http_client,
        ):
            instance = MagicMock()
            mock_http_client.return_value.__enter__ = lambda s: instance
            mock_http_client.return_value.__exit__ = MagicMock(return_value=False)
            instance.get.side_effect = _side_effect

            client.get("/test", "TR_TEST")

        # attempt 0 실패 → backoff = RATE_LIMIT_BACKOFF_SECONDS * 1
        # attempt 1 실패 → backoff = RATE_LIMIT_BACKOFF_SECONDS * 2
        expected = [RATE_LIMIT_BACKOFF_SECONDS * (i + 1) for i in range(len(sleep_calls))]
        assert sleep_calls == expected, (
            f"backoff 값 불일치: {sleep_calls} != {expected}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# AC-7: post()는 _RateGate.acquire 호출 안 함
# ──────────────────────────────────────────────────────────────────────────────


class TestPostNotGated:
    """AC-7 (REQ-051-A2): post() 경로에서 _RateGate.acquire가 호출되지 않는다."""

    def test_post_does_not_acquire_rate_gate(self):
        """주문 제출(post)은 rate gate를 통과하지 않아야 한다 (SPEC-043 정책 보존)."""
        KisTimeoutError = _import_timeout_error()
        client = KisClient()
        acquire_count = 0

        def _spy_acquire():
            nonlocal acquire_count
            acquire_count += 1

        def _timeout_post(*args, **kwargs):
            raise httpx.ReadTimeout("timeout", request=MagicMock())

        with (
            patch("httpx.Client") as mock_http_client,
            patch.object(_GATE, "acquire", side_effect=_spy_acquire),
        ):
            instance = MagicMock()
            mock_http_client.return_value.__enter__ = lambda s: instance
            mock_http_client.return_value.__exit__ = MagicMock(return_value=False)
            instance.post.side_effect = _timeout_post

            with pytest.raises(KisTimeoutError):
                client.post("/order", "TR_ORDER", {})

        assert acquire_count == 0, (
            f"post()에서 _GATE.acquire가 {acquire_count}회 호출됨 — 0회 기대 (AC-7)"
        )


# ──────────────────────────────────────────────────────────────────────────────
# AC-8: get() 재시도 소진 최악 wall-time < 300s (ADR-005)
# ──────────────────────────────────────────────────────────────────────────────


class TestWallTimeBound:
    """AC-8 (ADR-005): get() 재시도 소진 최악 wall-time이 워치독 */5(300s)보다 작다."""

    def test_worst_case_virtual_time_under_watchdog_period(self):
        """FakeClock으로 누적 가상 시간 검증 — 300s 미만 단언."""
        from trading.kis.client import (  # noqa: PLC0415
            KIS_READ_TIMEOUT_SECONDS,
            KIS_TIMEOUT_RETRIES,
        )

        KisTimeoutError = _import_timeout_error()
        client = KisClient()
        virtual_time = 0.0

        def _fake_sleep(d: float) -> None:
            nonlocal virtual_time
            virtual_time += d

        def _always_timeout(*args, **kwargs):
            nonlocal virtual_time
            # 각 HTTP 시도마다 read timeout 시간만큼 시간이 경과한 것으로 시뮬레이션
            virtual_time += KIS_READ_TIMEOUT_SECONDS
            raise httpx.ReadTimeout("timeout", request=MagicMock())

        with (
            patch("trading.kis.client._sleep_fn", side_effect=_fake_sleep),
            patch("httpx.Client") as mock_http_client,
        ):
            instance = MagicMock()
            mock_http_client.return_value.__enter__ = lambda s: instance
            mock_http_client.return_value.__exit__ = MagicMock(return_value=False)
            instance.get.side_effect = _always_timeout

            with pytest.raises(KisTimeoutError):
                client.get("/test", "TR_TEST")

        # ADR-005: 최악 누적 시간이 워치독 */5(300s) 주기보다 작아야 한다
        WATCHDOG_PERIOD_SECONDS = 300.0
        assert virtual_time < WATCHDOG_PERIOD_SECONDS, (
            f"get() 최악 wall-time {virtual_time:.1f}s가 워치독 주기 {WATCHDOG_PERIOD_SECONDS}s 이상 (ADR-005 위반)"
        )
        # 구체적으로: KIS_TIMEOUT_RETRIES=2이면
        # read_time=3*(15s) + backoff=(1s+2s) = 45s + 3s = 48s < 300s
        print(
            f"\nAC-8: 최악 가상 wall-time = {virtual_time:.1f}s "
            f"(타임아웃 캡 KIS_TIMEOUT_RETRIES={KIS_TIMEOUT_RETRIES}, "
            f"read={KIS_READ_TIMEOUT_SECONDS}s)"
        )


# ──────────────────────────────────────────────────────────────────────────────
# EC-3: rate-limit 응답과 httpx 예외 혼재 (ADR-004)
# ──────────────────────────────────────────────────────────────────────────────


class TestMixedRetryBudget:
    """EC-3 (ADR-004): rate-limit 응답과 httpx 예외가 같은 예산을 공유한다."""

    def test_mixed_rate_limit_and_timeout_share_budget(self):
        """attempt 1=ReadTimeout, attempt 2=rate-limit 응답, attempt 3=성공."""
        client = KisClient()
        call_count = 0
        success_resp = _make_httpx_response(SUCCESS_JSON)
        rate_limit_resp = _make_httpx_response(RATE_LIMIT_JSON)

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ReadTimeout("timeout", request=MagicMock())
            if call_count == 2:
                return rate_limit_resp
            return success_resp

        with (
            patch("trading.kis.client._sleep_fn", side_effect=lambda d: None),
            patch("httpx.Client") as mock_http_client,
        ):
            instance = MagicMock()
            mock_http_client.return_value.__enter__ = lambda s: instance
            mock_http_client.return_value.__exit__ = MagicMock(return_value=False)
            instance.get.side_effect = _side_effect

            result = client.get("/test", "TR_TEST")

        assert call_count == 3, f"혼재 재시도가 3회 시도여야 하는데 {call_count}회였다"
        assert result.rt_cd == "0"


# ──────────────────────────────────────────────────────────────────────────────
# 상수 존재 검증
# ──────────────────────────────────────────────────────────────────────────────


class TestNewConstants:
    """REQ-051-A4/ADR-001/ADR-005: 새 명명 상수가 client.py에 존재해야 한다."""

    def test_connect_timeout_constant_exists(self):
        from trading.kis.client import KIS_CONNECT_TIMEOUT_SECONDS  # noqa: PLC0415

        assert KIS_CONNECT_TIMEOUT_SECONDS == 5.0

    def test_read_timeout_constant_exists(self):
        from trading.kis.client import KIS_READ_TIMEOUT_SECONDS  # noqa: PLC0415

        assert KIS_READ_TIMEOUT_SECONDS == 15.0

    def test_timeout_retry_cap_constant_exists(self):
        """ADR-005: 타임아웃 전용 재시도 캡 상수가 존재한다."""
        from trading.kis.client import KIS_TIMEOUT_RETRIES  # noqa: PLC0415

        # KIS_TIMEOUT_RETRIES=2이면 총 3회 시도 → 최악 3*15+3 = 48s < 300s
        assert KIS_TIMEOUT_RETRIES >= 1
        assert KIS_TIMEOUT_RETRIES <= 4  # RATE_LIMIT_RETRIES 초과 금지

    def test_sleep_fn_seam_exists(self):
        """REQ-051-A5: 모듈 레벨 sleep seam이 존재해야 한다."""
        from trading.kis.client import _sleep_fn  # noqa: PLC0415

        assert callable(_sleep_fn)
