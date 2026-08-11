"""게이트 G0~G3 — 점수 계산 자격을 먼저 판정한다 (분석 3층 중 2층).

게이트가 점수보다 앞에 있는 이유: 미운 오리는 "싼 종목"이 아니라 "이익 추정이
온전한데 싼 종목"이다 (철학 §1 명제 3). 추정치가 무너지는 중인 종목은 아무리
싸도 후보가 아니므로, 감점이 아니라 탈락으로 처리한다.

판정 결과:
  통과       — 점수 계산 대상
  탈락       — 후보 아님 (밸류트랩·승자독식)
  워치리스트 — 프리어닝. 제외하되 감시는 계속 (명제 4)
  보류       — 판정에 필요한 데이터가 아직 없음. '통과'로 치지 않는다
  잣대미구현 — FFO배수·EV/Sales 종목
"""

import datetime as dt
import statistics
from dataclasses import dataclass

from src import facts
from src.analysis import metrics

PREEARNINGS_WINDOW_DAYS = 14      # G0: 실적 D-14 이내면 워치리스트
PREEARNINGS_OVERDUE_DAYS = 30     # 예상일이 지났는데 공시가 없으면 '임박'으로 본다
EST_DECLINE_TOLERANCE = -0.01     # G1: -1%까지는 잡음으로 본다
MIN_ESTIMATE_HISTORY_DAYS = 30    # G1 판정에 필요한 최소 스냅샷 이력
LAYER_BOTTOM_PERCENTILE = 20.0    # G2: 레이어 중앙값이 이 아래면 신뢰도 강등
LEADER_GROWTH_RATIO = 0.30        # G3: 1등 성장률의 30% 미만이면 탈락
MIN_LEADER_PEERS = 3              # G3 적용 최소 인원

PASS, FAIL, WATCH, HOLD, UNSUPPORTED = "통과", "탈락", "워치리스트", "보류", "잣대미구현"


@dataclass
class GateResult:
    status: str
    reason: str
    layer_degraded: bool = False


def evaluate(conn, metrics: dict, asof: str) -> dict[str, GateResult]:
    growth_by_layer: dict[int, list[float]] = {}
    for m in metrics.values():
        if m.valuation_supported and m.growth_pct is not None:
            growth_by_layer.setdefault(m.layer, []).append(m.growth_pct)

    est_history_ok = _estimate_history_days(conn, asof) >= MIN_ESTIMATE_HISTORY_DAYS
    return {
        t: _evaluate_one(conn, m, asof, growth_by_layer, est_history_ok)
        for t, m in metrics.items()
    }


def _evaluate_one(conn, m, asof, growth_by_layer, est_history_ok) -> GateResult:
    if not m.valuation_supported:
        return GateResult(UNSUPPORTED, f"{m.valuation} 잣대 미구현")
    if m.per is None:
        missing = "가격" if m.price is None else "TTM EPS(적자 또는 분기 부족)"
        return GateResult(HOLD, f"지표 부족 — {missing}")
    if m.band_percentile is None and m.peer_discount is None:
        # 명제 3의 '밸류에이션 근거'가 자기 밴드·동종 비교 둘 다 없으면 성립하지 않는다
        return GateResult(
            HOLD,
            f"밸류에이션 근거 없음 — 밴드 표본 {m.band_n}일"
            f"(최소 {metrics.MIN_BAND_OBSERVATIONS}), 동종 그룹 미형성",
        )

    # ── G0 프리어닝 (명제 4: 실적 직전에는 추천하지 않는다) ──
    expected = _expected_next_earnings(conn, m.ticker)
    if expected is not None:
        days_to = (expected - dt.date.fromisoformat(asof)).days
        if -PREEARNINGS_OVERDUE_DAYS <= days_to <= PREEARNINGS_WINDOW_DAYS:
            return GateResult(WATCH, f"실적 예상일 {expected} (D{days_to:+d}) — 프리어닝 제외")

    # ── G1 밸류트랩 (추정치 궤적) ──
    t30, t90 = m.est_trend.get(30), m.est_trend.get(90)
    if t30 is None and t90 is None:
        detail = "추정치 스냅샷 이력 부족" if not est_history_ok else "이 종목의 과거 스냅샷 없음"
        return GateResult(HOLD, f"G1 판정 불가 — {detail}")
    declining = [
        f"{w}일 {v:+.1%}" for w, v in ((30, t30), (90, t90))
        if v is not None and v < EST_DECLINE_TOLERANCE
    ]
    if declining:
        return GateResult(FAIL, f"밸류트랩 — forward EPS {', '.join(declining)}")

    # ── G3 승자독식 (반증조건 3) ──
    peers = growth_by_layer.get(m.layer, [])
    if len(peers) >= MIN_LEADER_PEERS and m.growth_pct is not None:
        leader = max(peers)
        if leader > 0 and m.growth_pct < LEADER_GROWTH_RATIO * leader:
            return GateResult(
                FAIL,
                f"승자독식 열위 — 성장률 {m.growth_pct:.0f}% vs 레이어 1등 {leader:.0f}%",
            )

    # ── G2 레이어 동반 하단 (반증조건 2: 싸다 = 베타일 뿐) ──
    if m.layer_band_median is not None and m.layer_band_median <= LAYER_BOTTOM_PERCENTILE:
        return GateResult(
            PASS,
            f"레이어 동반 하단(중앙값 {m.layer_band_median:.0f}%) — 신뢰도 강등",
            layer_degraded=True,
        )
    return GateResult(PASS, "")


# ── 헬퍼 ──────────────────────────────────────────────────────


def _expected_next_earnings(conn, ticker: str) -> dt.date | None:
    """공시 주기로 다음 실적일을 추정한다.

    실적 캘린더를 별도 수집하지 않으므로 EDGAR 공시일 간격의 중앙값을 쓴다.
    근사치이며, 정식 캘린더 원천이 생기면 교체할 자리다 (ARCHITECTURE §5).
    """
    quarters, unit_note = facts.eps_quarter_series(conn, ticker)
    if unit_note or len(quarters) < 3:
        return None
    filed = sorted({dt.date.fromisoformat(f) for _, _, f in quarters})
    if len(filed) < 3:
        return None
    gaps = [(b - a).days for a, b in zip(filed, filed[1:])]
    # 연 1회(10-K) 간격이 섞여 중앙값이 왜곡되지 않도록 분기 범위만 본다
    quarterly_gaps = [g for g in gaps if 60 <= g <= 130] or gaps
    return filed[-1] + dt.timedelta(days=int(statistics.median(quarterly_gaps)))


def _estimate_history_days(conn, asof: str) -> int:
    """추정치 스냅샷이 며칠치 쌓였는지 (G1 판정 가능 시점 안내용)."""
    row = conn.execute(
        "SELECT MIN(snapshot_date) AS d FROM raw_estimates WHERE snapshot_date <= ?",
        (asof,),
    ).fetchone()
    if row["d"] is None:
        return 0
    return (dt.date.fromisoformat(asof) - dt.date.fromisoformat(row["d"])).days
