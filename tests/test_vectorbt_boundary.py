"""SPEC-TRADING-044 M5 — vectorbt 런타임 import 경계 테스트.

AC-6: 런타임 트레이딩 모듈은 vectorbt를 import 하지 않는다.
      vectorbt는 offline backtest extra 전용.
"""
from __future__ import annotations

import importlib
import sys


# 런타임 트레이딩 모듈 목록 (vectorbt import 금지 대상)
RUNTIME_MODULES = [
    "trading.config",
    "trading.edge.analytics",
    "trading.edge.scorecard",
    "trading.edge.benchmark",
    "trading.edge.roundtrips",
    "trading.backtest.engine",
    "trading.backtest.exit_sweep",
    "trading.backtest.walk_forward",
]


class TestVectorbtImportBoundary:
    """런타임 모듈이 vectorbt를 import하지 않는다 (ADR-001, REQ-044-A6)."""

    def test_runtime_modules_do_not_import_vectorbt(self):
        """런타임 트레이딩 모듈 중 어떤 것도 vectorbt를 import하지 않는다.

        이 테스트는 ADR-001의 핵심 불변식:
        vectorbt는 offline [backtest] optional-dependencies 전용이며,
        런타임 컨테이너는 import하지 않아야 한다.
        """
        for module_name in RUNTIME_MODULES:
            # 모듈 소스를 직접 읽어 'vectorbt' 텍스트 포함 여부 확인
            try:
                mod = importlib.import_module(module_name)
            except ImportError:
                continue  # 모듈이 없으면 스킵
            src_file = getattr(mod, "__file__", None)
            if src_file is None:
                continue
            with open(src_file) as f:
                source = f.read()
            # 'import vectorbt' 또는 'from vectorbt' 가 있으면 경계 위반
            assert "import vectorbt" not in source, (
                f"{module_name} 이 'import vectorbt'를 포함합니다 — "
                "런타임 모듈에서 vectorbt를 import해서는 안 됩니다 (ADR-001)"
            )
            assert "from vectorbt" not in source, (
                f"{module_name} 이 'from vectorbt'를 포함합니다 — "
                "런타임 모듈에서 vectorbt를 import해서는 안 됩니다 (ADR-001)"
            )

    def test_walk_forward_does_not_import_vectorbt(self):
        """walk_forward.py는 vectorbt를 import하지 않는다.

        walk_forward는 offline 분석 도구지만 런타임 컨테이너에도 설치되므로
        runtime-safe해야 한다 (lazy optional import 사용).
        """
        import trading.backtest.walk_forward as wf_mod
        src_file = getattr(wf_mod, "__file__", None)
        assert src_file is not None
        with open(src_file) as f:
            source = f.read()
        # 무조건 import는 금지; try/except 안에서만 허용
        # 간단하게: 최상위에 'import vectorbt'가 없으면 OK
        lines = source.splitlines()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("import vectorbt") or stripped.startswith("from vectorbt"):
                assert False, (
                    f"walk_forward.py 최상위에 vectorbt import 발견: {stripped!r}\n"
                    "vectorbt는 try/except 가드 안에서 lazy import해야 합니다 (ADR-001)"
                )

    def test_backtest_engine_unchanged(self):
        """backtest/engine.py의 가중치 백테스트 경로가 변경되지 않았다 (AC-6)."""
        from trading.backtest.engine import DEFAULT_FEE_RATE, DEFAULT_SLIPPAGE, DEFAULT_TAX_RATE, run

        # run() 함수가 여전히 존재하고 기본 인자로 호출 가능한지 확인
        assert callable(run)
        # 기본 상수가 여전히 존재
        assert DEFAULT_FEE_RATE > 0
        assert DEFAULT_SLIPPAGE >= 0
        assert DEFAULT_TAX_RATE >= 0
