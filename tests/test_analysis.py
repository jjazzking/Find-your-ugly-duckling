"""analysis 3층 검증 — 합성 데이터에 게이트 케이스를 심고 판정을 확인한다.

실행: 레포 루트에서 `python -m tests.test_analysis` (pytest도 가능).
네트워크 불필요 (임시 SQLite만 사용).

레이어 ③(장비·파운드리) 5종목에 서로 다른 상황을 만든다:
  AMAT — 자기 밴드 하단 + 동종 할인 + 추정치 상향 + 낙폭 30%  → 통과, 레이어 1위
  LRCX — 밴드 상단 + 동종 할증 + 낙폭 없음                     → 통과, 하위
  KLA  — forward EPS 하락                                      → 탈락 (밸류트랩)
  TSM  — 성장률이 레이어 1등 대비 크게 열위                     → 탈락 (승자독식)
  ASML — 실적 발표 임박                                        → 워치리스트
  NVDA — 가격 없이 펀더멘털만 (룩어헤드 단위 검증용)            → 보류
"""

import datetime as dt
import os
import tempfile

os.environ["DUCKLING_DB"] = os.path.join(tempfile.mkdtemp(), "test_analysis.db")

from src import facts
from src.analysis import dashboard, gates, metrics, report, scoring
from src.storage import db

ASOF = dt.date(2026, 8, 7)          # 금요일
ASOF_S = ASOF.isoformat()
NOW = "2026-08-07T00:00:00+00:00"
PRICE_HISTORY_DAYS = 1170           # 약 3.2년 → 밴드 표본 250일 요건 충족
RAMP_DAYS = 60                      # 최근 가격 변화 구간
LAST_FILED_OFFSET = 30              # 대부분 종목: 마지막 공시가 30일 전


def _trading_days() -> list[str]:
    days, d = [], ASOF - dt.timedelta(days=PRICE_HISTORY_DAYS)
    while d <= ASOF:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += dt.timedelta(days=1)
    return days


DAYS = _trading_days()


def _put_prices(conn, ticker, path):
    """path(i, n) → 조정종가."""
    n = len(DAYS)
    conn.executemany(
        "INSERT INTO raw_prices VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (ticker, d, p, p, p, p, p, 1000, NOW)
            for i, d in enumerate(DAYS)
            for p in [path(i, n)]
        ],
    )


def _put_quarters(conn, ticker, last_end_offset=60, eps=1.0, latest_eps=None, n=24):
    """분기 EPS n개. 공시일 = 분기종료 + 30일 (실제 10-Q 리듬)."""
    for j in range(n):
        end = ASOF - dt.timedelta(days=last_end_offset + 91 * j)
        start = end - dt.timedelta(days=90)
        value = latest_eps if (j == 0 and latest_eps is not None) else eps
        conn.execute(
            "INSERT INTO raw_fundamentals VALUES (?,?,?,?,?,?,?,?,?,?,'edgar',?)",
            (
                ticker, "EarningsPerShareDiluted", "USD/shares",
                start.isoformat(), end.isoformat(), end.year, None, "10-Q",
                (end + dt.timedelta(days=30)).isoformat(), value, NOW,
            ),
        )


def _put_estimates(conn, ticker, fwd):
    """fwd(k) → k일 전 스냅샷의 forward EPS."""
    conn.executemany(
        "INSERT INTO raw_estimates VALUES (?,?,?,?,?,?,'yfinance',?)",
        [
            ((ticker), (ASOF - dt.timedelta(days=k)).isoformat(), fwd(k), None, 4.0, 30, NOW)
            for k in range(120, -1, -1)
        ],
    )


def _flat(level):
    return lambda i, n: level


def _ramp(level, target):
    def path(i, n):
        if i < n - RAMP_DAYS:
            return level
        return level + (target - level) * (i - (n - RAMP_DAYS)) / RAMP_DAYS
    return path


def seed(conn):
    # 가격: TTM EPS는 전 종목 4.00이므로 PER = 가격 / 4
    _put_prices(conn, "AMAT", _ramp(200, 140))   # PER 50 → 35 (밴드 하단, 낙폭 -30%)
    _put_prices(conn, "LRCX", _ramp(200, 260))   # PER 50 → 65 (밴드 상단, 낙폭 0)
    for t in ("KLA", "ASML", "TSM"):
        _put_prices(conn, t, _flat(200))         # PER 50 고정

    for t in ("AMAT", "LRCX", "KLA", "TSM"):
        _put_quarters(conn, t)                   # 마지막 공시 30일 전 → 다음 실적 61일 후
    _put_quarters(conn, "ASML", last_end_offset=118)   # 마지막 공시 88일 전 → 실적 임박
    _put_quarters(conn, "NVDA", latest_eps=2.0)        # 최신 분기만 2.00 (룩어헤드 검증)

    _put_estimates(conn, "AMAT", lambda k: 5.6 - 0.005 * k)   # 상향 (성장 40%)
    _put_estimates(conn, "LRCX", lambda k: 4.8)               # 보합 (성장 20%)
    _put_estimates(conn, "KLA", lambda k: 4.4 + 0.005 * k)    # 하향 → 밸류트랩
    _put_estimates(conn, "ASML", lambda k: 4.5)
    _put_estimates(conn, "TSM", lambda k: 4.1)                # 성장 2.5% → 승자독식 열위
    conn.commit()


_CONN = None


def _db():
    """씨딩된 커넥션 (pytest·직접 실행 어느 쪽에서도 한 번만 심는다)."""
    global _CONN
    if _CONN is None:
        _CONN = db.connect()
        seed(_CONN)
    return _CONN


