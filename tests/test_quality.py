"""quality/checks.py 검증 — 합성 데이터에 위반 7종을 심고 전부 검출되는지 확인.

실행: 레포 루트에서 `python -m tests.test_quality` (pytest도 가능).
네트워크 불필요 (임시 SQLite만 사용).
"""

import datetime as dt
import os
import tempfile

os.environ["DUCKLING_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

from src.quality import checks
from src.storage import db

NOW = "2026-08-09T00:00:00+00:00"


def _seed(conn):
    # 가격: 최근 10 거래일 (평일)
    dates = []
    d = dt.date(2026, 7, 27)
    while len(dates) < 10:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += dt.timedelta(days=1)

    def put_price(t, date, close, adj):
        conn.execute(
            "INSERT INTO raw_prices VALUES (?,?,?,?,?,?,?,?,?)",
            (t, date, close, close, close, close, adj, 1000, NOW),
        )

    for i, date in enumerate(dates):
        put_price("SPY", date, 500 + i, 500 + i)
        put_price("NVDA", date, 180 + i, 180 + i)        # 정상 — 오탐 검증용
        if date != dates[5]:
            put_price("AMD", date, 160, 160)             # 위반: 중간 결측일
        put_price("MU", date, 100, 145 if i == 7 else 100)  # 위반: ±30% 급변
        if date != dates[-1]:
            put_price("VST", date, 150, 150)             # 위반: 신선도 (최신일 누락)

    def put_fund(t, concept, unit, start, end, form, filed, val):
        conn.execute(
            "INSERT INTO raw_fundamentals VALUES (?,?,?,?,?,?,?,?,?,?,'edgar',?)",
            (t, concept, unit, start, end, 2026, None, form, filed, val, NOW),
        )

    # NVDA 정상: Q1~Q3 분기 + FY 연간 → Q4 유도, TTM = 4.30
    eps = "EarningsPerShareDiluted"
    put_fund("NVDA", eps, "USD/shares", "2025-01-27", "2025-04-27", "10-Q", "2025-05-28", 0.89)
    put_fund("NVDA", eps, "USD/shares", "2025-04-28", "2025-07-27", "10-Q", "2025-08-27", 1.05)
    put_fund("NVDA", eps, "USD/shares", "2025-07-28", "2025-10-26", "10-Q", "2025-11-19", 1.30)
    put_fund("NVDA", eps, "USD/shares", "2025-01-27", "2026-01-25", "10-K", "2026-02-25", 4.30)
    # AVGO 위반: EDGAR TTM 5.00 vs yfinance trailing 2.00
    for s, e, f in [
        ("2025-02-03", "2025-05-04", "2025-06-04"),
        ("2025-05-05", "2025-08-03", "2025-09-03"),
        ("2025-08-04", "2025-11-02", "2025-12-10"),
        ("2025-11-03", "2026-02-01", "2026-03-11"),
    ]:
        put_fund("AVGO", eps, "USD/shares", s, e, "10-Q", f, 1.25)
    # TSM: TWD 단위 → 대조 생략 경고
    put_fund("TSM", "BasicEarningsLossPerShare", "TWD/shares", "2026-01-01", "2026-03-31", "6-K", "2026-04-17", 13.94)
    # AMD 위반: 공시일 < 기간종료 (룩어헤드)
    put_fund("AMD", eps, "USD/shares", "2026-03-29", "2026-06-27", "10-Q", "2026-01-01", 0.50)
    # CRDO: 분기 2개뿐 → TTM 재구성 실패 경고
    put_fund("CRDO", eps, "USD/shares", "2025-11-03", "2026-02-01", "10-Q", "2026-03-05", 0.25)
    put_fund("CRDO", eps, "USD/shares", "2025-08-04", "2025-11-02", "10-Q", "2025-12-05", 0.20)

    for t, ttm in (("NVDA", 4.20), ("AVGO", 2.00), ("CRDO", 0.45)):
        conn.execute(
            "INSERT INTO raw_estimates VALUES (?,?,?,?,?,?,'yfinance',?)",
            (t, "2026-08-09", 5.0, 30.0, ttm, 40, NOW),
        )
    conn.commit()


def test_checks():
    conn = db.connect()
    _seed(conn)
    tickers = ["NVDA", "AMD", "MU", "VST", "AVGO", "TSM", "CRDO"]
    findings = (
        checks.check_price_series(conn, tickers)
        + checks.check_freshness(conn, tickers)
        + checks.check_filed_dates(conn, tickers)
        + checks.check_cross_source_eps(conn, tickers)
        + checks.check_ttm_computable(conn, tickers)
    )
    got = {(f.check_name, f.severity, f.ticker) for f in findings}
    expected = {
        ("시계열무결성", "error", "AMD"),
        ("시계열무결성", "error", "MU"),
        ("신선도", "error", "VST"),
        ("공시일정합성", "error", "AMD"),
        ("크로스소스대조", "error", "AVGO"),
        ("크로스소스대조", "warning", "TSM"),
        ("TTM검증", "warning", "CRDO"),
    }
    assert not expected - got, f"검출 실패: {expected - got}"
    assert not any(f.ticker == "NVDA" for f in findings), "정상 데이터(NVDA)에 오탐"

    quarters, unit_note = checks.eps_quarter_series(conn, "NVDA")
    assert unit_note is None
    assert abs(checks.ttm_eps(quarters) - 4.30) < 1e-6, "Q4 유도/TTM 계산 오류"


if __name__ == "__main__":
    test_checks()
    print("✅ 전체 통과: 위반 7종 검출 + NVDA 무오탐 + TTM 유도 정확")
