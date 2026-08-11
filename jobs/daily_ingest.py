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
            result = module.ingest(conn)
        except Exception as exc:  # 한 원천의 실패가 나머지 수집·검증을 막으면 안 된다
            counts[name] = 0
            ingest_failures.append(
                checks.Finding("수집실패", "error", None, f"{name}: {type(exc).__name__}: {exc}")
            )
            print(f"[ingest] {name}: 실패 — {exc}")
            continue

        counts[name] = result.inserted
        if result.failures and result.inserted == 0:
            # 몇 종목이 빠진 것과 원천이 통째로 죽은 것은 다른 사건이다.
            # 종목별 경고에 묻히지 않도록 원인을 한 줄로 올린다.
            ingest_failures.append(checks.Finding(
                "수집실패", "error", None,
                f"{name}: 전 종목 실패({len(result.failures)}건) — 원천 장애 또는 네트워크 차단. "
                f"예: {result.failures[0][0]} → {result.failures[0][1]}",
            ))
        # 종목 단위 실패는 경고로 남긴다. 데이터가 실제로 비면 커버리지·신선도
        # 체크가 오류로 올려주므로, 여기서 이중으로 막지 않는다.
        for ticker, reason in result.failures:
            ingest_failures.append(
                checks.Finding("수집실패", "warning", ticker, f"{name}: {reason}")
            )
        note = f" (종목 실패 {len(result.failures)}건)" if result.failures else ""
        print(f"[ingest] {name}: {result.inserted}행 적재{note}")

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
