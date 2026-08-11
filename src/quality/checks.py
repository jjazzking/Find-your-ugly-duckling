"""정합성 검증 규칙 7종 (ARCHITECTURE.md §3).

각 체크는 Finding 목록을 반환한다. severity='error'가 하나라도 있으면
그날 analysis는 실행되지 않는다 (jobs/daily_analyze.py가 게이트).
허용 오차 수치는 실데이터를 보며 튜닝한다 (ARCHITECTURE.md §5 오픈 항목).
"""

import datetime as dt
from dataclasses import dataclass

from src import config, universe
from src.facts import EPS_CONCEPTS, eps_quarter_series, ttm_eps  # noqa: F401 (재노출)

CROSS_SOURCE_TOLERANCE = 0.25   # EDGAR TTM vs yfinance trailing EPS 상대 오차
CROSS_SOURCE_ABS_FLOOR = 0.10   # 저EPS 종목의 상대오차 폭발 방지
DAILY_MOVE_LIMIT = 0.30         # 일간 ±30% 급변 → 분할 미조정 의심
RECENT_WINDOW_DAYS = 30         # 시계열 무결성 검사 구간
STALE_QUARTER_DAYS = 200        # 최신 분기 end_date가 이보다 오래되면 경고


@dataclass
class Finding:
    check_name: str
    severity: str  # 'error' | 'warning'
    ticker: str | None
    detail: str


def run_all(conn, run_date: str) -> list[Finding]:
    tickers = universe.tickers()
    findings: list[Finding] = []
    findings += check_price_series(conn, tickers)
    findings += check_freshness(conn, tickers)
    findings += check_filed_dates(conn, tickers)
    findings += check_cross_source_eps(conn, tickers)
    findings += check_ttm_computable(conn, tickers)
    findings += check_coverage(conn, tickers)
    findings += check_universe_structure()
    return findings


# ── 헬퍼: 가격 ────────────────────────────────────────────────


def _recent_dates(conn, ticker: str, limit: int) -> list[str]:
    rows = conn.execute(
        "SELECT date FROM raw_prices WHERE ticker = ? ORDER BY date DESC LIMIT ?",
        (ticker, limit),
    ).fetchall()
    return sorted(r["date"] for r in rows)


# 분기 EPS 재구성·TTM은 src/facts.py 공용 (analysis도 같은 규칙을 써야 하므로).


# ── 체크 1: 크로스소스 EPS 대조 (오류) ────────────────────────


def check_cross_source_eps(conn, tickers) -> list[Finding]:
    findings = []
    for t in tickers:
        row = conn.execute(
            "SELECT trailing_eps FROM raw_estimates WHERE ticker = ? "
            "ORDER BY snapshot_date DESC LIMIT 1",
            (t,),
        ).fetchone()
        yf_ttm = row["trailing_eps"] if row else None
        quarters, unit_note = eps_quarter_series(conn, t)
        if unit_note:
            findings.append(Finding(
                "크로스소스대조", "warning", t,
                f"EDGAR EPS 단위가 {unit_note} — USD 환산 전이라 대조 생략",
            ))
            continue
        edgar_ttm = ttm_eps(quarters)
        if yf_ttm is None or edgar_ttm is None:
            continue  # 데이터 부재는 커버리지/TTM 체크가 담당
        diff = abs(edgar_ttm - yf_ttm)
        if diff > CROSS_SOURCE_ABS_FLOOR + CROSS_SOURCE_TOLERANCE * max(abs(yf_ttm), abs(edgar_ttm)):
            findings.append(Finding(
                "크로스소스대조", "error", t,
                f"EDGAR TTM EPS {edgar_ttm:.2f} vs yfinance trailing {yf_ttm:.2f} — 허용 오차 초과",
            ))
    return findings


# ── 체크 2+3: 시계열 무결성 · 조정 일관성 (오류) ──────────────


def check_price_series(conn, tickers) -> list[Finding]:
    findings = []
    market_dates = _recent_dates(conn, config.BENCHMARK_TICKER, RECENT_WINDOW_DAYS)
    if not market_dates:
        return [Finding("시계열무결성", "error", None, "기준 티커(SPY) 가격 데이터 없음")]
    market_set = set(market_dates)
    for t in tickers:
        rows = conn.execute(
            "SELECT date, close, adj_close FROM raw_prices "
            "WHERE ticker = ? AND date >= ? ORDER BY date",
            (t, market_dates[0]),
        ).fetchall()
        if not rows:
            continue  # 커버리지 체크가 담당
        have = {r["date"] for r in rows}
        # 상장 이전 구간은 결측이 아니다: 첫 데이터일 이후만 본다
        first = rows[0]["date"]
        missing = sorted(d for d in market_set if d > first and d not in have)
        if missing:
            findings.append(Finding(
                "시계열무결성", "error", t,
                f"거래일 가격 결측 {len(missing)}일: {', '.join(missing[:5])}"
                + (" ..." if len(missing) > 5 else ""),
            ))
        prev_adj = None
        for r in rows:
            if r["adj_close"] is None or r["adj_close"] <= 0:
                findings.append(Finding("조정일관성", "error", t, f"{r['date']} adj_close 이상값"))
                break
            if prev_adj is not None:
                move = abs(r["adj_close"] / prev_adj - 1)
                if move > DAILY_MOVE_LIMIT:
                    findings.append(Finding(
                        "시계열무결성", "error", t,
                        f"{r['date']} 조정종가 일간 {move:+.0%} 급변 — 분할 미조정 의심",
                    ))
            prev_adj = r["adj_close"]
    return findings


