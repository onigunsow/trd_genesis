"""시장별 섹터 분류 설정 로더 (SPEC-TRADING-059 외부화).

sector_taxonomy.yaml 에서 시장별(KR/US 등) 섹터 분류 규칙을 읽어
market-agnostic API 로 노출한다.

sector_loader.py 에 하드코딩됐던 _SECTOR_COL / _FINANCIAL_SECTORS /
_normalize_sector 을 이 모듈로 이전한다. 새 시장 추가 시 YAML 만 수정하면
sector_loader 코드는 불변(개방-폐쇄 원칙).
"""

from __future__ import annotations

import functools
import logging
import os
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

# YAML 위치: 이 파일과 동일 패키지 디렉터리
_YAML_PATH = Path(__file__).parent / "sector_taxonomy.yaml"

# 알수없는 market 경고를 한 번만 출력하기 위한 캐시
_warned_markets: set[str] = set()


@functools.lru_cache(maxsize=1)
def _load_taxonomy() -> dict[str, Any]:
    """sector_taxonomy.yaml 을 파싱해 반환. 오류 시 빈 dict 반환(예외 불전파)."""
    try:
        import yaml  # pyyaml — 이미 설치됨

        with _YAML_PATH.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        LOG.error("sector_taxonomy: YAML 로드 실패 — %s", exc)
        return {}


def active_market() -> str:
    """현재 활성 시장 코드 반환. 환경변수 TRADING_MARKET 기본값 'KR'."""
    return os.environ.get("TRADING_MARKET", "KR").upper()


def _market_config(market: str | None) -> dict[str, Any]:
    """주어진 시장의 설정 블록 반환. 없으면 {}."""
    m = market if market is not None else active_market()
    taxonomy = _load_taxonomy()
    cfg = taxonomy.get(m)
    if cfg is None:
        if m not in _warned_markets:
            _warned_markets.add(m)
            LOG.warning("sector_taxonomy: 시장 '%s' 설정 없음 — 폴백 사용", m)
        return {}
    return cfg  # type: ignore[return-value]


def sector_column(market: str | None = None) -> str | None:
    """시장의 섹터 컬럼명 반환. 설정 없으면 None."""
    cfg = _market_config(market)
    return cfg.get("sector_column")  # type: ignore[return-value]


def unknown_label(market: str | None = None) -> str:
    """시장의 unknown 섹터 라벨 반환. 설정 없으면 최종 폴백 'Unknown'."""
    cfg = _market_config(market)
    return cfg.get("unknown_label", "Unknown")  # type: ignore[return-value]


@functools.lru_cache(maxsize=16)
def _reverse_index(market: str) -> dict[str, str]:
    """sub-label → broad-label 역인덱스 구축 (시장별 캐시)."""
    cfg = _market_config(market)
    groups: dict[str, list[str]] = cfg.get("sector_groups", {})
    idx: dict[str, str] = {}
    for broad, members in groups.items():
        for member in members:
            idx[member] = broad
    return idx


def news_sector(raw_industry: str | None, market: str | None = None) -> str | None:
    """업종명(ticker_metadata.sector)을 news 섹터 키로 변환한다.

    # @MX:NOTE: [AUTO] SPEC-TRADING-060 REQ-060-1 — 업종명→news 섹터 매핑.
    # @MX:REASON: 미매핑 시 반드시 None 반환(가짜 캐치올 금지). 이 규약이
    #             오경보 억제의 불변식이다. 정밀-우선 매핑: 명확 6개만 매핑.

    Args:
        raw_industry: ticker_metadata.sector 값 (실 리터럴, 가운뎃점·단형).
        market: 시장 코드. None 이면 active_market() 사용.

    Returns:
        news 섹터 키 문자열, 또는 None (미매핑·빈값·None 입력 모두 None 반환).
    """
    if raw_industry is None:
        return None
    name = str(raw_industry).strip()
    if not name:
        return None
    cfg = _market_config(market)
    ns_map: dict[str, str] = cfg.get("news_sector_map", {})
    return ns_map.get(name)  # 미매핑 시 None


def normalize_sector(raw: object, market: str | None = None) -> str:
    """raw 업종명을 섹터 가드용 라벨로 정규화.

    - 빈 문자열 / None / 'nan' → unknown_label(market)
    - sector_groups 의 멤버이면 broad 라벨로 매핑
    - 그 외 → 원본 그대로 반환
    """
    m = market if market is not None else active_market()
    name = (str(raw) if raw is not None else "").strip()
    if not name or name == "nan":
        return unknown_label(m)
    idx = _reverse_index(m)
    return idx.get(name, name)
