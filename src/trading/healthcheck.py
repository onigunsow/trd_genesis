"""M1 Healthcheck — verify env, KIS reachability, Telegram reachability, DB reachability.

Implements REQ-INFRA-01-3 (healthcheck within 60s).

Run inside container:
    python -m trading.healthcheck            # verbose
    python -m trading.healthcheck --quiet    # used by Docker HEALTHCHECK directive
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Literal

import httpx

from trading.config import TradingMode, get_settings

PAPER_BASE = "https://openapivts.koreainvestment.com:29443"
LIVE_BASE = "https://openapi.koreainvestment.com:9443"
TELEGRAM_BASE = "https://api.telegram.org"


CheckResult = tuple[Literal["ok", "warn", "fail"], str]


def check_env() -> CheckResult:
    try:
        s = get_settings()
        # Touch each subsection to trigger pydantic validation.
        _ = s.kis.paper_app_key
        _ = s.telegram.bot_token
        _ = s.postgres.user
        return ("ok", f"env loaded, mode={s.trading_mode.value}")
    except Exception as e:  # noqa: BLE001
        return ("fail", f"env load error: {e!r}")


def check_kis_reachable() -> CheckResult:
    """Probe KIS endpoint TCP/TLS reachability without burning a token request."""
    s = get_settings()
    base = LIVE_BASE if s.trading_mode == TradingMode.LIVE else PAPER_BASE
    try:
        # OPTIONS or simple GET on a non-token endpoint to avoid 1-min reissue limit.
        with httpx.Client(timeout=5.0, verify=True) as client:
            r = client.get(f"{base}/", headers={"User-Agent": "trading/healthcheck"})
        # KIS returns 404 on root, but reachable host = 200/4xx with non-empty status.
        if r.status_code in (200, 301, 302, 401, 403, 404, 405):
            return ("ok", f"KIS {s.trading_mode.value} reachable ({r.status_code})")
        return ("warn", f"KIS unexpected status {r.status_code}")
    except Exception as e:  # noqa: BLE001
        return ("warn", f"KIS unreachable: {e!r}")


def check_telegram_reachable() -> CheckResult:
    s = get_settings()
    token = s.telegram.bot_token.get_secret_value()
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{TELEGRAM_BASE}/bot{token}/getMe")
        if r.status_code == 200 and r.json().get("ok"):
            return ("ok", f"telegram bot @{r.json()['result'].get('username')}")
        return ("fail", f"telegram getMe status={r.status_code}")
    except Exception as e:  # noqa: BLE001
        return ("fail", f"telegram error: {e!r}")


def check_db_reachable() -> CheckResult:
    """Connect to Postgres using DATABASE_URL (in-container) or skip outside container."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return ("warn", "DATABASE_URL not set (running outside container?)")
    try:
        # Lazy import: psycopg may not be available outside container.
        import psycopg  # type: ignore[import-not-found]
    except ImportError:
        return ("warn", "psycopg not installed (running outside container?)")
    try:
        # psycopg accepts both libpq DSN and SQLAlchemy-style URL prefix; strip the SA prefix.
        plain = dsn.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(plain, connect_timeout=5) as conn:  # type: ignore[arg-type]
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return ("ok", "postgres reachable")
    except Exception as e:  # noqa: BLE001
        return ("fail", f"postgres error: {e!r}")


# @MX:ANCHOR: SPEC-TRADING-016 REQ-016-1-2 — boot-time build commit verification.
# @MX:REASON: Past zero-trade incidents (adeadeb, 2172cdf) traced to containers running
# stale code because `__pycache__` and Jinja2 caches survived without `--no-cache` rebuild.
# This check guarantees the running image was built from the host's current git HEAD.
def check_build_commit() -> CheckResult:
    """Verify the container's baked-in commit matches the host's HEAD passed via env.

    On `fail`, the main loop calls `system_error("BOOT", BuildCommitMismatch(...))`
    so the user gets an immediate Telegram alert instead of silent zero-trade days.
    """
    try:
        with open("/app/.build_commit", encoding="utf-8") as f:
            container_sha = f.read().strip()
    except FileNotFoundError:
        return ("warn", "no .build_commit file (likely dev/legacy image)")
    except Exception as e:  # noqa: BLE001
        return ("warn", f"could not read /app/.build_commit: {e!r}")

    if not container_sha or container_sha == "unknown":
        return ("warn", "container BUILD_COMMIT=unknown (not built via `make redeploy`?)")

    host_sha = os.environ.get("HOST_BUILD_COMMIT", "").strip()
    if not host_sha or host_sha == "unknown":
        # Compose did not inject HOST_BUILD_COMMIT — likely raw `docker compose up`,
        # not a `make redeploy`. Warn but do not crash the container.
        return ("warn", f"HOST_BUILD_COMMIT not set; container commit={container_sha[:8]}")

    if container_sha != host_sha:
        return (
            "fail",
            f"BUILD MISMATCH: container={container_sha[:8]} host={host_sha[:8]} "
            f"(re-run `make redeploy`)",
        )

    return ("ok", f"build commit verified: {container_sha[:8]}")


CHECKS = (
    ("env", check_env),
    ("kis", check_kis_reachable),
    ("telegram", check_telegram_reachable),
    ("db", check_db_reachable),
    ("build", check_build_commit),
)


def _alert_boot_failure(check_name: str, message: str) -> None:
    """SPEC-TRADING-016 REQ-016-1-2: send Telegram alert on critical boot failure.

    Best-effort — never raise. Used for build_commit mismatches so the user
    is notified even when the container is exiting.
    """
    try:
        # Lazy import: telegram module pulls in config/httpx; we want healthcheck
        # to remain importable even if telegram setup is broken.
        from trading.alerts.telegram import system_error  # noqa: WPS433

        system_error(
            "BOOT",
            RuntimeError(f"healthcheck:{check_name} failed"),
            context=message,
        )
    except Exception:  # noqa: BLE001
        # Last resort: if we cannot send Telegram, we still want exit-code 1
        # so docker shows the container as unhealthy.
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="trading healthcheck (M1)")
    parser.add_argument("--quiet", action="store_true", help="suppress output, exit code only")
    args = parser.parse_args(argv)

    fail = False
    for name, fn in CHECKS:
        try:
            level, msg = fn()
        except Exception as e:  # noqa: BLE001
            level, msg = "fail", f"unhandled: {e!r}"
        if not args.quiet:
            marker = {"ok": "OK ", "warn": "WRN", "fail": "ERR"}[level]
            print(f"[{marker}] {name:<10} {msg}")
        if level == "fail":
            fail = True
            # Build-commit mismatch is the most likely silent killer (see SPEC-016).
            # Surface it via Telegram so the user sees it even if `--quiet`.
            if name == "build":
                _alert_boot_failure(name, msg)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
