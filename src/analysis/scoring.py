"""미운 오리 점수 — 게이트 통과 종목만 채점한다 (분석 3층 중 3층).

가중치는 **잠정값**이다. 3차 마일스톤 백테스트로 튜닝하기 전까지는
"합의된 출발점" 이상의 의미가 없다 (철학 §6 오픈 항목).

랭킹은 레이어 내부에서만 낸다. ②는 FFO배수, ⑧은 EV/Sales로 재는 종목들이라
전 레이어를 한 줄로 세우면 단위가 다른 숫자를 비교하게 된다 (철학 §2).
"""

import datetime as dt
from dataclasses import dataclass, field

from src.analysis import gates

WEIGHTS = {"밴드": 0.35, "동종할인": 0.25, "PEG": 0.25, "낙폭": 0.15}

DRAWDOWN_THRESHOLD = -0.15   # 이보다 얕은 낙폭은 0점 (사용자 확정: 고정 기준선)
DRAWDOWN_FULL = -0.40        # 이보다 깊으면 만점
PEG_FULL = 1.0               # PEG 1.0 이하 만점
PEG_ZERO = 3.0               # PEG 3.0 이상 0점
PEER_FULL_DISCOUNT = 0.30    # 동종 대비 30% 할인이면 만점

# 순도는 점수의 신뢰도 계수다: AI 매출 비중이 낮은 종목은
# 애초에 AI 밸류체인 밴드에 견주는 것 자체의 설명력이 약하다 (철학 §2)
PURITY_CONFIDENCE = {"H": 1.0, "M": 0.85, "L": 0.6}
LAYER_DEGRADE = 0.5          # G2 레이어 동반 하단 시 곱하는 계수


@dataclass
class Score:
    ticker: str
    layer: int
    components: dict[str, float] = field(default_factory=dict)
    score: float | None = None
    confidence: float = 1.0
    basis: str = ""
    layer_rank: int | None = None


def compute(metrics: dict, gate_results: dict[str, gates.GateResult]) -> dict[str, Score]:
    scores: dict[str, Score] = {}
    for ticker, m in metrics.items():
        s = Score(ticker=ticker, layer=m.layer)
        gate = gate_results[ticker]
        if gate.status != gates.PASS:
            scores[ticker] = s
            continue

        s.components = {
            "밴드": _band_component(m.band_percentile),
            "동종할인": _peer_component(m.peer_discount),
            "PEG": _peg_component(m.peg),
            "낙폭": _drawdown_component(m.drawdown),
        }
        available = {k: v for k, v in s.components.items() if v is not None}
        total_weight = sum(WEIGHTS[k] for k in available)
        if not available or total_weight == 0:
            scores[ticker] = s
            continue

        # 결측 구성요소가 있으면 남은 것들끼리 가중치를 재정규화한다.
        # (초기에는 forward 스냅샷 이력이 짧아 PEG가 자주 비어 있다)
        raw = sum(WEIGHTS[k] * v for k, v in available.items()) / total_weight
        s.confidence = PURITY_CONFIDENCE.get(m.purity, 0.6) * (
            LAYER_DEGRADE if gate.layer_degraded else 1.0
        )
        s.score = 100.0 * raw * s.confidence
        s.basis = "+".join(sorted(available))
        scores[ticker] = s

    _rank_within_layer(scores)
    return scores


def _rank_within_layer(scores: dict[str, Score]) -> None:
    by_layer: dict[int, list[Score]] = {}
    for s in scores.values():
        if s.score is not None:
            by_layer.setdefault(s.layer, []).append(s)
    for group in by_layer.values():
        for rank, s in enumerate(sorted(group, key=lambda x: -x.score), start=1):
            s.layer_rank = rank


# ── 구성요소: 전부 0~1로 정규화 (높을수록 '미운 오리'다움) ────


def _band_component(percentile: float | None) -> float | None:
    if percentile is None:
        return None
    return (100.0 - percentile) / 100.0


def _peer_component(discount: float | None) -> float | None:
    if discount is None:
        return None
    return _clamp(discount / PEER_FULL_DISCOUNT)   # 할증(음수)은 0점


def _peg_component(peg: float | None) -> float | None:
    if peg is None or peg <= 0:
        return None
    return _clamp((PEG_ZERO - peg) / (PEG_ZERO - PEG_FULL))


def _drawdown_component(drawdown: float | None) -> float | None:
    if drawdown is None:
        return None
    if drawdown > DRAWDOWN_THRESHOLD:
        return 0.0   # 기준선에 못 미치는 낙폭은 '미운 오리'의 조건이 아니다
    return _clamp(
        (drawdown - DRAWDOWN_THRESHOLD) / (DRAWDOWN_FULL - DRAWDOWN_THRESHOLD)
    )


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


# ── 저장 ──────────────────────────────────────────────────────


def persist(conn, date: str, scores: dict[str, Score], gate_results: dict) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn.execute("DELETE FROM derived_scores WHERE date = ?", (date,))
    conn.executemany(
        "INSERT INTO derived_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                s.ticker, date, gate_results[s.ticker].status,
                gate_results[s.ticker].reason or None, s.score,
                s.components.get("밴드"), s.components.get("동종할인"),
                s.components.get("PEG"), s.components.get("낙폭"),
                s.basis or None, s.confidence, s.layer, s.layer_rank, now,
            )
            for s in scores.values()
        ],
    )
    conn.commit()
