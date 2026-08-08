"""KRX 서킷 HALF_OPEN probe 고착 (재현 우선).

2026-08-08 사고: KRX 비밀번호 만료로 서킷이 OPEN 됐다. 운영자가 비밀번호를
복구해 pykrx 직접 호출이 정상 동작하는 것을 확인한 뒤에도 ``refresh_ohlcv`` 는
52/52 전부 ``KRX 서킷 HALF_OPEN probe 진행 중. open_until=2026-08-08 09:15``
로 실패했다. open_until 이 7시간 지났는데도 자가 해제되지 않았다.

원인: ``check_or_raise`` 는 HALF_OPEN 이면 무조건 차단하는데, HALF_OPEN 을
빠져나가는 유일한 경로는 probe 의 ``record_success``/``record_failure`` 다.
probe 호출자(``refresh_market_data._call_with_timeout``)는 타임아웃 시 호출을
버리므로 결과가 기록되지 않을 수 있고, 그 순간 서킷은 프로세스 수명 내내
고착된다. 재시작해도 store 가 HALF_OPEN 을 그대로 복원하면 마찬가지다.

즉 **엔드포인트가 복구돼도 시스템은 수동 개입 전까지 멈춰 있는다.**

차단기 자체는 유지해야 한다 — 반복 로그인으로 집 IP 가 KRX 에서 차단된 전력이
있다. 고칠 것은 "버려진 probe 를 영원히 기다리는" 부분뿐이며, 자가 해제 후에도
지수 백오프는 그대로 적용돼야 한다.

AC-1  데드라인 안의 probe 는 여전히 차단된다 (동시 probe 방지 유지).
AC-2  데드라인을 넘긴 probe 는 실패로 간주해 서킷을 다시 OPEN 한다.
AC-3  재-OPEN 후 쿨다운이 지나면 새 probe 가 허용된다 (자가 해제 완료).
AC-4  자가 해제가 백오프를 초기화하지 않는다 (하머링 금지 유지).
AC-5  store 에 HALF_OPEN 이 남아 있어도 복원 후 영구 차단되지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading.data.krx_circuit_breaker import (
    _COOLDOWN_STEPS,
    CircuitState,
    InMemoryStateStore,
    KrxCircuitBreaker,
    KrxCircuitOpen,
)

T0 = datetime(2026, 8, 8, 9, 0, 0, tzinfo=UTC)
FIRST_COOLDOWN = _COOLDOWN_STEPS[0]


def _breaker(store: InMemoryStateStore | None = None) -> KrxCircuitBreaker:
    return KrxCircuitBreaker(
        failure_threshold=2,
        _notify_fn=lambda *_a, **_k: None,
        _state_store=store or InMemoryStateStore(),
    )


def _drive_to_half_open(br: KrxCircuitBreaker) -> datetime:
    """연속 실패로 OPEN → 쿨다운 경과 → HALF_OPEN(probe 허용) 까지 몬다.

    반환값은 probe 가 허용된 시각.
    """
    br.record_failure(now=T0)
    br.record_failure(now=T0)
    assert br.state is CircuitState.OPEN

    probe_at = T0 + FIRST_COOLDOWN
    br.check_or_raise(now=probe_at)  # probe 1회 허용 — 예외 없이 통과해야 한다
    assert br.state is CircuitState.HALF_OPEN
    return probe_at


class TestProbeInFlightStillBlocks:
    def test_concurrent_probe_is_rejected(self):
        """AC-1: 진행 중인 probe 가 있으면 다른 호출은 여전히 막는다."""
        br = _breaker()
        probe_at = _drive_to_half_open(br)

        with pytest.raises(KrxCircuitOpen):
            br.check_or_raise(now=probe_at + timedelta(seconds=30))


class TestAbandonedProbeSelfHeals:
    def test_probe_past_deadline_reopens_circuit(self):
        """AC-2: 결과가 기록되지 않은 probe 를 영원히 기다리지 않는다."""
        br = _breaker()
        probe_at = _drive_to_half_open(br)

        # probe 가 record_success/record_failure 없이 사라졌다(타임아웃 등).
        way_later = probe_at + timedelta(hours=7)
        with pytest.raises(KrxCircuitOpen):
            br.check_or_raise(now=way_later)

        # 고착이 아니라 OPEN 으로 되돌아가 재시도 일정이 잡혀야 한다.
        assert br.state is CircuitState.OPEN
        assert br.open_until is not None
        assert br.open_until > way_later

    def test_new_probe_allowed_after_reopen_cooldown(self):
        """AC-3: 재-OPEN 쿨다운이 지나면 실제로 다시 probe 할 수 있다."""
        br = _breaker()
        probe_at = _drive_to_half_open(br)

        way_later = probe_at + timedelta(hours=7)
        with pytest.raises(KrxCircuitOpen):
            br.check_or_raise(now=way_later)

        assert br.open_until is not None
        # 쿨다운 경과 후에는 예외 없이 통과해야 한다 — 자가 해제 완료.
        br.check_or_raise(now=br.open_until + timedelta(seconds=1))
        assert br.state is CircuitState.HALF_OPEN

    def test_self_heal_keeps_backoff(self):
        """AC-4: 자가 해제가 쿨다운을 처음으로 되돌리면 하머링이 된다."""
        br = _breaker()
        probe_at = _drive_to_half_open(br)

        way_later = probe_at + timedelta(hours=7)
        with pytest.raises(KrxCircuitOpen):
            br.check_or_raise(now=way_later)

        assert br.open_until is not None
        # 두 번째 쿨다운은 첫 번째보다 길어야 한다 (지수 백오프 유지).
        assert br.open_until - way_later > FIRST_COOLDOWN


class TestRestoreNeverDeadlocks:
    def test_half_open_in_store_does_not_block_forever(self):
        """AC-5: 재시작이 고착을 영속화하면 안 된다."""
        store = InMemoryStateStore()
        store.save(
            {
                "state": "HALF_OPEN",
                "open_until": (T0 - timedelta(hours=7)).isoformat(),
                "cooldown_level": 1,
                "consecutive_failures": 2,
            }
        )

        br = _breaker(store)
        # 복원 직후, 쿨다운이 한참 지난 시점이라면 probe 가 가능해야 한다.
        br.check_or_raise(now=T0)
