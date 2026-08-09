"""일간 분석 잡: 정합성 게이트를 통과한 날만 실행된다.

analysis 모듈은 2차 마일스톤 — 지금은 게이트 판정까지만 구현되어 있다.
"""

import sys

from src.storage import db


def main() -> int:
    conn = db.connect()
    row = conn.execute("SELECT MAX(run_date) AS d FROM quality_log").fetchone()
    if row["d"] is None:
        print("[gate] 정합성 검증 기록이 없음 — daily_ingest를 먼저 실행할 것")
        return 1
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
        return 1

    print(f"[gate] 🟢 {run_date} 정합성 통과 — 분석 진행 가능")
    print("[analysis] 2차 마일스톤 미구현 (metrics → gates → scoring)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
