"""SPEC-059 선결 probe — 생존편향-free 퀄리티 팩터 데이터 가용성 검증.

컨테이너 안에서(stdin 파이프) 실행. KRX 안정 장중(09:00~15:30 KST)에만 의미.
4가지를 확인한다:
  1. 시점별(as-of) 종목 리스트 — get_market_ticker_list(과거일자) 가 그 시점
     상장종목(이후 상폐된 종목 포함)을 주는가 = 생존편향-free 유니버스 원천.
  2. 상폐 종목 펀더멘털 — 상장 기간 동안의 EPS/BPS 시계열을 회수할 수 있는가
     (SPEC-059 퀄리티 팩터의 핵심 미지수).
  3. 다년 깊이 — 현재 상장종목의 펀더멘털이 몇 년치 있는가.
  4. as-of 횡단면 — 특정 과거일자에 전 종목 펀더멘털 단면을 주는가.
출력: JSON 1개(요약). 부작용 없음(읽기 전용, DB 미접촉).
"""

import json

from pykrx import stock

out: dict = {}

# 1) 시점별 유니버스 (생존편향-free 원천)
try:
    past = list(stock.get_market_ticker_list("20180102", market="KOSPI"))
    today = set(stock.get_market_ticker_list(market="KOSPI"))
    delisted_since = [t for t in past if t not in today]
    out["pit_universe"] = {
        "past_2018_count": len(past),
        "today_count": len(today),
        "delisted_since_count": len(delisted_since),
        "sample_delisted": delisted_since[:5],
    }
except Exception as exc:
    out["pit_universe_error"] = f"{type(exc).__name__}: {exc}"

# 2) 상폐 종목 펀더멘털 (상장 기간 회수 가능?)
candidates = list(out.get("pit_universe", {}).get("sample_delisted", []))
candidates += ["117930"]  # 한진해운(2017 상폐) 고정 대조군
for tk in candidates[:3]:
    try:
        df = stock.get_market_fundamental_by_date("20150101", "20171231", tk)
        out.setdefault("delisted_fundamentals", {})[tk] = {
            "rows": 0 if df is None else len(df),
            "cols": [] if df is None else list(df.columns),
        }
    except Exception as exc:
        out.setdefault("delisted_fundamentals", {})[tk] = f"{type(exc).__name__}: {exc}"

# 3) 다년 깊이 (현재 상장종목)
try:
    df = stock.get_market_fundamental_by_date("20150101", "20241231", "005930")
    if df is None or df.empty:
        out["listed_depth"] = {"rows": 0}
    else:
        out["listed_depth"] = {
            "rows": len(df),
            "first": str(df.index.min().date()),
            "last": str(df.index.max().date()),
            "cols": list(df.columns),
        }
except Exception as exc:
    out["listed_depth_error"] = f"{type(exc).__name__}: {exc}"

# 4) as-of 횡단면
try:
    df = stock.get_market_fundamental("20200102", market="KOSPI")
    out["asof_cross_section"] = {"rows": 0 if df is None else len(df)}
except Exception as exc:
    out["asof_cross_section_error"] = f"{type(exc).__name__}: {exc}"

print(json.dumps(out, ensure_ascii=False, indent=2))
