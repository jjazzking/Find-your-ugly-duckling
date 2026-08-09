import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DB_PATH = Path(os.environ.get("DUCKLING_DB", ROOT / "data" / "duckling.db"))
UNIVERSE_PATH = ROOT / "universe" / "universe.yaml"
REPORTS_DIR = ROOT / "reports"

# SEC은 요청자 식별 가능한 User-Agent를 요구한다. 실행 환경에서 연락처를 넣을 것:
#   export SEC_EDGAR_USER_AGENT="find-your-ugly-duckling (you@example.com)"
SEC_USER_AGENT = os.environ.get(
    "SEC_EDGAR_USER_AGENT",
    "find-your-ugly-duckling (contact: set SEC_EDGAR_USER_AGENT env)",
)

# 시장 기준 티커: 거래일 캘린더의 기준 (유니버스 밖, 수집만 한다)
BENCHMARK_TICKER = "SPY"
