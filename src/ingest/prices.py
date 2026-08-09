"""yfinance 일간 가격 수집 → raw_prices.

- auto_adjust=False로 미조정 종가(close)와 조정 종가(adj_close)를 모두 저장한다.
- 증분 수집: 티커별 마지막 저장일 - 7일부터 다시 받는다 (뒤늦은 조정 반영 여지).
- append-only: INSERT OR IGNORE — 이미 있는 (ticker, date)는 건드리지 않는다.
"""

import datetime as dt

import pandas as pd
import yfinance as yf

from src import config, universe

FULL_LOOKBACK_DAYS = 5 * 365
INCREMENTAL_PAD_DAYS = 7


def _start_date(conn, ticker: str) -> str:
    row = conn.execute(
        "SELECT MAX(date) AS d FROM raw_prices WHERE ticker = ?", (ticker,)
    ).fetchone()
    if row["d"] is None:
        start = dt.date.today() - dt.timedelta(days=FULL_LOOKBACK_DAYS)
    else:
        start = dt.date.fromisoformat(row["d"]) - dt.timedelta(days=INCREMENTAL_PAD_DAYS)
    return start.isoformat()


def ingest(conn, tickers: list[str] | None = None) -> int:
    tickers = sorted(set(tickers or universe.tickers()) | {config.BENCHMARK_TICKER})
    collected_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    inserted = 0
    for ticker in tickers:
        df = yf.download(
            ticker,
            start=_start_date(conn, ticker),
            auto_adjust=False,
            progress=False,
        )
        if df is None or df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        for date, row in df.iterrows():
            cur = conn.execute(
                "INSERT OR IGNORE INTO raw_prices "
                "(ticker, date, open, high, low, close, adj_close, volume, collected_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ticker,
                    date.strftime("%Y-%m-%d"),
                    float(row["Open"]),
                    float(row["High"]),
                    float(row["Low"]),
                    float(row["Close"]),
                    float(row["Adj Close"]),
                    int(row["Volume"]) if pd.notna(row["Volume"]) else None,
                    collected_at,
                ),
            )
            inserted += cur.rowcount
    conn.commit()
    return inserted
