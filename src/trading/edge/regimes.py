"""코드 체제(runtime_version)로 관측을 가른다 — 이벤트 귀속 전용 (2026-09-05).

## 왜 P&L 이 아니라 이벤트인가

2026-09-05 실측으로 갈렸다. 체제 경계(8/17·8/23·8/27) 어디로 잘라도 **성과 결론은
바뀌지 않는다** — 모든 체제가 진다. payoff 는 0.65 -> 0.49 로 나빠지고 승률은
30.5 -> 35.7% 로 좋아져 방향이 서로 반대다(둘 다 노이즈로 읽는 게 정직하다).

반면 **메커니즘 주장은 뒤집힌다.** rotate 는 60일 창에서 21건으로 "청산의 다수"
처럼 보이지만 8/17 이후로는 **0건**이다. 결론이 "끄자" 에서 "이미 죽었다" 로 완전히
뒤집힌다. 그날의 오진 3건(장전 매도·rotate·원장 음수)은 전부 성과가 아니라
메커니즘 존재 여부에 관한 것이었다.

그래서 이 모듈은 이벤트 귀속만 제공하고, 체제 간 P&L 비교는 ``underpowered`` 로
거절한다. n=1 짜리 이벤트도 체제 귀속은 100% 정확하지만, n=14 짜리 평균은 체제를
갈라도 의미가 없다.

## 왜 git 이 아니라 runtime_version 인가

배포 시각은 어디에도 기록되지 않는다(실측): ``schema_migrations`` 는 마이그레이션이
있는 커밋만 찍고, 이미지 빌드 시각은 2개월 낡았으며(``src/`` bind-mount 라 배포=재시작),
``docker inspect`` 는 최신 1회만 보관하고, audit_log 에 기동 표식이 없다. 커밋 시각과
배포 시각의 실측 오차는 최대 2~3일인데 청산 중앙 보유일이 6~7일이라 **오차가
보유기간의 절반**이다. 실행 중인 프로세스가 스스로 남기는 것 외에 방법이 없다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

from trading.db.session import connection

LOG = logging.getLogger(__name__)

# 관측 분포에서 역산한 군당 필요 표본 (유의수준 .05 양측, 검정력 80%).
# A(<8/17) n=59 mean=-2.791% sd=5.378 / B(>=8/17) n=14 mean=-1.543% sd=3.342
# -> Cohen d=0.246 -> 군당 259. 승률(0.305 vs 0.357)은 군당 1,279 이 필요하다.
# n=14 로 잡히는 최소 효과는 평균차 5.37%p / 승률차 46.7%p — 그 아래는 못 본다.
MIN_N_PER_GROUP = 259


@dataclass
class Regime:
    """한 체제의 관측 구간. ``end`` 가 None 이면 현재 진행 중."""

    code_hash: str | None
    prompt_hash: str | None
    start: datetime
    end: datetime | None
    last_seen: datetime | None

    @property
    def label(self) -> str:
        return f"{self.code_hash or '?'}/{self.prompt_hash or '?'}"

    @property
    def is_current(self) -> bool:
        return self.end is None


def regime_windows() -> list[Regime]:
    """``runtime_version`` 을 관측 순서대로 구간화. 기록이 없으면 [].

    구간은 [first_seen, 다음 체제의 first_seen) 이고 마지막은 열려 있다.
    ``last_seen`` 이 다음 체제의 ``start`` 를 넘으면 두 체제가 겹쳐 돈 것이다
    (롤백·재배포·프로세스별 버전 혼재). ``overlaps`` 로 확인할 것.
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT code_hash, prompt_hash, first_seen, last_seen "
                "FROM runtime_version ORDER BY first_seen"
            )
            rows = cur.fetchall()
    except Exception:  # 기록이 없으면 체제 없이 전 구간을 하나로 본다.
        LOG.warning("runtime_version 조회 실패 — 체제 분할 없이 진행", exc_info=True)
        return []

    out: list[Regime] = []
    for i, r in enumerate(rows):
        nxt = rows[i + 1]["first_seen"] if i + 1 < len(rows) else None
        out.append(
            Regime(
                code_hash=r["code_hash"],
                prompt_hash=r["prompt_hash"],
                start=r["first_seen"],
                end=nxt,
                last_seen=r["last_seen"],
            )
        )
    return out


def overlaps(windows: list[Regime]) -> list[tuple[Regime, Regime]]:
    """구간이 겹치는 체제 쌍. 겹치면 그 구간의 귀속은 신뢰할 수 없다."""
    out = []
    for a, b in pairwise(windows):
        if a.last_seen is not None and a.last_seen > b.start:
            out.append((a, b))
    return out


def current_regime() -> Regime | None:
    """지금 돌고 있는 체제. 기록이 없으면 None."""
    w = regime_windows()
    return w[-1] if w else None


def event_counts_by_regime(event_type: str) -> list[tuple[Regime, int]]:
    """``audit_log`` 이벤트를 체제별로 센다.

    주간 에이전트가 "이 메커니즘이 지금도 도는가" 에 답하는 경로다. 2026-09-05 의
    오진 셋은 전부 이 질문을 60일 합계로 답해서 생겼다.
    """
    windows = regime_windows()
    if not windows:
        return []
    out: list[tuple[Regime, int]] = []
    try:
        with connection() as conn, conn.cursor() as cur:
            for w in windows:
                if w.end is None:
                    cur.execute(
                        "SELECT count(*) n FROM audit_log "
                        "WHERE event_type = %s AND ts >= %s",
                        (event_type, w.start),
                    )
                else:
                    cur.execute(
                        "SELECT count(*) n FROM audit_log "
                        "WHERE event_type = %s AND ts >= %s AND ts < %s",
                        (event_type, w.start, w.end),
                    )
                out.append((w, int(cur.fetchone()["n"])))
    except Exception:
        LOG.warning("이벤트 체제 집계 실패", exc_info=True)
        return []
    return out


def underpowered(n_a: int, n_b: int, *, min_n: int = MIN_N_PER_GROUP) -> str | None:
    """체제 간 성과 비교가 표본 부족이면 거절 사유를, 충분하면 None.

    "그래도 숫자는 보고한다" 를 막기 위한 게이트다. 관측된 효과크기에서 군당 259건이
    필요한데 현재 체제의 왕복은 10여 건이다 — 이 상태의 비교는 노이즈를 읽는 것이다.
    """
    if n_a >= min_n and n_b >= min_n:
        return None
    return (
        f"표본 부족으로 체제 간 성과 비교를 하지 않는다 "
        f"(n={n_a} / {n_b}, 군당 {min_n} 필요). "
        f"이 표본으로 잡히는 최소 효과는 평균차 5.37%p·승률차 46.7%p 이며, "
        f"그보다 작은 차이는 관측되어도 노이즈와 구별되지 않는다. "
        f"체제 분할은 성과 비교가 아니라 이벤트 귀속에 쓸 것."
    )
