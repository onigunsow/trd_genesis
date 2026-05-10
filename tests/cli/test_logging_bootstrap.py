"""Tests for the CLI-level root logger bootstrap.

SPEC-TRADING-017: Verify that ``trading.cli.main`` configures the root
logger before subcommand dispatch so all long-running services emit logs
to stdout.

Acceptance criteria covered:

- AC-017-1: Root logger has a handler with the spec format after main().
- AC-017-3: ``TRADING_LOG_LEVEL`` overrides the default INFO level.
- AC-017-4: Invalid ``TRADING_LOG_LEVEL`` falls back to INFO with a single
  WARNING line.
- AC-017-5: Short-lived subcommands (``calendar``) still complete.
- AC-017-6: Idempotency -- repeated calls do not duplicate the bootstrap
  handler.
- AC-017-7: Format string is byte-identical to the spec.

Test isolation note: pytest's logging plugin attaches its own
``LogCaptureHandler`` to the root logger before each test's call phase --
*after* fixtures finish setup. Tests that need to observe the
bootstrap-installed handler therefore call ``_strip_root_handlers()`` at
the start of the test body, *not* in the autouse fixture (which would be
overwritten before the test runs).
"""

from __future__ import annotations

import logging
from typing import Iterator

import pytest

EXPECTED_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def _bootstrap_handlers(root: logging.Logger) -> list[logging.Handler]:
    """Return handlers that look like they were installed by the CLI bootstrap.

    Identified by an exact match on the spec format string. This avoids
    coupling to pytest's own ``LogCaptureHandler`` instances.
    """
    return [
        h
        for h in root.handlers
        if h.formatter is not None and h.formatter._fmt == EXPECTED_FORMAT
    ]


def _strip_root_handlers() -> list[logging.Handler]:
    """Detach all root logger handlers and return the originals.

    Caller is responsible for restoring via ``_restore_root_handlers``.
    Use inside a test body (not in a fixture) so pytest's logging plugin
    cannot reinstall its capture handler between fixture teardown and
    the test call.
    """
    root = logging.getLogger()
    saved = list(root.handlers)
    for h in saved:
        root.removeHandler(h)
    return saved


def _restore_root_handlers(saved: list[logging.Handler]) -> None:
    """Reverse ``_strip_root_handlers``: clear, then re-attach the originals."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in saved:
        root.addHandler(h)


@pytest.fixture(autouse=True)
def _restore_root_logger_state() -> Iterator[None]:
    """Snapshot root logger state and restore after each test.

    Only runs in teardown to undo any handler/level mutations the test
    or the CLI bootstrap performed. Setup is intentionally a no-op
    because pytest re-adds its capture handler after fixture setup
    completes anyway.
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        yield
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(saved_level)


def _call_main_with_calendar() -> int:
    """Invoke ``trading.cli.main`` with a safe, short-lived subcommand.

    ``calendar`` is a pure-stdlib subcommand that does not touch the DB,
    KIS, or Telegram, so it is safe in a test environment.
    """
    from trading.cli import main

    return main(["calendar", "2026-05-11"])


def test_main_registers_root_handler(monkeypatch, capsys):
    """AC-017-1: After main() runs, the root logger has the bootstrap handler."""
    monkeypatch.delenv("TRADING_LOG_LEVEL", raising=False)
    _strip_root_handlers()

    rc = _call_main_with_calendar()
    capsys.readouterr()  # discard calendar table

    assert rc == 0
    assert len(_bootstrap_handlers(logging.getLogger())) == 1


def test_default_level_is_info(monkeypatch, capsys):
    """AC-017-3 default branch: unset env var -> level is INFO."""
    monkeypatch.delenv("TRADING_LOG_LEVEL", raising=False)
    _strip_root_handlers()

    _call_main_with_calendar()
    capsys.readouterr()

    assert logging.getLogger().level == logging.INFO


def test_env_var_sets_debug(monkeypatch, capsys):
    """AC-017-3 override branch: TRADING_LOG_LEVEL=DEBUG -> level is DEBUG."""
    monkeypatch.setenv("TRADING_LOG_LEVEL", "DEBUG")
    _strip_root_handlers()

    _call_main_with_calendar()
    capsys.readouterr()

    assert logging.getLogger().level == logging.DEBUG


def test_case_insensitive_env_var(monkeypatch, capsys):
    """REQ-017-1-3 (a): comparison is case-insensitive."""
    monkeypatch.setenv("TRADING_LOG_LEVEL", "debug")
    _strip_root_handlers()

    _call_main_with_calendar()
    capsys.readouterr()

    assert logging.getLogger().level == logging.DEBUG


def test_invalid_env_var_falls_back_to_info_with_warning(
    monkeypatch, capsys, caplog
):
    """AC-017-4: Invalid value -> INFO + a single WARNING line.

    This test does NOT strip root handlers because we rely on caplog to
    capture the WARNING. caplog's handler is sufficient; our bootstrap
    becomes a stdlib no-op for handler installation, but the level is
    still re-applied and the warning is still emitted.
    """
    monkeypatch.setenv("TRADING_LOG_LEVEL", "BOGUS")
    caplog.set_level(logging.WARNING)

    _call_main_with_calendar()
    capsys.readouterr()

    assert logging.getLogger().level == logging.INFO

    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "BOGUS" in r.getMessage()
    ]
    assert len(warnings) == 1, (
        f"expected exactly one WARNING mentioning BOGUS, got {len(warnings)}: "
        f"{[r.getMessage() for r in caplog.records]}"
    )


def test_idempotent_double_call(monkeypatch, capsys):
    """AC-017-6: Calling main twice does not install a second bootstrap handler."""
    monkeypatch.delenv("TRADING_LOG_LEVEL", raising=False)
    _strip_root_handlers()

    _call_main_with_calendar()
    capsys.readouterr()
    first = len(_bootstrap_handlers(logging.getLogger()))

    _call_main_with_calendar()
    capsys.readouterr()
    second = len(_bootstrap_handlers(logging.getLogger()))

    assert first == 1, f"first call installed {first} bootstrap handler(s); expected 1"
    assert second == 1, (
        f"second call grew bootstrap handler count from {first} to {second}"
    )


def test_format_string_matches_spec(monkeypatch, capsys):
    """REQ-017-1-4: Format string is byte-identical to the spec."""
    monkeypatch.delenv("TRADING_LOG_LEVEL", raising=False)
    _strip_root_handlers()

    _call_main_with_calendar()
    capsys.readouterr()

    bootstrap = _bootstrap_handlers(logging.getLogger())
    assert len(bootstrap) == 1, (
        f"expected exactly one bootstrap handler; got {len(bootstrap)}"
    )
    assert bootstrap[0].formatter._fmt == EXPECTED_FORMAT
