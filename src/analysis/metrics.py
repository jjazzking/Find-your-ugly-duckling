"""지표 계산 — raw만 읽고 derived_metrics를 만든다 (분석 3층 중 1층).

원칙 두 가지:
1. 룩어헤드 금지. PER 히스토리의 각 날짜는 **그날까지 공시된** EPS만 쓴다
   (facts.ttm_by_date). 오늘 아는 실적으로 과거 밴드를 그리면 밴드가 거짓이 된다.
2. 잣대는 레이어마다 다르다 (철학 §2). PER 계열이 아닌 종목은 지표를 만들지 않고
   'valuation_supported=False'로 넘긴다 — DC부동산에 PER을 몰래 적용하지 않는다.
"""

import datetime as dt
import statistics
from dataclasses import dataclass, field

from src import facts, universe

BAND_LOOKBACK_DAYS = 365 * 3      # 자기 밴드 산출 구간
MIN_BAND_OBSERVATIONS = 250       # 이보다 표본이 적으면 백분위를 내지 않는다 (약 1년)
DRAWDOWN_WINDOW_DAYS = 365        # 52주 고점
EST_TREND_WINDOWS = (30, 90)      # forward EPS 궤적 비교 시점 (일)
EST_TREND_SLACK_DAYS = 10         # 스냅샷이 정확히 그날 없을 때 허용하는 소급 범위

# 유니버스 스키마에 속한 값이므로 universe에서 가져온다 (quality도 같은 기준을 쓴다)
MIN_PEER_GROUP = universe.MIN_PEER_GROUP
PER_VALUATIONS = universe.PER_VALUATIONS


@dataclass
class Metric:
    ticker: str
    layer: int
    purity: str
    valuation: str
    valuation_supported: bool = True
    price: float | None = None
    ttm_eps: float | None = None
    per: float | None = None
    band_percentile: float | None = None
    band_n: int = 0
    forward_eps: float | None = None
    forward_per: float | None = None
    growth_pct: float | None = None
    peg: float | None = None
    drawdown: float | None = None
    est_trend: dict[int, float | None] = field(default_factory=dict)
    peer_group: str | None = None
    peer_median_per: float | None = None
    peer_discount: float | None = None
    layer_band_median: float | None = None


def compute(conn, asof: str) -> dict[str, Metric]:
    """asof(거래일) 기준 전 종목 지표. 반환은 티커 → Metric."""
    stocks = universe.load()
    out: dict[str, Metric] = {}
    for ticker, attrs in stocks.items():
        m = Metric(
            ticker=ticker,
            layer=attrs["layer"],
            purity=attrs["purity"],
            valuation=attrs["valuation"],
            valuation_supported=attrs["valuation"] in PER_VALUATIONS,
        )
        if m.valuation_supported:
            _fill_single(conn, m, asof)
        out[ticker] = m
    _fill_relative(out)
    return out


# ── 종목 단위 지표 ────────────────────────────────────────────


def _fill_single(conn, m: Metric, asof: str) -> None:
    row = conn.execute(
        "SELECT date, adj_close FROM raw_prices WHERE ticker = ? AND date <= ? "
        "AND adj_close > 0 ORDER BY date DESC LIMIT 1",
        (m.ticker, asof),
    ).fetchone()
    if row is None:
        return
    m.price = row["adj_close"]

    band_start = (dt.date.fromisoformat(asof) - dt.timedelta(days=BAND_LOOKBACK_DAYS)).isoformat()
    hist = conn.execute(
        "SELECT date, adj_close FROM raw_prices WHERE ticker = ? AND date BETWEEN ? AND ? "
        "AND adj_close > 0 ORDER BY date",
        (m.ticker, band_start, asof),
    ).fetchall()

    ttm_map = facts.ttm_by_date(conn, m.ticker, [r["date"] for r in hist])
    m.ttm_eps = ttm_map.get(row["date"])
    if m.ttm_eps is not None and m.ttm_eps > 0:
        m.per = m.price / m.ttm_eps

    # 자기 밴드: 적자 구간(TTM ≤ 0)의 PER은 의미가 없으므로 표본에서 뺀다
    per_hist = [
        r["adj_close"] / ttm_map[r["date"]]
        for r in hist
        if r["date"] in ttm_map and ttm_map[r["date"]] > 0
    ]
    m.band_n = len(per_hist)
    if m.per is not None and m.band_n >= MIN_BAND_OBSERVATIONS:
        below = sum(1 for p in per_hist if p <= m.per)
        m.band_percentile = 100.0 * below / m.band_n

    dd_start = (dt.date.fromisoformat(asof) - dt.timedelta(days=DRAWDOWN_WINDOW_DAYS)).isoformat()
    high = conn.execute(
        "SELECT MAX(adj_close) AS h FROM raw_prices WHERE ticker = ? AND date BETWEEN ? AND ?",
        (m.ticker, dd_start, asof),
    ).fetchone()["h"]
    if high:
        m.drawdown = m.price / high - 1

    _fill_estimates(conn, m, asof)


