"""universe.yaml 로더 — 철학 §5 스키마의 단일 진입점."""

import datetime as dt

import yaml

from src import config

_REQUIRED_KEYS = {
    "name", "layer", "aux_layers", "waves", "purity", "valuation", "maturity", "added_date",
}
_VALID_GRADE = {"H", "M", "L"}

# 잣대가 구현된 계열. 나머지(FFO배수·EV/Sales)는 분석에서 '잣대미구현'으로 빠진다.
PER_VALUATIONS = {"PER밴드", "PEG혼합"}
# 동종 비교 최소 인원 — 이보다 적으면 중앙값이 의미를 갖지 못한다
MIN_PEER_GROUP = 3

# 종목의 역할. '관찰'은 밸류체인을 읽기 위해 유니버스에 두되 추천·랭킹에서는 빼는 것.
# 지표는 (계산 가능해지면) 계산하지만 점수는 내지 않는다.
RECOMMEND, OBSERVE = "추천", "관찰"
_VALID_MODES = {RECOMMEND, OBSERVE}


def load() -> dict:
    with open(config.UNIVERSE_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    stocks = data.get("stocks") or {}
    for ticker, attrs in stocks.items():
        missing = _REQUIRED_KEYS - set(attrs)
        if missing:
            raise ValueError(f"universe.yaml {ticker}: 필수 키 누락 {sorted(missing)}")
        if attrs["purity"] not in _VALID_GRADE:
            raise ValueError(f"universe.yaml {ticker}: purity는 H/M/L이어야 함")
        waves = attrs["waves"]
        if set(waves) != {"W1", "W2", "W3"} or not set(waves.values()) <= _VALID_GRADE:
            raise ValueError(f"universe.yaml {ticker}: waves는 W1/W2/W3 각 H/M/L이어야 함")
        if attrs.setdefault("mode", RECOMMEND) not in _VALID_MODES:
            raise ValueError(
                f"universe.yaml {ticker}: mode는 {sorted(_VALID_MODES)} 중 하나여야 함"
            )
        try:
            dt.date.fromisoformat(str(attrs["added_date"]))
        except ValueError:
            raise ValueError(
                f"universe.yaml {ticker}: added_date는 YYYY-MM-DD여야 함 "
                f"(백테스트가 편입 이전 구간에서 이 종목을 제외하는 기준)"
            ) from None
    return stocks


def tickers() -> list[str]:
    return sorted(load().keys())
