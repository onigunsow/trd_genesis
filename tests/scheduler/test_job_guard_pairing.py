"""잡의 발사 요일과 거래일 가드가 모순되지 않는지 (2026-09-12).

주간 회고는 일요일 18:00 에 등록돼 있는데 `_wrap`(거래일이 아니면 즉시 return)으로
감싸여 있었다. 일요일은 정의상 거래일이 아니므로 **등록된 이래 4개월간 한 번도
실행되지 않았다** — persona_runs 0건, retrospectives 0행. 조용히 아무 일도 일어나지
않는 종류의 결함이라 로그에도 실패가 남지 않는다.
"""
from __future__ import annotations

import inspect

from trading.scheduler import runner


def test_주말_잡은_거래일_가드를_쓰지_않는다():
    """day_of_week 에 sat/sun 만 있는 잡이 _wrap 을 쓰면 영원히 실행되지 않는다."""
    src = inspect.getsource(runner.main)
    lines = src.splitlines()

    offenders = []
    for i, line in enumerate(lines):
        if 'day_of_week="sun"' in line or 'day_of_week="sat"' in line:
            # 같은 add_job 블록의 앞쪽에서 래퍼를 찾는다.
            wrapper = next(
                (lines[j] for j in range(i - 1, max(i - 6, -1), -1)
                 if "_wrap(" in lines[j] or "_safe_call(" in lines[j]),
                "",
            )
            if "_wrap(" in wrapper:
                offenders.append(line.strip())

    assert not offenders, (
        "주말 전용 잡이 거래일 가드(_wrap)에 걸려 영원히 실행되지 않는다: "
        + "; ".join(offenders)
    )


def test_회고_잡이_등록되어_있다():
    src = inspect.getsource(runner.main)
    assert 'id="retrospective"' in src
    assert '_safe_call("retrospective"' in src
