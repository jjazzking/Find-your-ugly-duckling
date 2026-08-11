"""일간 미운 오리 리포트 (markdown 한 장)."""

import datetime as dt

from src import config
from src.analysis import gates, scoring

WEIGHTS_NOTE = " / ".join(f"{k} {v}" for k, v in scoring.WEIGHTS.items())

LAYER_NAMES = {
    1: "① 전력·그리드",
    2: "② DC 부동산·구축",
    3: "③ 장비·파운드리",
    4: "④ 칩",
    5: "⑤ 시스템·네트워킹·냉각",
    6: "⑥ 클라우드 플랫폼",
    7: "⑦ 모델",
    8: "⑧ 응용 SW",
    9: "⑨ 피지컬 AI",
}


def _pct(x, digits=0):
    return "—" if x is None else f"{x:.{digits}%}"


def _num(x, digits=1):
    return "—" if x is None else f"{x:.{digits}f}"


def write_markdown(conn, date: str, metrics: dict, gate_results: dict, scores: dict) -> str:
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.REPORTS_DIR / f"duckling_{date}.md"

    by_status: dict[str, list[str]] = {}
    for t, g in gate_results.items():
        by_status.setdefault(g.status, []).append(t)

    lines = [
        f"# 미운 오리 리포트 — {date}",
        "",
        f"유니버스 {len(metrics)}종목 · 게이트 통과 **{len(by_status.get(gates.PASS, []))}종목**",
        "",
        "| 게이트 판정 | 종목 수 |",
        "|---|---|",
    ]
    for status in (gates.PASS, gates.WATCH, gates.FAIL, gates.HOLD,
                   gates.UNSUPPORTED, gates.OBSERVE_ONLY):
        lines.append(f"| {status} | {len(by_status.get(status, []))} |")

    lines += _ranking_section(metrics, gate_results, scores)
    lines += _list_section(
        "워치리스트 (프리어닝)", by_status.get(gates.WATCH, []), gate_results,
        "명제 4: 실적 직전에는 추천하지 않되 감시는 계속한다.",
    )
    lines += _list_section(
        "탈락", by_status.get(gates.FAIL, []), gate_results,
        "싼 것과 싸야 마땅한 것을 가르는 자리.",
    )
    lines += _list_section(
        "보류 (판정 불가)", by_status.get(gates.HOLD, []), gate_results,
        "데이터가 없어서 판정을 못 한 것이지 통과가 아니다.",
    )
    lines += _list_section(
        "잣대 미구현", by_status.get(gates.UNSUPPORTED, []), gate_results,
        "FFO배수·EV/Sales 계열. PER을 대신 적용하지 않는다 (철학 §2).",
    )
    lines += _list_section(
        "관찰 전용", by_status.get(gates.OBSERVE_ONLY, []), gate_results,
        "동종 비교가 성립하지 않아 추천을 내지 않기로 한 레이어. 기다린다고 풀리는 상태가 아니다.",
    )

    lines += [
        "",
        "---",
        "",
        "**읽는 법**",
        "",
        "- 점수는 **레이어 내부 순위**로만 의미가 있다. 레이어 간 비교 금지 (잣대가 다름).",
        f"- 가중치 {WEIGHTS_NOTE}는 잠정값 — 3차 백테스트 전까지 튜닝 근거가 없다.",
        "- 신뢰도 = 순도 계수 × 레이어 강등 계수. 순도 L 종목의 점수는 원래 설명력이 약하다.",
        "- 모든 EPS는 **공시일 이후**에만 사용된다 (룩어헤드 차단).",
        "",
        f"_생성: {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}_",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _ranking_section(metrics, gate_results, scores) -> list[str]:
    ranked: dict[int, list] = {}
    for t, s in scores.items():
        if s.score is not None:
            ranked.setdefault(s.layer, []).append(s)
    if not ranked:
        return [
            "",
            "## 레이어별 랭킹",
            "",
            "> 오늘 채점된 종목이 없다. 아래 '보류' 사유를 확인할 것 "
            "— 추정치 스냅샷이 쌓이기 전에는 정상적인 상태다.",
        ]

    out = ["", "## 레이어별 랭킹", ""]
    for layer in sorted(ranked):
        out += [
            f"### {LAYER_NAMES.get(layer, layer)}",
            "",
            "| # | 티커 | 점수 | PER | 밴드% | 동종할인 | PEG | 낙폭 | 순도 | 신뢰도 | 근거 | 비고 |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for s in sorted(ranked[layer], key=lambda x: x.layer_rank):
            m = metrics[s.ticker]
            out.append(
                f"| {s.layer_rank} | **{s.ticker}** | {_num(s.score)} | {_num(m.per)} | "
                f"{_num(m.band_percentile, 0)} | {_pct(m.peer_discount)} | {_num(m.peg, 2)} | "
                f"{_pct(m.drawdown)} | {m.purity} | {s.confidence:.2f} | {s.basis} | "
                f"{gate_results[s.ticker].reason} |"
            )
        out.append("")
    return out


def _list_section(title: str, tickers: list[str], gate_results: dict, note: str) -> list[str]:
    if not tickers:
        return []
    out = ["", f"## {title}", "", f"> {note}", "", "| 티커 | 사유 |", "|---|---|"]
    out += [f"| {t} | {gate_results[t].reason} |" for t in sorted(tickers)]
    return out
