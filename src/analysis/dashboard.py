"""대시보드 생성 — derived 테이블을 읽어 자립형 HTML 한 장을 만든다.

analysis 결과를 사람이 검토하기 위한 화면이다. 네트워크·서버 없이 파일 하나로
열리도록 데이터를 HTML 안에 JSON으로 심는다 (로컬 파일에서 fetch가 막히므로).

  python -m jobs.daily_analyze   # 먼저 분석을 돌리고
  python -m jobs.dashboard       # reports/dashboard_YYYY-MM-DD.html
"""

import datetime as dt
import json
from pathlib import Path

from src import config, universe
from src.analysis import gates, report, scoring

TEMPLATE_PATH = Path(__file__).with_name("dashboard_template.html")
PLACEHOLDER = "/*__PAYLOAD__*/ null"

PAGE_SKELETON = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body>
{body}
</body>
</html>
"""

EST_DECLINE_PCT = abs(gates.EST_DECLINE_TOLERANCE) * 100


def build_payload(conn, date: str) -> dict:
    stocks = universe.load()
    metric_rows = {
        r["ticker"]: r for r in conn.execute(
            "SELECT * FROM derived_metrics WHERE date = ?", (date,)
        )
    }
    score_rows = {
        r["ticker"]: r for r in conn.execute(
            "SELECT * FROM derived_scores WHERE date = ?", (date,)
        )
    }
    if not score_rows:
        raise SystemExit(f"{date} 분석 결과가 없다 — jobs/daily_analyze를 먼저 실행할 것")

    out = []
    for ticker, attrs in sorted(stocks.items()):
        s, m = score_rows.get(ticker), metric_rows.get(ticker)
        if s is None or m is None:
            continue
        components = {
            "밴드": s["band_score"], "동종할인": s["peer_score"],
            "PEG": s["peg_score"], "낙폭": s["drawdown_score"],
        }
        out.append({
            "ticker": ticker,
            "name": attrs["name"],
            "layer": m["layer"],
            "layer_name": report.LAYER_NAMES.get(m["layer"], str(m["layer"])),
            "aux_layers": attrs["aux_layers"],
            "waves": attrs["waves"],
            "purity": attrs["purity"],
            "valuation": m["valuation"],
            "status": s["gate_status"],
            "reason": s["gate_reason"],
            "score": s["score"],
            "rank": s["layer_rank"],
            "confidence": s["confidence"],
            "basis": s["score_basis"],
            "components": components,
            "contributions": _contributions(components, s),
            "price": m["price"],
            "ttm_eps": m["ttm_eps"],
            "per": m["per"],
            "band": m["band_percentile"],
            "band_n": m["band_n"],
            "fwd_eps": m["forward_eps"],
            "forward_per": m["forward_per"],
            "growth": m["growth_pct"],
            "peg": m["peg"],
            "drawdown": m["drawdown"],
            "trend30": m["est_trend_30d"],
            "trend90": m["est_trend_90d"],
            "peer_group": m["peer_group"],
            "peer_median": m["peer_median_per"],
            "peer_discount": m["peer_discount"],
            "layer_band_median": m["layer_band_median"],
        })

    return {
        "asof": date,
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "quality": _quality(conn),
        "weights": scoring.WEIGHTS,
        "purity_confidence": scoring.PURITY_CONFIDENCE,
        "stocks": out,
        "rules": _rules(),
    }


def _contributions(components: dict, score_row) -> dict:
    """스택바 세그먼트: 각 구성요소가 실제로 기여한 점수(합 = 총점).

    결측 구성요소가 있으면 남은 가중치가 재정규화되므로, 명목 가중치가 아니라
    재정규화된 몫으로 나눠야 세그먼트 합이 총점과 맞는다.
    """
    if score_row["score"] is None:
        return {}
    total_weight = sum(
        scoring.WEIGHTS[k] for k, v in components.items() if v is not None
    )
    if total_weight == 0:
        return {}
    return {
        k: 100.0 * (scoring.WEIGHTS[k] / total_weight) * v * score_row["confidence"]
        for k, v in components.items() if v is not None
    }


def _quality(conn) -> dict:
    row = conn.execute("SELECT MAX(run_date) AS d FROM quality_log").fetchone()
    if row["d"] is None:
        return {"run_date": None, "errors": [], "warnings": 0}
    run_date = row["d"]
    rows = conn.execute(
        "SELECT check_name, ticker, detail, severity FROM quality_log WHERE run_date = ?",
        (run_date,),
    ).fetchall()
    return {
        "run_date": run_date,
        "errors": [
            {"check": r["check_name"], "ticker": r["ticker"], "detail": r["detail"]}
            for r in rows if r["severity"] == "error"
        ],
        "warnings": sum(1 for r in rows if r["severity"] == "warning"),
    }


def _rules() -> list[dict]:
    """규칙 카드는 코드 상수에서 만든다 — 화면과 구현이 어긋나지 않도록."""
    return [
        {
            "title": "G0 프리어닝 → 워치리스트",
            "body": f"실적 예상일 D-{gates.PREEARNINGS_WINDOW_DAYS} 이내면 추천에서 뺀다. "
                    "예상일은 EDGAR 공시 간격의 중앙값 근사이며 정식 캘린더로 교체 예정.",
        },
        {
            "title": "G1 밸류트랩 → 하드 탈락",
            "body": f"forward EPS가 30일·90일 중 하나라도 {EST_DECLINE_PCT:.0f}% 넘게 내려가면 탈락. "
                    "감점이 아니라 탈락인 이유는 '추정치 온전'이 미운 오리의 정의이기 때문.",
        },
        {
            "title": "G2 레이어 동반 하단 → 신뢰도 강등",
            "body": f"레이어 밴드 중앙값이 {gates.LAYER_BOTTOM_PERCENTILE:.0f}% 아래면 "
                    f"점수에 ×{scoring.LAYER_DEGRADE}. 전 레이어가 싸면 그건 개별 종목이 싼 게 아니라 베타다.",
        },
        {
            "title": "G3 승자독식 → 탈락",
            "body": f"레이어 1등 성장률의 {gates.LEADER_GROWTH_RATIO:.0%} 미만이면 탈락. "
                    "동종 대비 할인이 할인이 아니라 퇴출 예고인 경우를 거른다.",
        },
        {
            "title": "낙폭 기준선",
            "body": f"{scoring.DRAWDOWN_THRESHOLD:.0%}보다 얕은 낙폭은 0점, "
                    f"{scoring.DRAWDOWN_FULL:.0%}에서 만점. 고정 기준선으로 시작해 백테스트로 튜닝한다.",
        },
        {
            "title": "보류는 통과가 아니다",
            "body": f"추정치 스냅샷이 {gates.MIN_ESTIMATE_HISTORY_DAYS}일 미만이거나 밴드 표본이 "
                    "모자라면 판정을 유보한다. 데이터 부족을 통과로 흘리지 않기 위한 상태다.",
        },
    ]


def render(payload: dict) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        raise RuntimeError("템플릿에서 페이로드 자리를 찾지 못했다")
    # </script> 가 데이터 안에 있으면 스크립트 블록이 조기 종료된다
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return template.replace(PLACEHOLDER, blob)


def write(conn, date: str, standalone: bool = True) -> str:
    body = render(build_payload(conn, date))
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.REPORTS_DIR / f"dashboard_{date}.html"
    path.write_text(
        PAGE_SKELETON.format(body=f"<body>\n{body}\n</body>") if standalone else body,
        encoding="utf-8",
    )
    return str(path)
