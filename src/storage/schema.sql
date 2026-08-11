-- 스토리지 계약 (ARCHITECTURE.md §2)
-- raw_* 테이블은 append-only: UPDATE/DELETE 금지, INSERT OR IGNORE만 사용한다.
-- derived_* 테이블은 raw에서 언제든 전량 재계산 가능하다: 해당 날짜를 지우고 다시 쓴다.

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

-- ── 파생 테이블 (analysis) ───────────────────────────────────
-- raw만 있으면 전부 재생성된다. 여기 값을 손으로 고치지 말 것.

CREATE TABLE IF NOT EXISTS derived_metrics (
  ticker            TEXT NOT NULL,
  date              TEXT NOT NULL,
  price             REAL,                    -- 조정 종가
  ttm_eps           REAL,                    -- 공시일 컷오프 적용
  per               REAL,
  band_percentile   REAL,                    -- 자기 과거 PER 분포에서의 위치 (낮을수록 쌈)
  band_n            INTEGER,                 -- 밴드 표본 수 (부족하면 백분위 NULL)
  forward_eps       REAL,
  forward_per       REAL,
  growth_pct        REAL,                    -- (forward_eps/ttm_eps - 1) * 100
  peg               REAL,
  drawdown          REAL,                    -- 52주 고점 대비 (음수)
  est_trend_30d     REAL,                    -- forward EPS 30일 변화율
  est_trend_90d     REAL,
  peer_group        TEXT,                    -- 동종 비교 그룹 식별자
  peer_median_per   REAL,
  peer_discount     REAL,                    -- (동종중앙값 - 자기PER) / 동종중앙값
  layer             INTEGER NOT NULL,
  layer_band_median REAL,                    -- 레이어 동반 하단 감시용
  valuation         TEXT NOT NULL,           -- 적용 잣대
  computed_at       TEXT NOT NULL,
  PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS derived_scores (
  ticker          TEXT NOT NULL,
  date            TEXT NOT NULL,
  gate_status     TEXT NOT NULL,             -- 통과 / 탈락 / 보류 / 워치리스트 / 잣대미구현
  gate_reason     TEXT,
  score           REAL,                      -- 0~100, 게이트 통과자만
  band_score      REAL,
  peer_score      REAL,
  peg_score       REAL,
  drawdown_score  REAL,
  score_basis     TEXT,                      -- 실제로 쓰인 구성요소 (결측분 재정규화)
  confidence      REAL,                      -- 순도 × 레이어 강등
  layer           INTEGER NOT NULL,
  layer_rank      INTEGER,                   -- 레이어 내부 순위 (전역 순위는 만들지 않는다)
  computed_at     TEXT NOT NULL,
  PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_scores_date ON derived_scores (date, layer, layer_rank);
