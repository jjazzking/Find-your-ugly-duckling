"""forward 추정치 스냅샷 수집 → raw_estimates.

forward EPS 히스토리는 무료로 살 수 없으므로 1일차부터 매일 스냅샷을 쌓는다 (철학 §0).
하루 한 장: (ticker, snapshot_date)가 PK라 같은 날 재실행해도 중복 적재되지 않는다.
"""

import datetime as dt

import yfinance as yf

from src import universe


def ingest(conn, tickers: list[str] | None = None) -> int:
    tickers = tickers or universe.tickers()
    snapshot_date = dt.datetime.now(dt.timezone.utc).date().isoformat()
    collected_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    inserted = 0
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception:
            info = {}  # 결측은 커버리지 체크(quality #6)가 잡는다
        cur = conn.execute(
            "INSERT OR IGNORE INTO raw_estimates "
            "(ticker, snapshot_date, forward_eps, forward_pe, trailing_eps, "
            " num_analysts, source, collected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'yfinance', ?)",
            (
                ticker,
                snapshot_date,
                info.get("forwardEps"),
                info.get("forwardPE"),
                info.get("trailingEps"),
                info.get("numberOfAnalystOpinions"),
                collected_at,
            ),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted
