"""수집 모듈의 실패 처리 검증 — 네트워크 대신 가짜 응답을 주입한다.

실데이터에서 처음 터지는 곳은 대개 정상 경로가 아니라 예외 경로다.
(한 종목 예외 / 빈 응답 / NaN 행 / 추정치 필드 결측 / SEC User-Agent 미설정)

실행: 레포 루트에서 `python -m tests.test_ingest` (pytest도 가능).
"""

import datetime as dt
import os
import tempfile

os.environ["DUCKLING_DB"] = os.path.join(tempfile.mkdtemp(), "test_ingest.db")

import pandas as pd

from src import config
from src.ingest import estimates, fundamentals, prices
from src.storage import db

DATES = pd.to_datetime(["2026-08-05", "2026-08-06", "2026-08-07"])


def _frame(with_nan=False):
    close = [100.0, 101.0, 102.0]
    adj = [100.0, float("nan") if with_nan else 101.0, 102.0]
    return pd.DataFrame(
        {
            "Open": close, "High": close, "Low": close, "Close": close,
            "Adj Close": adj, "Volume": [1000, 1000, 1000],
        },
        index=DATES,
    )


def test_price_failures_are_isolated(monkeypatch):
    conn = db.connect()

    def fake_download(ticker, **kw):
        if ticker == "AMD":
            raise RuntimeError("HTTPError: 429 Too Many Requests")
        if ticker == "MU":
            return pd.DataFrame()                  # 상장폐지·티커 오기
        if ticker == "NVDA":
            return _frame(with_nan=True)           # 거래정지일 NaN
        return _frame()

    monkeypatch.setattr(prices.yf, "download", fake_download)
    monkeypatch.setattr(prices.time, "sleep", lambda *_: None)

    result = prices.ingest(conn, ["NVDA", "AMD", "MU"])

    failed = dict(result.failures)
    assert "AMD" in failed and "429" in failed["AMD"], result.failures
    assert "MU" in failed and "가격 데이터 없음" in failed["MU"], result.failures
    assert "NVDA" not in failed, "정상 종목이 실패로 잡힘"

    # NVDA는 3일 중 NaN 하루를 뺀 2일만 적재된다
    rows = conn.execute(
        "SELECT date FROM raw_prices WHERE ticker = 'NVDA' ORDER BY date"
    ).fetchall()
    assert [r["date"] for r in rows] == ["2026-08-05", "2026-08-07"], rows
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM raw_prices WHERE ticker = 'AMD'"
    ).fetchone()["n"] == 0


def test_empty_estimates_do_not_poison_the_day(monkeypatch):
    """빈 스냅샷을 넣으면 INSERT OR IGNORE 때문에 그날 재시도가 영영 막힌다."""
    conn = db.connect()
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()

    class FakeTicker:
        def __init__(self, t):
            self.t = t

        @property
        def info(self):
            if self.t == "ORCL":
                raise RuntimeError("JSONDecodeError")
            return {} if self.t == "AVGO" else {"forwardEps": 5.0, "trailingEps": 4.0}

    monkeypatch.setattr(estimates.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(estimates.time, "sleep", lambda *_: None)

    result = estimates.ingest(conn, ["ANET", "AVGO", "ORCL"])
    assert result.inserted == 1, result
    assert {t for t, _ in result.failures} == {"AVGO", "ORCL"}, result.failures
    empty = conn.execute(
        "SELECT COUNT(*) AS n FROM raw_estimates WHERE ticker IN ('AVGO','ORCL')"
    ).fetchone()["n"]
    assert empty == 0, "값 없는 스냅샷이 적재되어 그날 재시도가 막힌다"

    # 나중에 성공하면 그때 적재되어야 한다 (같은 날 재실행)
    monkeypatch.setattr(
        estimates.yf, "Ticker",
        lambda t: type("T", (), {"info": {"forwardEps": 3.0, "trailingEps": 2.0}})(),
    )
    again = estimates.ingest(conn, ["AVGO"])
    assert again.inserted == 1, again
    row = conn.execute(
        "SELECT forward_eps FROM raw_estimates WHERE ticker='AVGO' AND snapshot_date=?",
        (today,),
    ).fetchone()
    assert row["forward_eps"] == 3.0, row


def test_missing_sec_user_agent_fails_loudly(monkeypatch):
    """SEC은 UA 없으면 403을 준다. 원인 모를 403 대신 안내문으로 죽어야 한다."""
    monkeypatch.setattr(config, "SEC_USER_AGENT", "")
    try:
        fundamentals.ingest(db.connect(), ["NVDA"])
    except RuntimeError as exc:
        assert "SEC_EDGAR_USER_AGENT" in str(exc), exc
    else:
        raise AssertionError("UA 미설정인데 실패하지 않았다")


class _MonkeyPatch:
    """pytest 없이 직접 실행할 때 쓰는 최소 구현."""

    def __init__(self):
        self._undo = []

    def setattr(self, obj, name, value):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self):
        for obj, name, old in reversed(self._undo):
            setattr(obj, name, old)
        self._undo.clear()


if __name__ == "__main__":
    for fn in (
        test_price_failures_are_isolated,
        test_empty_estimates_do_not_poison_the_day,
        test_missing_sec_user_agent_fails_loudly,
    ):
        mp = _MonkeyPatch()
        try:
            fn(mp)
        finally:
            mp.undo()
    print("✅ 전체 통과: 종목 단위 실패 격리 · NaN 행 제외 · 빈 스냅샷 미적재 · UA 미설정 조기 실패")
