import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DB_PATH = Path(os.environ.get("DUCKLING_DB", ROOT / "data" / "duckling.db"))
UNIVERSE_PATH = ROOT / "universe" / "universe.yaml"
REPORTS_DIR = ROOT / "reports"

# SEC은 요청자 식별 가능한 User-Agent를 요구한다. 실행 환경에서 연락처를 넣을 것:
#   export SEC_EDGAR_USER_AGENT="find-your-ugly-duckling (you@example.com)"
# 미설정 시 SEC은 403으로 거절한다. 원인을 알기 어려운 403 대신 명시적으로 실패시킨다.
SEC_USER_AGENT = os.environ.get("SEC_EDGAR_USER_AGENT", "")
SEC_USER_AGENT_HINT = (
    'SEC_EDGAR_USER_AGENT가 설정되지 않았다. SEC은 연락처가 든 User-Agent를 요구한다:\n'
    '  export SEC_EDGAR_USER_AGENT="find-your-ugly-duckling (your@email.com)"'
)

# 시장 기준 티커: 거래일 캘린더의 기준 (유니버스 밖, 수집만 한다)
BENCHMARK_TICKER = "SPY"