def test_lookahead_cutoff():
    """공시 전날에는 그 분기를 몰라야 한다."""
    conn = _db()
    last_filed = ASOF - dt.timedelta(days=LAST_FILED_OFFSET)
    before = (last_filed - dt.timedelta(days=1)).isoformat()
    after = last_filed.isoformat()
    ttm = facts.ttm_by_date(conn, "NVDA", [before, after])
    assert abs(ttm[before] - 4.0) < 1e-9, f"공시 전에 최신 분기를 참조함: {ttm[before]}"
    assert abs(ttm[after] - 5.0) < 1e-9, f"공시 후 반영 실패: {ttm[after]}"


def test_gates_and_scores():
    conn = _db()
    ms = metrics.compute(conn, ASOF_S)
    gr = gates.evaluate(conn, ms, ASOF_S)
    sc = scoring.compute(ms, gr)

    assert gr["AMAT"].status == gates.PASS, gr["AMAT"]
    assert gr["LRCX"].status == gates.PASS, gr["LRCX"]
    assert gr["KLA"].status == gates.FAIL and "밸류트랩" in gr["KLA"].reason, gr["KLA"]
    assert gr["TSM"].status == gates.FAIL and "승자독식" in gr["TSM"].reason, gr["TSM"]
    assert gr["ASML"].status == gates.WATCH, gr["ASML"]
    assert gr["NVDA"].status == gates.HOLD, gr["NVDA"]     # 가격 없음
    assert gr["EQIX"].status == gates.UNSUPPORTED, gr["EQIX"]  # FFO배수

    # 탈락·보류·워치리스트는 채점하지 않는다
    for t in ("KLA", "TSM", "ASML", "NVDA", "EQIX"):
        assert sc[t].score is None, f"{t}는 채점 대상이 아님"

    # 레이어 내부 순위: 싼 쪽이 위
    assert sc["AMAT"].layer_rank == 1, sc["AMAT"]
    assert sc["LRCX"].layer_rank == 2, sc["LRCX"]
    assert sc["AMAT"].score > sc["LRCX"].score

    # 구성요소가 의도대로 계산되는지 (밴드 하단·동종 할인 만점·낙폭 -30%)
    c = sc["AMAT"].components
    assert c["밴드"] > 0.95, c
    assert c["동종할인"] > 0.95, c            # 동종 중앙값 대비 ~30% 할인 (만점 근처)
    assert abs(c["낙폭"] - 0.6) < 0.05, c     # -30% → (0.30-0.15)/(0.40-0.15)
    assert abs(sc["AMAT"].confidence - 0.85) < 1e-9   # 순도 M
    # 낙폭 기준선(-15%) 미달은 0점
    assert sc["LRCX"].components["낙폭"] == 0.0, sc["LRCX"].components

    # 저장이 멱등인지 (재실행 시 중복 없이 덮어써야 함)
    for _ in range(2):
        metrics.persist(conn, ASOF_S, ms)
        scoring.persist(conn, ASOF_S, sc, gr)
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM derived_scores WHERE date = ?", (ASOF_S,)
    ).fetchone()["n"]
    assert n == len(ms), f"derived_scores 행 수 {n} != 유니버스 {len(ms)}"

    path = report.write_markdown(conn, ASOF_S, ms, gr, sc)
    body = open(path, encoding="utf-8").read()
    assert "AMAT" in body and "밸류트랩" in body
    globals()["LAST_SCORES"] = sc


def test_dashboard_payload():
    """대시보드가 화면에서 쓰는 키를 페이로드가 전부 담고 있는지.

    (열 하나를 빠뜨려도 HTML은 멀쩡히 그려지고 값만 '—'로 나오므로,
     눈으로 보기 전에 여기서 걸린다.)
    """
    conn = _db()
    ms = metrics.compute(conn, ASOF_S)
    gr = gates.evaluate(conn, ms, ASOF_S)
    sc = scoring.compute(ms, gr)
    metrics.persist(conn, ASOF_S, ms)
    scoring.persist(conn, ASOF_S, sc, gr)

    payload = dashboard.build_payload(conn, ASOF_S)
    required = {
        "ticker", "name", "layer", "layer_name", "aux_layers", "waves", "purity",
        "valuation", "status", "reason", "score", "rank", "confidence", "basis",
        "components", "contributions", "price", "ttm_eps", "per", "band", "band_n",
        "fwd_eps", "forward_per", "growth", "peg", "drawdown", "trend30", "trend90",
        "peer_group", "peer_median", "peer_discount", "layer_band_median",
    }
    for row in payload["stocks"]:
        missing = required - set(row)
        assert not missing, f"{row['ticker']} 페이로드 키 누락: {sorted(missing)}"
    assert len(payload["stocks"]) == len(ms)

    amat = next(r for r in payload["stocks"] if r["ticker"] == "AMAT")
    assert amat["peer_discount"] is not None and amat["peer_discount"] > 0.2, amat
    # 스택바 세그먼트 합 = 총점 (결측 구성요소 재정규화 반영)
    assert abs(sum(amat["contributions"].values()) - amat["score"]) < 1e-6

    html = dashboard.render(payload)
    assert dashboard.PLACEHOLDER not in html, "페이로드가 주입되지 않음"
    assert "</script>" not in html.split("<script>")[1].split("renderHead")[0], \
        "JSON 안의 </ 이스케이프 실패 — 스크립트 블록이 조기 종료됨"


if __name__ == "__main__":
    test_lookahead_cutoff()
    test_gates_and_scores()
    test_dashboard_payload()
    sc = LAST_SCORES
    print(
        f"✅ 전체 통과: 룩어헤드 차단 · 게이트 5종 판정 · 대시보드 페이로드 · 레이어 랭킹 "
        f"(AMAT {sc['AMAT'].score:.1f}점 > LRCX {sc['LRCX'].score:.1f}점)"
    )
