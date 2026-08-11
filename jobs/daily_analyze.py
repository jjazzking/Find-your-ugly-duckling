"""일간 분석 잡: 정합성 게이트를 통과한 날만 실행된다.

수집은 daily_ingest가 담당한다. 이 잡은 raw를 읽기만 하고, 네트워크를 쓰지 않는다.
파이프라인: 정합성 확인 → metrics → gates → scoring → 리포트.
종료 코드: 정합성 오류로 분석을 못 하면 1.
"""

import sys

from src.analysis import dashboard, gates, metrics, report, scoring
from src.storage import db


def latest_trading_date(conn) -> str | None:
    row = conn.execute("SELECT MAX(date) AS d FROM raw_prices").fetchone()
    return row["d"]


def check_gate(conn) -> tuple[bool, str | None]:
    row = conn.execute("SELECT MAX(run_date) AS d FROM quality_log").fetchone()
    if row["d"] is None:
        print("[gate] 정합성 검증 기록이 없음 — daily_ingest를 먼저 실행할 것")
        return False, None
    run_date = row["d"]
    errors = conn.execute(
        "SELECT check_name, ticker, detail FROM quality_log "
        "WHERE run_date = ? AND severity = 'error'",
        (run_date,),
    ).fetchall()
    if errors:
        print(f"[gate] 🔴 {run_date} 정합성 오류 {len(errors)}건 — 분석 중단:")
        for e in errors[:10]:
            print(f"  - [{e['check_name']}] {e['ticker'] or '-'}: {e['detail']}")
        return False, run_date
    print(f"[gate] 🟢 {run_date} 정합성 통과 — 분석 진행")
    return True, run_date


def main() -> int:
    conn = db.connect()
    ok, _ = check_gate(conn)
    if not ok:
        return 1

    asof = latest_trading_date(conn)
    if asof is None:
        print("[analysis] 가격 데이터가 없어 분석할 수 없음")
        return 1

    print(f"[analysis] 기준일 {asof} — 지표 계산 중...")
    ms = metrics.compute(conn, asof)
    metrics.persist(conn, asof, ms)

    gate_results = gates.evaluate(conn, ms, asof)
    scores = scoring.compute(ms, gate_results)
    scoring.persist(conn, asof, scores, gate_results)

    tally: dict[str, int] = {}
    for g in gate_results.values():
        tally[g.status] = tally.get(g.status, 0) + 1
    print("[analysis] 게이트: " + ", ".join(f"{k} {v}" for k, v in sorted(tally.items())))

    print(f"[analysis] 리포트: {report.write_markdown(conn, asof, ms, gate_results, scores)}")
    print(f"[analysis] 대시보드: {dashboard.write(conn, asof)}")

    top = sorted(
        (s for s in scores.values() if s.score is not None),
        key=lambda s: -s.score,
    )[:5]
    if top:
        print("[analysis] 레이어별 1위 후보 (레이어 간 비교 아님):")
        for s in top:
            print(f"  - L{s.layer} {s.ticker}: {s.score:.1f}점 (레이어 내 {s.layer_rank}위)")
    else:
        print("[analysis] 채점된 종목 없음 — 리포트의 '보류' 사유를 확인할 것")
    return 0


if __name__ == "__main__":
    sys.exit(main())
