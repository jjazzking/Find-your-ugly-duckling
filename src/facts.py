"""raw_fundamentals에서 분기 EPS 시계열을 재구성한다 (quality·analysis 공용).

quality는 "지금 데이터가 말이 되는가"를 보고, analysis는 "그날 알 수 있던 값"만
써야 한다. 재구성 규칙은 같고 공시일 컷오프(asof)만 다르므로 여기 한 곳에 둔다.

XBRL 관행상 Q4는 분기 fact로 따로 공시되지 않는 경우가 많다 (10-K에 FY만).
→ Q4 = FY − (Q1+Q2+Q3)로 유도하고, 공시일은 10-K의 것을 물려받는다.
"""

import datetime as dt

# EPS 개념 우선순위: US-GAAP 희석 → IFRS 희석 → basic 순
EPS_CONCEPTS = [
    "EarningsPerShareDiluted",
    "DilutedEarningsLossPerShare",
    "EarningsPerShareBasic",
    "BasicEarningsLossPerShare",
]

QUARTER_SPAN = (60, 120)   # 일 단위: 이 범위면 분기 fact로 본다
ANNUAL_SPAN = (330, 390)
TTM_MAX_SPAN_DAYS = 400    # 4개 분기가 이보다 벌어지면 연속으로 보지 않는다


def load_eps_rows(conn, ticker: str) -> tuple[list[dict], str | None]:
    """(rows, unit_note). unit_note가 있으면 USD EPS가 없다는 뜻 (예: TSM은 TWD 공시)."""
    for concept in EPS_CONCEPTS:
        rows = conn.execute(
            "SELECT unit, start_date, end_date, filed_date, value FROM raw_fundamentals "
            "WHERE ticker = ? AND concept = ? AND start_date != ''",
            (ticker, concept),
        ).fetchall()
        if not rows:
            continue
        usd = [dict(r) for r in rows if r["unit"].upper().startswith("USD")]
        if not usd:
            return [], rows[0]["unit"]
        return usd, None
    return [], None


def reconstruct(rows: list[dict], asof: str | None = None):
    """[(end_date, eps, filed_date), ...] 시간순. asof가 있으면 그날까지 공시된 것만."""
    if asof is not None:
        rows = [r for r in rows if r["filed_date"] <= asof]
    quarterly: dict[str, tuple[float, str]] = {}   # end_date -> (value, filed_date)
    annual: list[tuple[str, str, float, str]] = []  # (start, end, value, filed)
    for r in rows:
        days = (
            dt.date.fromisoformat(r["end_date"]) - dt.date.fromisoformat(r["start_date"])
        ).days
        if QUARTER_SPAN[0] <= days <= QUARTER_SPAN[1]:
            prev = quarterly.get(r["end_date"])
            if prev is None or r["filed_date"] < prev[1]:
                # 같은 분기 재공시(비교표시 포함)는 최초 공시분을 쓴다:
                # "그 시점에 알 수 있던 값" 원칙 (append-only와 동일한 취지)
                quarterly[r["end_date"]] = (r["value"], r["filed_date"])
        elif ANNUAL_SPAN[0] <= days <= ANNUAL_SPAN[1]:
            annual.append((r["start_date"], r["end_date"], r["value"], r["filed_date"]))
    for a_start, a_end, a_val, a_filed in annual:
        if a_end in quarterly:
            continue
        inside = [v for e, (v, _) in quarterly.items() if a_start < e < a_end]
        if len(inside) == 3:
            quarterly[a_end] = (round(a_val - sum(inside), 4), a_filed)
    return sorted((end, val, filed) for end, (val, filed) in quarterly.items())


def eps_quarter_series(conn, ticker: str, asof: str | None = None):
    """(quarters, unit_note) — 편의 래퍼."""
    rows, unit_note = load_eps_rows(conn, ticker)
    if unit_note:
        return [], unit_note
    return reconstruct(rows, asof), None


def ttm_eps(quarters) -> float | None:
    """마지막 4개 분기가 연속(총 스팬 ≤ 400일)일 때만 TTM을 계산한다."""
    if len(quarters) < 4:
        return None
    last4 = quarters[-4:]
    span = (
        dt.date.fromisoformat(last4[-1][0]) - dt.date.fromisoformat(last4[0][0])
    ).days
    if span > TTM_MAX_SPAN_DAYS:
        return None
    return sum(v for _, v, _ in last4)


def ttm_by_date(conn, ticker: str, dates: list[str]) -> dict[str, float]:
    """날짜별 TTM EPS — 각 날짜에 **그날까지 공시된** 분기만 사용한다.

    TTM은 새 공시가 도착할 때만 바뀌므로, 공시일 경계마다 한 번씩만 재구성한다.
    (날짜마다 재구성하면 5년치 × 32종목에서 불필요하게 느려진다.)
    """
    rows, unit_note = load_eps_rows(conn, ticker)
    if unit_note or not rows:
        return {}
    filed = sorted({r["filed_date"] for r in rows})
    cache: dict[str, float | None] = {}
    out: dict[str, float] = {}
    for d in dates:
        applicable = [f for f in filed if f <= d]
        if not applicable:
            continue
        boundary = applicable[-1]
        if boundary not in cache:
            cache[boundary] = ttm_eps(reconstruct(rows, asof=boundary))
        val = cache[boundary]
        if val is not None:
            out[d] = val
    return out
