"""정합성 리포트 — quality_log 적재 + 일간 markdown 한 장."""

import datetime as dt

from src import config
from src.quality.checks import Finding


def persist(conn, run_date: str, findings: list[Finding]) -> None:
    created_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    conn.execute("DELETE FROM quality_log WHERE run_date = ?", (run_date,))
    conn.executemany(
        "INSERT INTO quality_log (run_date, check_name, severity, ticker, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(run_date, f.check_name, f.severity, f.ticker, f.detail, created_at) for f in findings],
    )
    conn.commit()


def write_markdown(conn, run_date: str, findings: list[Finding], ingest_counts: dict) -> str:
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    gate = "🔴 분석 중단 (오류 존재)" if errors else "🟢 분석 진행 가능"

    lines = [
        f"# 정합성 리포트 — {run_date}",
        "",
        f"**게이트 판정: {gate}** · 오류 {len(errors)}건 · 경고 {len(warnings)}건",
        "",
        "## 수집 현황",
        "",
        "| 원천 | 신규 적재 행 |",
        "|---|---|",
    ]
    for source, n in ingest_counts.items():
        lines.append(f"| {source} | {n} |")

    for title, items in (("## 오류", errors), ("## 경고", warnings)):
        lines += ["", title, ""]
        if not items:
            lines.append("(없음)")
            continue
        lines += ["| 체크 | 티커 | 내용 |", "|---|---|---|"]
        for f in items:
            lines.append(f"| {f.check_name} | {f.ticker or '-'} | {f.detail} |")

    lines += [
        "",
        "---",
        "오류가 1건이라도 있으면 daily_analyze는 실행되지 않는다 (ARCHITECTURE.md 원칙 2).",
        "",
    ]

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.REPORTS_DIR / f"quality_{run_date}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)
