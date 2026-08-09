"""일간 수집 잡: ingest 3종 → 정합성 검증 → 리포트.

여기까지가 수집 파이프라인의 전부다. 분석은 daily_analyze가 별도로 수행한다.
종료 코드: 정합성 오류가 있으면 1 (스케줄러에서 실패로 보이게).
"""

import datetime as dt
import sys

from src.ingest import estimates, fundamentals, prices
from src.quality import checks, report
from src.storage import db


def main() -> int:
    run_date = dt.datetime.now(dt.timezone.utc).date().isoformat()
    conn = db.connect()

    counts = {}
    ingest_failures = []
    for name, module in (("prices", prices), ("fundamentals", fundamentals), ("estimates", estimates)):
        print(f"[ingest] {name} 수집 중...")
        try:
            counts[name] = module.ingest(conn)
            print(f"[ingest] {name}: {counts[name]}행 적재")
        except Exception as exc:  # 한 원천의 실패가 나머지 수집·검증을 막으면 안 된다
            counts[name] = 0
            ingest_failures.append(
                checks.Finding("수집실패", "error", None, f"{name}: {type(exc).__name__}: {exc}")
            )
            print(f"[ingest] {name}: 실패 — {exc}")

    print("[quality] 정합성 검증 중...")
    findings = ingest_failures + checks.run_all(conn, run_date)
    report.persist(conn, run_date, findings)
    path = report.write_markdown(conn, run_date, findings, counts)

    errors = sum(1 for f in findings if f.severity == "error")
    warnings = sum(1 for f in findings if f.severity == "warning")
    print(f"[quality] 오류 {errors}건, 경고 {warnings}건 → 리포트: {path}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
