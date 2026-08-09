"""SEC EDGAR companyfacts 수집 → raw_fundamentals.

- XBRL 사실(fact)을 가공 없이 원형 적재한다. TTM 계산·해석은 quality/analysis의 몫.
- filed_date(공시일)를 반드시 저장한다 — 룩어헤드 방지의 기준값.
- SEC 요청 예절: 식별 가능한 User-Agent + 요청 간 대기.
"""

import datetime as dt
import time

import requests

from src import config, universe

TICKER_CIK_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
REQUEST_INTERVAL_SEC = 0.2

# 원형 적재하되, 지금 필요한 개념만 추린다 (전체 companyfacts는 수천 개 fact).
CONCEPTS = {
    # US-GAAP
    "EarningsPerShareDiluted",
    "EarningsPerShareBasic",
    "NetIncomeLoss",
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    # IFRS (ADR: TSM, ASML 등 20-F 제출사)
    "DilutedEarningsLossPerShare",
    "BasicEarningsLossPerShare",
    "ProfitLoss",
    "Revenue",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": config.SEC_USER_AGENT})
    return s


def _cik_map(session: requests.Session) -> dict[str, int]:
    resp = session.get(TICKER_CIK_URL, timeout=30)
    resp.raise_for_status()
    return {v["ticker"].upper(): int(v["cik_str"]) for v in resp.json().values()}


def ingest(conn, tickers: list[str] | None = None) -> int:
    tickers = tickers or universe.tickers()
    collected_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    session = _session()
    cik_map = _cik_map(session)
    inserted = 0
    for ticker in tickers:
        cik = cik_map.get(ticker.upper())
        if cik is None:
            continue  # 커버리지 체크(quality #6)가 잡는다
        time.sleep(REQUEST_INTERVAL_SEC)
        resp = session.get(COMPANYFACTS_URL.format(cik=cik), timeout=60)
        if resp.status_code == 404:
            continue
        resp.raise_for_status()
        facts = resp.json().get("facts", {})
        for taxonomy in ("us-gaap", "ifrs-full"):
            for concept, body in facts.get(taxonomy, {}).items():
                if concept not in CONCEPTS:
                    continue
                for unit, entries in body.get("units", {}).items():
                    for e in entries:
                        if e.get("val") is None or not e.get("filed") or not e.get("end"):
                            continue
                        cur = conn.execute(
                            "INSERT OR IGNORE INTO raw_fundamentals "
                            "(ticker, concept, unit, start_date, end_date, fiscal_year, "
                            " fiscal_period, form, filed_date, value, source, collected_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'edgar', ?)",
                            (
                                ticker,
                                concept,
                                unit,
                                e.get("start") or "",
                                e["end"],
                                e.get("fy"),
                                e.get("fp"),
                                e.get("form"),
                                e["filed"],
                                float(e["val"]),
                                collected_at,
                            ),
                        )
                        inserted += cur.rowcount
        conn.commit()
    return inserted
