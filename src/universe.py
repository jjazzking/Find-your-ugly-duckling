"""universe.yaml 로더 — 철학 §5 스키마의 단일 진입점."""

import yaml

from src import config

_REQUIRED_KEYS = {"name", "layer", "aux_layers", "waves", "purity", "valuation", "maturity"}
_VALID_GRADE = {"H", "M", "L"}


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
    return stocks


def tickers() -> list[str]:
    return sorted(load().keys())