# ── 체크 4: 공시일 정합성 (오류) ──────────────────────────────


def check_filed_dates(conn, tickers) -> list[Finding]:
    findings = []
    for t in tickers:
        rows = conn.execute(
            "SELECT concept, end_date, filed_date FROM raw_fundamentals "
            "WHERE ticker = ? AND filed_date < end_date LIMIT 3",
            (t,),
        ).fetchall()
        for r in rows:
            findings.append(Finding(
                "공시일정합성", "error", t,
                f"{r['concept']} 기간종료 {r['end_date']} > 공시일 {r['filed_date']} — 룩어헤드 위험",
            ))
    return findings


# ── 체크 5: TTM 계산 가능성 (경고) ────────────────────────────


def check_ttm_computable(conn, tickers) -> list[Finding]:
    findings = []
    today = dt.date.today()
    for t in tickers:
        quarters, unit_note = eps_quarter_series(conn, t)
        if unit_note:
            continue  # 크로스소스 체크에서 이미 보고됨
        if not quarters:
            continue  # 커버리지 체크가 담당
        if ttm_eps(quarters) is None:
            findings.append(Finding(
                "TTM검증", "warning", t,
                f"연속된 4개 분기 EPS를 재구성하지 못함 (보유 분기 {len(quarters)}개)",
            ))
            continue
        latest_end = dt.date.fromisoformat(quarters[-1][0])
        if (today - latest_end).days > STALE_QUARTER_DAYS:
            findings.append(Finding(
                "TTM검증", "warning", t,
                f"최신 분기 종료일 {latest_end} — {STALE_QUARTER_DAYS}일 이상 경과",
            ))
    return findings


# ── 체크 6: 유니버스 커버리지 (경고) ──────────────────────────


def check_coverage(conn, tickers) -> list[Finding]:
    findings = []
    sources = {
        "raw_prices": "SELECT COUNT(*) AS n FROM raw_prices WHERE ticker = ?",
        "raw_fundamentals": "SELECT COUNT(*) AS n FROM raw_fundamentals WHERE ticker = ?",
        "raw_estimates": (
            "SELECT COUNT(*) AS n FROM raw_estimates WHERE ticker = ? "
            "AND (forward_eps IS NOT NULL OR trailing_eps IS NOT NULL)"
        ),
    }
    for t in tickers:
        for table, sql in sources.items():
            if conn.execute(sql, (t,)).fetchone()["n"] == 0:
                findings.append(Finding("커버리지", "warning", t, f"{table}에 데이터 없음"))
    return findings


# ── 체크 8: 유니버스 구조 (경고) ──────────────────────────────


def check_universe_structure() -> list[Finding]:
    """레이어가 동종 비교를 성립시킬 만큼의 인원을 갖췄는지.

    잣대가 구현된 종목이 레이어에 3개 미만이면 중앙값이 만들어지지 않아
    '동종 할인'(가중치 0.25)이 통째로 빠진 채 점수가 나온다. 데이터 문제가
    아니라 유니버스 설계 문제이므로 경고로 남긴다.
    """
    stocks = universe.load()
    by_layer: dict[int, list[str]] = {}
    for ticker, attrs in stocks.items():
        if attrs["valuation"] in universe.PER_VALUATIONS:
            by_layer.setdefault(attrs["layer"], []).append(ticker)
    findings = []
    for layer in sorted({a["layer"] for a in stocks.values()}):
        members = by_layer.get(layer, [])
        if len(members) < universe.MIN_PEER_GROUP:
            findings.append(Finding(
                "유니버스구조", "warning", None,
                f"레이어 {layer}: 잣대 구현 종목 {len(members)}개"
                f"(최소 {universe.MIN_PEER_GROUP}) — 동종 할인이 산출되지 않음"
                f"{' [' + ', '.join(members) + ']' if members else ''}",
            ))
    return findings


# ── 체크 7: 신선도 (오류) ─────────────────────────────────────


def check_freshness(conn, tickers) -> list[Finding]:
    findings = []
    market_latest = conn.execute(
        "SELECT MAX(date) AS d FROM raw_prices WHERE ticker = ?",
        (config.BENCHMARK_TICKER,),
    ).fetchone()["d"]
    if market_latest is None:
        return findings  # 시계열 체크에서 이미 오류 보고됨
    for t in tickers:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM raw_prices WHERE ticker = ?", (t,)
        ).fetchone()
        if row["d"] is not None and row["d"] < market_latest:
            findings.append(Finding(
                "신선도", "error", t,
                f"최신 수집일 {row['d']} < 시장 최신 거래일 {market_latest}",
            ))
    return findings
