"""yfinance 일간 가격 수집 → raw_prices.

- auto_adjust=False로 미조정 종가(close)와 조정 종가(adj_close)를 모두 저장한다.
- 증분 수집: 티커별 마지막 저장일 - 7일부터 다시 받는다 (뒤늦은 조정 반영 여지).
- append-only: INSERT OR IGNORE — 이미 있는 (ticker, date)는 건드리지 않는다.
- 한 종목이 실패해도 나머지는 계속 받는다 (Result.failures로 보고).
"""

import datetime as dt
import time

import pandas as pd
import yfinance as yf

from src import config, universe
from src.ingest import Result

FULL_LOOKBACK_DAYS = 5 * 365
INCREMENTAL_PAD_DAYS = 7
REQUEST_INTERVAL_SEC = 0.5   # 연속 요청 시 스로틀 회피
DOWNLOAD_TIMEOUT_SEC = 30    # yfinance 기본 10초는 종목 수가 많으면 짧다


def _start_date(conn, ticker: str) -> str:
    row = conn.execute(
        "SELECT MAX(date) AS d FROM raw_prices WHERE ticker = ?", (ticker,)
    ).fetchone()
    if row["d"] is None:
        start = dt.date.today() - dt.timedelta(days=FULL_LOOKBACK_DAYS)
    else:
        start = dt.date.fromisoformat(row["d"]) - dt.timedelta(days=INCREMENTAL_PAD_DAYS)
    return start.isoformat()


def _value(row, key):
    v = row.get(key)
    return None if v is None or pd.isna(v) else float(v)


def ingest(conn, tickers: list[str] | None = None) -> Result:
    tickers = sorted(set(tickers or universe.tickers()) | {config.BENCHMARK_TICKER})
    collected_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    result = Result()
    for ticker in tickers:
        try:
            df = yf.download(
                ticker,
                start=_start_date(conn, ticker),
                auto_adjust=False,
                progress=False,
                timeout=DOWNLOAD_TIMEOUT_SEC,
            )
        except Exception as exc:
            result.fail(ticker, exc)
            continue
        finally:
            time.sleep(REQUEST_INTERVAL_SEC)

        if df is None or df.empty:
            result.failures.append((ticker, "응답에 가격 데이터 없음 (상장폐지·티커 오기 확인)"))
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        for date, row in df.iterrows():
            close, adj = _value(row, "Close"), _value(row, "Adj Close")
            if close is None or adj is None or adj <= 0:
                continue  # 거래정지·결측 행. 없는 날로 두면 시계열 체크가 잡는다
            volume = _value(row, "Volume")
            cur = conn.execute(
                "INSERT OR IGNORE INTO raw_prices "
                "(ticker, date, open, high, low, close, adj_close, volume, collected_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ticker,
                    date.strftime("%Y-%m-%d"),
                    _value(row, "Open"),
                    _value(row, "High"),
                    _value(row, "Low"),
                    close,
                    adj,
                    int(volume) if volume is not None else None,
                    collected_at,
                ),
            )
            result.inserted += cur.rowcount
        conn.commit()
    return result
