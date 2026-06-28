"""KRX 데이터 포털 서킷 브레이커.

근본 원인 (2026-06-28 사고):
  KRX 포털이 IP를 403 차단 → pykrx가 실패 때마다 공격적 재로그인 →
  죽은 엔드포인트를 hammer → 차단 장기화.

목표: 연속 실패가 임계에 달하면 pykrx 호출 자체를 멈춰
hammer-while-blocked 패턴을 완전히 차단한다.

상태 전이:
  CLOSED → OPEN (연속 실패 ≥ failure_threshold)
  OPEN   → HALF_OPEN (open_until 경과 후 1회 probe 허용)
  HALF_OPEN → CLOSED (probe 성공)
  HALF_OPEN → OPEN (probe 실패, 더 긴 쿨다운)

쿨다운 지수 백오프: 15m → 1h → 6h → 24h(상한)

영속화: _state_store seam 을 통해 DB(system_state) 또는 인메모리에
상태를 저장한다. 기본은 InMemoryStateStore (테스트·단독 실행용).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Protocol

LOG = logging.getLogger(__name__)

# 쿨다운 단계: [0] 첫 OPEN, [1] 두 번째, [2] 세 번째, [3+] 상한
_COOLDOWN_STEPS: list[timedelta] = [
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=6),
    timedelta(hours=24),  # 상한 — 이 이상 증가하지 않음
]


class CircuitState(Enum):
    """서킷 브레이커 상태."""
    CLOSED = "CLOSED"       # 정상 — pykrx 호출 허용
    OPEN = "OPEN"           # 차단 — pykrx 호출 거부
    HALF_OPEN = "HALF_OPEN" # 쿨다운 후 1회 probe 허용 중


class KrxCircuitOpen(Exception):
    """서킷 OPEN 상태에서 pykrx 호출을 시도했을 때 발생하는 예외."""


class StateStore(Protocol):
    """서킷 상태 영속화 인터페이스 (DB 또는 인메모리 구현)."""

    def save(self, state: dict[str, Any]) -> None:
        """현재 서킷 상태를 저장한다."""
        ...

    def load(self) -> dict[str, Any] | None:
        """저장된 서킷 상태를 반환한다. 없으면 None."""
        ...


class InMemoryStateStore:
    """테스트 및 단독 실행용 인메모리 StateStore."""

    def __init__(self) -> None:
        self._data: dict[str, Any] | None = None

    def save(self, state: dict[str, Any]) -> None:
        self._data = dict(state)

    def load(self) -> dict[str, Any] | None:
        return dict(self._data) if self._data is not None else None


class SystemStateStore:
    """system_state DB 컬럼을 통해 서킷 상태를 영속화하는 StateStore.

    DB 연결 없이도 graceful-fail: 저장/로드 실패는 WARNING으로 기록하고
    인메모리 fallback을 유지한다.
    """

    def save(self, state: dict[str, Any]) -> None:
        """서킷 상태를 system_state에 저장한다. DB 오류는 swallow."""
        try:
            from trading.db.session import update_system_state

            update_system_state(
                krx_circuit_state=state.get("state", "CLOSED"),
                krx_circuit_open_until=(
                    state.get("open_until")  # ISO 문자열 또는 None
                ),
                krx_circuit_cooldown_level=state.get("cooldown_level", 0),
                krx_circuit_consecutive_failures=state.get(
                    "consecutive_failures", 0
                ),
                updated_by="krx_circuit_breaker",
            )
        except Exception:
            LOG.warning("KRX 서킷 상태 DB 저장 실패 (swallowed)", exc_info=True)

    def load(self) -> dict[str, Any] | None:
        """system_state에서 서킷 상태를 로드한다. 컬럼 없으면 None."""
        try:
            from trading.db.session import get_system_state

            row = get_system_state()
            raw_state = row.get("krx_circuit_state")
            if raw_state is None:
                return None
            raw_until = row.get("krx_circuit_open_until")
            open_until_iso = (
                raw_until.isoformat() if hasattr(raw_until, "isoformat") else raw_until
            )
            return {
                "state": raw_state,
                "open_until": open_until_iso,
                "cooldown_level": row.get("krx_circuit_cooldown_level", 0),
                "consecutive_failures": row.get(
                    "krx_circuit_consecutive_failures", 0
                ),
            }
        except Exception:
            LOG.warning("KRX 서킷 상태 DB 로드 실패 (swallowed)", exc_info=True)
            return None


# @MX:ANCHOR: [AUTO] KRX 서킷 브레이커 핵심 — fetch_ohlcv/flows/fundamentals와 universe가 공유
# @MX:REASON: fan_in >= 4 (pykrx_adapter 3개 함수 + universe._fetch_kospi200_from_pykrx);
#             OPEN 상태 판정·쿨다운 계산·알림 로직이 이 클래스에 집중되어
#             모든 호출 경로가 동일한 서킷 상태를 공유한다.
# @MX:SPEC: SPEC-TRADING-KRX-CB-001
class KrxCircuitBreaker:
    """KRX pykrx 호출 서킷 브레이커.

    Args:
        failure_threshold: 연속 실패 임계 (기본 3).
        _notify_fn: 알림 콜백 (기본 system_briefing). 테스트 seam.
        _state_store: 상태 영속화 구현 (기본 InMemoryStateStore). 테스트 seam.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        *,
        _notify_fn: Callable[[str, str], Any] | None = None,
        _state_store: StateStore | None = None,
    ) -> None:
        self._threshold = failure_threshold
        self._notify_fn = _notify_fn or _default_notify
        self._store = _state_store or InMemoryStateStore()
        self._lock = threading.Lock()

        # 인메모리 상태 (영속화 외 빠른 접근)
        self._state = CircuitState.CLOSED
        self._open_until: datetime | None = None
        self._cooldown_level: int = 0       # 지수 백오프 단계
        self._consecutive_failures: int = 0
        self._in_half_open: bool = False    # probe 허용 여부 플래그

        # 시작 시 영속 상태 복원
        self._restore_from_store()

    # ------------------------------------------------------------------
    # 공개 상태 접근자
    # ------------------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def open_until(self) -> datetime | None:
        return self._open_until

    # ------------------------------------------------------------------
    # 핵심 메서드
    # ------------------------------------------------------------------

    def check_or_raise(self, *, now: datetime | None = None) -> None:
        """현재 서킷 상태를 확인한다.

        - CLOSED: 즉시 반환 (정상 흐름).
        - OPEN + 쿨다운 미경과: KrxCircuitOpen 발생.
        - OPEN + 쿨다운 경과: HALF_OPEN으로 전이, 1회 probe 허용.
        - HALF_OPEN: probe 진행 중 — 다음 check_or_raise()는 차단.
        """
        now = now or datetime.now(UTC)
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return

            if self._state == CircuitState.HALF_OPEN:
                # 이미 probe 중 — 다음 호출은 차단
                raise KrxCircuitOpen(
                    f"KRX 서킷 HALF_OPEN probe 진행 중. "
                    f"open_until={self._open_until}"
                )

            # OPEN 상태
            assert self._state == CircuitState.OPEN
            if self._open_until is not None and now >= self._open_until:
                # 쿨다운 경과 → HALF_OPEN으로 전이
                self._state = CircuitState.HALF_OPEN
                self._in_half_open = True
                LOG.info(
                    "KRX 서킷 HALF_OPEN — probe 1회 허용 (open_until=%s 경과)",
                    self._open_until,
                )
                return  # probe 허용

            # 쿨다운 미경과 → 차단
            raise KrxCircuitOpen(
                f"KRX 서킷 OPEN (차단 중). open_until={self._open_until}"
            )

    def record_success(self, *, now: datetime | None = None) -> None:
        """pykrx 호출 성공 — 연속 실패 카운터 리셋, 서킷 CLOSE."""
        now = now or datetime.now(UTC)
        with self._lock:
            self._consecutive_failures = 0
            prev_state = self._state
            if self._state in (CircuitState.OPEN, CircuitState.HALF_OPEN):
                self._state = CircuitState.CLOSED
                self._open_until = None
                self._cooldown_level = 0
                self._in_half_open = False
                LOG.info("KRX 서킷 CLOSED — probe 성공으로 정상화")
                self._persist()
            elif prev_state == CircuitState.CLOSED:
                pass  # 정상 흐름, 영속 불필요

    def record_failure(self, *, now: datetime | None = None) -> None:
        """pykrx 호출 실패 — 연속 실패 카운터 증가, 임계 도달 시 OPEN.

        OPEN 상태에서 open_until이 경과한 후 실패가 기록되면
        (half-open probe 실패에 해당) 더 긴 쿨다운으로 re-open한다.
        """
        now = now or datetime.now(UTC)
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                # probe 실패 → re-open (더 긴 쿨다운, 에피소드 카운터 유지)
                self._in_half_open = False
                self._open_circuit(now=now, notify=False)
                return

            if self._state == CircuitState.OPEN:
                # OPEN 중 open_until 경과 후 실패 = half-open probe 실패
                if self._open_until is not None and now >= self._open_until:
                    self._open_circuit(now=now, notify=False)
                # open_until 미경과 중 실패는 쿨다운 갱신 없음
                return

            # CLOSED 상태에서 연속 실패 누적
            self._consecutive_failures += 1

            if self._consecutive_failures >= self._threshold:
                # closed → open 전이
                self._open_circuit(now=now, notify=True)
            else:
                # 임계 미달 — 카운터만 갱신, 영속
                self._persist()

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _open_circuit(self, *, now: datetime, notify: bool) -> None:
        """서킷을 OPEN으로 전이하고, 쿨다운과 알림을 처리한다."""
        cooldown = _COOLDOWN_STEPS[min(self._cooldown_level, len(_COOLDOWN_STEPS) - 1)]
        self._open_until = now + cooldown
        self._state = CircuitState.OPEN
        LOG.warning(
            "KRX 서킷 OPEN — 연속 실패 %d회, 쿨다운 %s, open_until=%s",
            self._consecutive_failures,
            cooldown,
            self._open_until,
        )
        if notify:
            try:
                self._notify_fn(
                    "KRX 서킷 OPEN",
                    f"KRX pykrx 연속 실패 {self._consecutive_failures}회 "
                    f"→ 서킷 차단. {cooldown} 후 재시도. "
                    f"open_until={self._open_until.isoformat()}",
                )
            except Exception:
                LOG.warning("KRX 서킷 알림 발송 실패 (swallowed)", exc_info=True)
        # 다음 쿨다운 단계로 진행 (상한 고정)
        if self._cooldown_level < len(_COOLDOWN_STEPS) - 1:
            self._cooldown_level += 1
        self._persist()

    def _persist(self) -> None:
        """현재 상태를 store에 저장한다. 실패해도 인메모리 상태는 유지."""
        try:
            self._store.save({
                "state": self._state.value,
                "open_until": (
                    self._open_until.isoformat() if self._open_until else None
                ),
                "cooldown_level": self._cooldown_level,
                "consecutive_failures": self._consecutive_failures,
            })
        except Exception:
            LOG.warning("KRX 서킷 상태 저장 실패 (swallowed)", exc_info=True)

    def _restore_from_store(self) -> None:
        """store에서 상태를 복원한다. 데이터 없으면 CLOSED로 시작."""
        try:
            data = self._store.load()
            if not data:
                return
            raw_state = data.get("state", "CLOSED")
            self._state = CircuitState(raw_state)
            raw_until = data.get("open_until")
            if raw_until:
                # ISO 문자열 또는 datetime 모두 처리
                if isinstance(raw_until, str):
                    self._open_until = datetime.fromisoformat(raw_until)
                    # timezone-naive → UTC로 보정
                    if self._open_until.tzinfo is None:
                        self._open_until = self._open_until.replace(
                            tzinfo=UTC
                        )
                else:
                    self._open_until = raw_until
            self._cooldown_level = int(data.get("cooldown_level", 0))
            self._consecutive_failures = int(
                data.get("consecutive_failures", 0)
            )
        except Exception:
            LOG.warning("KRX 서킷 상태 복원 실패 — CLOSED로 초기화", exc_info=True)
            self._state = CircuitState.CLOSED


