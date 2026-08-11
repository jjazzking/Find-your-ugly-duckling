"""대시보드만 다시 만든다 (분석을 재실행하지 않고 derived 테이블에서 렌더).

  python -m jobs.dashboard              # 최신 분석일
  python -m jobs.dashboard 2026-08-07   # 특정 날짜
"""

import sys

from src.analysis import dashboard
from src.storage import db


def main(argv: list[str]) -> int:
    conn = db.connect()
    if len(argv) > 1:
        date = argv[1]
    else:
        row = conn.execute("SELECT MAX(date) AS d FROM derived_scores").fetchone()
        if row["d"] is None:
            print("[dashboard] 분석 결과가 없다 — jobs/daily_analyze를 먼저 실행할 것")
            return 1
        date = row["d"]
    print(f"[dashboard] {dashboard.write(conn, date)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
