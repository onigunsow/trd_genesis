"""SPEC-TRADING-044 M2 — Walk-forward / point-in-time 하니스.

롤링 train/test 윈도로 출구 룰 파라미터를 out-of-sample(OOS) 검증한다.

주요 설계 원칙:
  - Point-in-time 규율: 각 train 윈도 종료일 T 기준 ts <= T 인 바만 파라미터 적합에 사용.
  - Exit 의미론 재사용: exit_sweep.run_sweep / run_exit_simulation / simulate_position 그대로.
  - OOS 가 헤드라인: oos_metrics 가 주 출력, in_sample_metrics 는 라벨된 보조 진단.
  - 결정성: 주입된 OHLCV 로 실행, 라이브 pykrx/DB 없음.
  - LLM 결정 레이어 미검증 (ADR-002): 기계적 진입 제어변수, 출구 룰만 검증.

"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from trading.backtest.engine import (
    DEFAULT_FEE_RATE,
    DEFAULT_SLIPPAGE,
    DEFAULT_TAX_RATE,
)
from trading.backtest.exit_sweep import (
    SweepMetrics,
    recommend,
    run_exit_simulation,
    run_sweep,
)

LOG = logging.getLogger(__name__)

Bar = dict[str, Any]

# @MX:ANCHOR: [AUTO] walk-forward point-in-time 슬라이스 — 안전 불변식
# @MX:REASON: SPEC-TRADING-044 REQ-044-A1/A2: 윈도 종료일 T 기준 ts <= T 바만 적합에 사용.
# 미래 바 제외는 test_walk_forward.py::TestPointInTimeSlice 로 테스트된 불변식.

# @MX:WARN: [AUTO] vectorbt optional dependency 경계 — 런타임 컨테이너 import 금지
# @MX:REASON: SPEC-TRADING-044 ADR-001: vectorbt(numba/llvmlite)는 [backtest] optional-extra 전용.
# 이 파일에서 vectorbt를 import할 경우 반드시 try/except 가드 안에서 lazy import해야 한다.


def _slice_bars(bars: list[Bar], cutoff: date) -> list[Bar]:
    """Point-in-time 슬라이스: ts <= cutoff 인 바만 반환.

    모든 train/test 윈도는 이 함수를 통해 바를 조회한다.
    직접 인덱싱 금지 — 이 경계를 우회하면 look-ahead 누출이 발생한다.
    """
    return [b for b in bars if b["ts"] <= cutoff]


@dataclass
class WalkForwardWindow:
    """롤링 train/test 윈도 한 슬롯."""

    window_idx: int
    train_bars: list[Bar]
    test_bars: list[Bar]
    train_cutoff: date
    test_cutoff: date


def rolling_windows(
    bars: list[Bar],
    *,
    train_bars: int,
    test_bars: int,
    step_bars: int,
) -> list[WalkForwardWindow]:
    """날짜순 정렬된 단일 심볼 바 리스트 → 롤링 train/test 윈도 목록.

    각 윈도:
      - train_bars: 인덱스 [start, start + train_bars) — 파라미터 적합용
      - test_bars: 인덱스 [start + train_bars, start + train_bars + test_bars) — OOS 평가용
      - train 과 test 는 겹치지 않는다 (look-ahead 없음)
    step_bars 마다 시작점을 이동하여 다음 윈도를 생성한다.
    """
    sorted_bars = sorted(bars, key=lambda b: b["ts"])
    total = len(sorted_bars)
    min_needed = train_bars + test_bars
    if total < min_needed:
        return []

    windows: list[WalkForwardWindow] = []
    idx = 0
    win_idx = 0
    while idx + min_needed <= total:
        train = sorted_bars[idx: idx + train_bars]
        test = sorted_bars[idx + train_bars: idx + train_bars + test_bars]
        windows.append(WalkForwardWindow(
            window_idx=win_idx,
            train_bars=train,
            test_bars=test,
            train_cutoff=train[-1]["ts"],
            test_cutoff=test[-1]["ts"],
        ))
        idx += step_bars
        win_idx += 1
    return windows


@dataclass
class WalkForwardResult:
    """Walk-forward 하니스 실행 결과.

    oos_metrics: OOS(test 윈도) 집계 — 헤드라인 결과.
    in_sample_metrics: 각 train 윈도 in-sample 파라미터 추천 — 라벨된 보조 진단.
    n_windows: 실제 실행된 윈도 수.
    """

    oos_metrics: list[SweepMetrics] = field(default_factory=list)
    in_sample_metrics: list[SweepMetrics] = field(default_factory=list)
    n_windows: int = 0


def run_walk_forward(
    price_data: dict[str, list[Bar]],
    atr_by_symbol: dict[str, float],
    *,
    train_bars: int = 252,          # Q-A1 기본값: 12개월 ≈ 252 영업일
    test_bars: int = 63,            # Q-A1 기본값: 3개월 ≈ 63 영업일
    step_bars: int = 63,            # Q-A1 기본값: 3개월 스텝
    stop_atr_mults: list[float] | None = None,
    stop_floor_pcts: list[float] | None = None,
    take_atr_mults: list[float] | None = None,
    every_n: int = 5,
    fee_rate: float = DEFAULT_FEE_RATE,
    tax_rate: float = DEFAULT_TAX_RATE,
    slippage: float = DEFAULT_SLIPPAGE,
) -> WalkForwardResult:
    """롤링 walk-forward 로 OOS 출구 룰 성과를 집계한다.

    각 train 윈도에서 run_sweep 로 파라미터 그리드를 평가해 robust 추천을 얻고,
    직후 unseen test 윈도에서 run_exit_simulation 으로 OOS 성과를 측정한다.

    OOS 집계(oos_metrics) 가 헤드라인; in_sample_metrics 는 보조 진단.
    출구 시뮬레이션은 exit_sweep.simulate_position / run_exit_simulation 을 그대로 사용 (ADR-A5).

    CRITICAL (ADR-001/002):
      - 출구 룰만 검증; LLM 진입 엣지는 비결정적이라 검증 불가.
      - 기계적 진입 모델(mechanical_entries)은 EXIT 룰 stress-test 용 제어변수.
    """
    if stop_atr_mults is None:
        stop_atr_mults = [2.0, 3.0, 4.0]
    if stop_floor_pcts is None:
        stop_floor_pcts = [-7.0, -10.0]
    if take_atr_mults is None:
        take_atr_mults = [2.0, 3.0, 5.0]

    # 모든 심볼의 바를 일자 기준으로 대표 시퀀스 확보 (윈도 분할용)
    # 심볼 수무관하게 공통 날짜 기준 윈도를 사용한다.
    # 가장 긴 심볼의 바를 기준으로 윈도를 분할.
    if not price_data:
        return WalkForwardResult()

    reference_bars = max(price_data.values(), key=len)
    windows = rolling_windows(
        reference_bars,
        train_bars=train_bars,
        test_bars=test_bars,
        step_bars=step_bars,
    )

    if not windows:
        LOG.info("walk_forward: 윈도 부족 — OOS 없음")
        return WalkForwardResult()

    oos_list: list[SweepMetrics] = []
    is_list: list[SweepMetrics] = []

    for win in windows:
        train_cutoff = win.train_cutoff
        test_cutoff = win.test_cutoff

        # Point-in-time 슬라이스: train 윈도 기준 각 심볼의 바를 잘라낸다.
        # _slice_bars 가 유일한 진입점 — 직접 인덱싱 금지 (불변식).
        train_data: dict[str, list[Bar]] = {
            sym: _slice_bars(bars, train_cutoff)
            for sym, bars in price_data.items()
        }
        test_data: dict[str, list[Bar]] = {
            sym: [b for b in bars if train_cutoff < b["ts"] <= test_cutoff]
            for sym, bars in price_data.items()
        }

        # Train 윈도에서 파라미터 그리드 평가 → robust 추천 (in-sample)
        train_results = run_sweep(
            train_data, atr_by_symbol,
            stop_atr_mults=stop_atr_mults,
            stop_floor_pcts=stop_floor_pcts,
            take_atr_mults=take_atr_mults,
            every_n=every_n,
            fee_rate=fee_rate,
            tax_rate=tax_rate,
            slippage=slippage,
        )
        if not train_results:
            continue

        try:
            rec = recommend(train_results)
        except ValueError:
            continue

        is_list.append(rec.metrics)

        # Test 윈도에서 추천 파라미터로 OOS 평가
        oos_metric = run_exit_simulation(
            test_data, atr_by_symbol, rec.params,
            every_n=every_n,
            fee_rate=fee_rate,
            tax_rate=tax_rate,
            slippage=slippage,
        )
        oos_list.append(oos_metric)
        LOG.debug(
            "walk_forward 윈도 %d: train_end=%s test_end=%s oos_trades=%d oos_expectancy=%.3f%%",
            win.window_idx, train_cutoff, test_cutoff, oos_metric.trades, oos_metric.expectancy,
        )

    return WalkForwardResult(
        oos_metrics=oos_list,
        in_sample_metrics=is_list,
        n_windows=len(windows),
    )
