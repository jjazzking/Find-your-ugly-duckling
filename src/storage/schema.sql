-- 스토리지 계약 (ARCHITECTURE.md §2)
-- raw_* 테이블은 append-only: UPDATE/DELETE 금지, INSERT OR IGNORE만 사용한다.
-- derived_* 테이블은 2차 마일스톤(analysis)에서 추가한다.

CREATE TABLE IF NOT EXISTS raw_prices (
  ticker       TEXT NOT NULL,
  date         TEXT NOT NULL,              -- YYYY-MM-DD (거래일)
  open         REAL,
  high         REAL,
  low          REAL,
  close        REAL,                       -- 미조정 종가
  adj_close    REAL,                       -- 분할·배당 조정 종가
  volume       INTEGER,
  collected_at TEXT NOT NULL,              -- UTC ISO8601
  PRIMARY KEY (ticker, date)
);

-- SEC EDGAR companyfacts의 XBRL 사실(fact)을 원형 그대로 적재한다.
-- filed_date가 룩어헤드 방지의 기준: 분석은 "filed_date 이후"에만 이 값을 알 수 있다.
CREATE TABLE IF NOT EXISTS raw_fundamentals (
  ticker        TEXT NOT NULL,
  concept       TEXT NOT NULL,             -- 예: EarningsPerShareDiluted
  unit          TEXT NOT NULL,             -- 예: USD/shares
  start_date    TEXT NOT NULL DEFAULT '',  -- 기간 시작 (instant 항목은 '')
  end_date      TEXT NOT NULL,
  fiscal_year   INTEGER,
  fiscal_period TEXT,                      -- FY / Q1..Q4
  form          TEXT,                      -- 10-K / 10-Q / 20-F / 6-K ...
  filed_date    TEXT NOT NULL,             -- SEC 공시일
  value         REAL NOT NULL,
  source        TEXT NOT NULL DEFAULT 'edgar',
  collected_at  TEXT NOT NULL,
  PRIMARY KEY (ticker, concept, unit, start_date, end_date, form, filed_date)
);

-- forward 추정치는 히스토리를 살 수 없으므로 1일차부터 매일 스냅샷으로 축적한다 (철학 §0).
CREATE TABLE IF NOT EXISTS raw_estimates (
  ticker        TEXT NOT NULL,
  snapshot_date TEXT NOT NULL,             -- YYYY-MM-DD (UTC)
  forward_eps   REAL,
  forward_pe    REAL,
  trailing_eps  REAL,
  num_analysts  INTEGER,
  source        TEXT NOT NULL DEFAULT 'yfinance',
  collected_at  TEXT NOT NULL,
  PRIMARY KEY (ticker, snapshot_date)
);

CREATE TABLE IF NOT EXISTS quality_log (
  run_date   TEXT NOT NULL,                -- YYYY-MM-DD
  check_name TEXT NOT NULL,
  severity   TEXT NOT NULL CHECK (severity IN ('error', 'warning')),
  ticker     TEXT,                         -- 전역 이슈는 NULL
  detail     TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quality_run ON quality_log (run_date, severity);