# ---------------------------------------------------------------------------
# 프로세스 단일 공유 인스턴스 (singleton)
# ---------------------------------------------------------------------------

_SHARED_BREAKER: KrxCircuitBreaker | None = None
_SHARED_LOCK = threading.Lock()


def _default_notify(category: str, message: str) -> None:
    """기본 알림 — trading.alerts.telegram.system_briefing 위임."""
    try:
        from trading.alerts.telegram import system_briefing

        system_briefing(category, message)
    except Exception:
        LOG.warning("KRX 서킷 알림(system_briefing) 발송 실패 (swallowed)", exc_info=True)


def _get_shared_breaker() -> KrxCircuitBreaker:
    """프로세스-글로벌 KRX 서킷 브레이커 인스턴스를 반환한다.

    스케줄러 재시작·다중 모듈 임포트 시에도 하나의 인스턴스를 공유한다.
    상태는 SystemStateStore를 통해 DB에 영속화된다.
    """
    global _SHARED_BREAKER
    if _SHARED_BREAKER is None:
        with _SHARED_LOCK:
            if _SHARED_BREAKER is None:
                _SHARED_BREAKER = KrxCircuitBreaker(
                    failure_threshold=3,
                    _state_store=SystemStateStore(),
                )
    return _SHARED_BREAKER


def reset_shared_breaker_for_test() -> None:
    """테스트 격리용 — 공유 인스턴스를 초기화한다."""
    global _SHARED_BREAKER
    with _SHARED_LOCK:
        _SHARED_BREAKER = None
