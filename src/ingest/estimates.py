"""forward 추정치 스냅샷 수집 → raw_estimates.

forward EPS 히스토리는 무료로 살 수 없으므로 1일차부터 매일 스냅샷을 쌓는다 (철학 §0).
하루 한 장: (ticker, snapshot_date)가 PK라 같은 날 재실행해도 중복 적재되지 않는다.

**값이 하나도 없으면 적재하지 않는다.** 빈 행을 넣으면 INSERT OR IGNORE 때문에
같은 날 재시도해도 무시되어, 일시적 장애가 그날 스냅샷을 영구히 망가뜨린다.
"""

import datetime as dt
import time

import yfinance as yf

from src import universe
from src.ingest import Result

REQUEST_INTERVAL_SEC = 0.5
FIELDS = {
    "forward_eps": "forwardEps",
    "forward_pe": "forwardPE",
    "trailing_eps": "trailingEps",
    "num_analysts": "numberOfAnalystOpinions",
}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def ingest(conn, tickers: list[str] | None = None) -> Result:
    tickers = tickers or universe.tickers()
    snapshot_date = dt.datetime.now(dt.timezone.utc).date().isoformat()
    collected_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    result = Result()
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception as exc:
            result.fail(ticker, exc)
            continue
        finally:
            time.sleep(REQUEST_INTERVAL_SEC)

        values = {k: _number(info.get(src)) for k, src in FIELDS.items()}
        if all(v is None for v in values.values()):
            result.failures.append((ticker, "추정치 필드가 비어 있음 — 이 날짜 스냅샷 미적재"))
            continue

        cur = conn.execute(
            "INSERT OR IGNORE INTO raw_estimates "
            "(ticker, snapshot_date, forward_eps, forward_pe, trailing_eps, "
            " num_analysts, source, collected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'yfinance', ?)",
            (
                ticker,
                snapshot_date,
                values["forward_eps"],
                values["forward_pe"],
                values["trailing_eps"],
                int(values["num_analysts"]) if values["num_analysts"] is not None else None,
                collected_at,
            ),
        )
        result.inserted += cur.rowcount
    conn.commit()
    return result