def _fill_estimates(conn, m: Metric, asof: str) -> None:
    snap = _snapshot_at(conn, m.ticker, asof)
    if snap is None:
        return
    m.forward_eps = snap["forward_eps"]
    if m.forward_eps and m.forward_eps > 0 and m.price:
        m.forward_per = m.price / m.forward_eps
    if m.forward_eps is not None and m.ttm_eps and m.ttm_eps > 0:
        m.growth_pct = (m.forward_eps / m.ttm_eps - 1) * 100
        if m.forward_per is not None and m.growth_pct > 0:
            m.peg = m.forward_per / m.growth_pct

    for window in EST_TREND_WINDOWS:
        m.est_trend[window] = None
        if not m.forward_eps or m.forward_eps <= 0:
            continue
        target = (dt.date.fromisoformat(asof) - dt.timedelta(days=window)).isoformat()
        past = _snapshot_at(conn, m.ticker, target, floor=(
            dt.date.fromisoformat(target) - dt.timedelta(days=EST_TREND_SLACK_DAYS)
        ).isoformat())
        if past and past["forward_eps"] and past["forward_eps"] > 0:
            m.est_trend[window] = m.forward_eps / past["forward_eps"] - 1


def _snapshot_at(conn, ticker: str, asof: str, floor: str | None = None):
    sql = (
        "SELECT snapshot_date, forward_eps FROM raw_estimates "
        "WHERE ticker = ? AND snapshot_date <= ? AND forward_eps IS NOT NULL"
    )
    params: list = [ticker, asof]
    if floor is not None:
        sql += " AND snapshot_date >= ?"
        params.append(floor)
    return conn.execute(sql + " ORDER BY snapshot_date DESC LIMIT 1", params).fetchone()


# ── 상대 지표 (동종 비교·레이어 집계) ─────────────────────────


def _fill_relative(metrics: dict[str, Metric]) -> None:
    """동종 할인과 레이어 밴드 중앙값.

    비교 그룹은 '레이어 + 순도'가 원칙이다 (순도가 다르면 같은 레이어라도
    멀티플의 의미가 다르다 — 철학 §2). 인원이 모자라면 레이어 전체로 완화하고,
    그것도 모자라면 동종 할인을 내지 않는다.
    """
    usable = [m for m in metrics.values() if m.valuation_supported and m.per and m.per > 0]

    by_layer_purity: dict[tuple[int, str], list[Metric]] = {}
    by_layer: dict[int, list[Metric]] = {}
    for m in usable:
        by_layer_purity.setdefault((m.layer, m.purity), []).append(m)
        by_layer.setdefault(m.layer, []).append(m)

    for m in usable:
        group = by_layer_purity[(m.layer, m.purity)]
        label = f"L{m.layer}/{m.purity}"
        if len(group) < MIN_PEER_GROUP:
            group = by_layer[m.layer]
            label = f"L{m.layer}"
        if len(group) < MIN_PEER_GROUP:
            continue
        peers = [p.per for p in group if p.ticker != m.ticker]
        if len(peers) < MIN_PEER_GROUP - 1:
            continue
        median = statistics.median(peers)
        if median <= 0:
            continue
        m.peer_group = label
        m.peer_median_per = median
        m.peer_discount = (median - m.per) / median

    band_by_layer: dict[int, list[float]] = {}
    for m in usable:
        if m.band_percentile is not None:
            band_by_layer.setdefault(m.layer, []).append(m.band_percentile)
    for m in metrics.values():
        vals = band_by_layer.get(m.layer)
        if vals:
            m.layer_band_median = statistics.median(vals)


# ── 저장 ──────────────────────────────────────────────────────


def persist(conn, date: str, metrics: dict[str, Metric]) -> None:
    """derived는 재계산 가능하므로 해당 날짜를 지우고 다시 쓴다 (재실행 멱등)."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn.execute("DELETE FROM derived_metrics WHERE date = ?", (date,))
    conn.executemany(
        "INSERT INTO derived_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                m.ticker, date, m.price, m.ttm_eps, m.per, m.band_percentile, m.band_n,
                m.forward_eps, m.forward_per, m.growth_pct, m.peg, m.drawdown,
                m.est_trend.get(30), m.est_trend.get(90),
                m.peer_group, m.peer_median_per, m.peer_discount,
                m.layer, m.layer_band_median, m.valuation, now,
            )
            for m in metrics.values()
        ],
    )
    conn.commit()
