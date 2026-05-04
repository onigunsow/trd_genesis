"""Strategy abstract base. Output: a daily target weight per asset."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass
class StrategyResult:
    name: str
    params: dict
    weights: pd.DataFrame   # index=date, columns=asset, values=target weight in [0, 1]


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def compute(self, prices: pd.DataFrame) -> StrategyResult:
        """Return target weights given a DataFrame of close prices (index=date, cols=asset)."""
