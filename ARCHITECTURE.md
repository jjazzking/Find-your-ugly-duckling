# Find Your Ugly Duckling — 모듈 아키텍처 v1.0

> PHILOSOPHY.md가 "무엇을 왜 찾는가"라면, 이 문서는 "시스템을 어떻게 쪼개는가"다.
> 핵심 요구사항: **수집 파이프라인과 분석 파이프라인의 완전한 분리**, 그리고
> 그 사이에 **데이터 정합성 검증을 1급 관문**으로 세우는 것.

## 0. 핵심 원칙

### 원칙 1 — 수집과 분석은 DB를 사이에 두고만 대화한다

- 수집(ingest)은 분석의 존재를 모른다. 원천에서 받아 raw 테이블에 쓰는 것까지가 전부.
- 분석(analysis)은 데이터 원천을 모른다. DB만 읽는다. yfinance/EDGAR를 직접 호출하지 않는다.
- 둘의 계약은 함수 호출이 아니라 **DB 스키마**다.

### 원칙 2 — 정합성 게이트가 관문이다

```
[ingest 수집] → raw 테이블 → [quality 정합성 검증] → 통과 → [analysis 분석] → derived 테이블
                                      ↓ 실패(오류 등급)
                              그날 분석 중단 + 리포트
```

정합성 검증에서 **오류 등급이 하나라도 나오면 그날 analysis는 돌지 않는다.**
경고 등급은 리포트에 남기고 진행한다.

### 원칙 3 — raw는 append-only, derived는 재계산 가능

- raw 테이블은 절대 덮어쓰지 않는다. 수집 시각(`collected_at`)을 함께 기록해
  "그날 내가 무엇을 알고 있었나"를 재현할 수 있어야 한다 → 백테스트 정직성의 토대.
- derived 테이블은 언제든 raw에서 전부 재계산 가능해야 한다. 분석 버그 발견 시
  raw는 그대로 두고 derived만 다시 만든다.

---

## 1. 디렉토리 구성

```
find-your-ugly-duckling/
├── PHILOSOPHY.md
├── ARCHITECTURE.md
├── universe/
│   └── universe.yaml        # 철학 §5 스키마: 티커 → 레이어/파도/순도/잣대 (수동 큐레이션)
├── src/
│   ├── ingest/              # 수집 — DB에 raw만 쓴다
│   │   ├── prices.py        #   yfinance 일간 가격 (adjusted + unadjusted)
│   │   ├── fundamentals.py  #   SEC EDGAR companyfacts → 분기 EPS (공시일 필수 기록)
│   │   └── estimates.py     #   forward EPS 스냅샷 (매일 축적, 1일차부터)
│   ├── quality/             # 정합성 — raw를 읽고 판정만 한다
│   │   ├── checks.py        #   검증 규칙 (아래 §3)
│   │   └── report.py        #   일간 정합성 리포트 (markdown)
│   ├── analysis/            # 분석 — 검증 통과한 raw만 읽는다 (2차 마일스톤)
│   │   ├── metrics.py       #   TTM, PER/FFO/EV·S, 자기 밴드 백분위
│   │   ├── gates.py         #   G0~G3 게이트
│   │   ├── scoring.py       #   미운 오리 점수
│   │   └── backtest/
│   └── storage/
│       ├── schema.sql       # SQLite 스키마
│       └── db.py            # 커넥션/마이그레이션 헬퍼
└── jobs/
    ├── daily_ingest.py      # 수집 → 정합성 검증까지만
    └── daily_analyze.py     # 정합성 통과 확인 후 분석 실행
```

잡(jobs)이 둘로 나뉘어 있는 것 자체가 파이프라인 분리의 실행형이다.
스케줄링은 GitHub Actions cron(장 마감 후) 기본, 로컬 수동 실행도 동일 진입점 사용.

---

## 2. 스토리지 계약 (SQLite)

| 테이블 | 성격 | 주요 컬럼 |
|---|---|---|
| `raw_prices` | append-only | ticker, date, open/high/low/close, adj_close, volume, collected_at |
| `raw_fundamentals` | append-only | ticker, fiscal_quarter, eps, revenue, **filed_date(공시일)**, source, collected_at |
| `raw_estimates` | append-only 스냅샷 | ticker, snapshot_date, fwd_eps_curr_yr, fwd_eps_next_yr, source, collected_at |
| `quality_log` | append-only | run_date, check_name, severity(오류/경고), ticker, detail |
| `derived_metrics` | 재계산 가능 | ticker, date, ttm_eps, per, band_percentile, ... (2차) |
| `derived_scores` | 재계산 가능 | ticker, date, gate_status, score, layer_rank (2차) |

`universe.yaml`은 DB 밖의 수동 큐레이션 파일로 유지한다(분기 리뷰가 git diff로 남도록).

---

## 3. 정합성 검증 규칙 (quality/checks.py)

| # | 검증 | 내용 | 등급 |
|---|---|---|---|
| 1 | 크로스소스 대조 | 같은 분기 EPS를 yfinance vs EDGAR 비교, 허용 오차 초과 시 | 오류 |
| 2 | 시계열 무결성 | 거래일인데 가격 결측 / 전일 대비 ±30% 급변(분할 미조정 의심) | 오류 |
| 3 | 조정 일관성 | adjusted 가격 기준 통일 확인 (분할·배당 반영) | 오류 |
| 4 | 공시일 정합성 | EPS 적용 시작일 ≥ SEC 공시일 (룩어헤드 방지) | 오류 |
| 5 | TTM 검증 | 4개 분기 합 vs 연간 공시값 대조 | 경고 |
| 6 | 유니버스 커버리지 | universe.yaml 모든 티커에 데이터 존재 여부 (ADR·리츠 구멍 주의) | 경고 |
| 7 | 신선도 | 최신 수집일 = 최신 거래일 | 오류 |

- 결과는 `quality_log`에 적재 + 일간 markdown 리포트 한 장 생성.
- **오류 1건 이상 → 그날 `daily_analyze` 실행 안 함.**

---

## 4. 마일스톤

| 차수 | 범위 | 완료 기준 |
|---|---|---|
| 1차 ✅ | universe.yaml 초안 + storage 스키마 + ingest 3종 + quality 검증·리포트 | 수 주간 매일 돌려 정합성 리포트가 깨끗하게 유지됨 |
| 2차 ✅ | analysis (metrics → gates → scoring) + 일간 미운 오리 리포트 | 철학 §6 판정 규칙 확정 후 구현 완료 |
| **3차 (다음)** | backtest (Phase 1: trailing PER 밴드, EDGAR 공시일 적용) | 가중치 튜닝 근거 확보 |
| 4차 | UI/대시보드 | 별도 논의 |

### 분석 3층 (2차 산출물)

```
raw (검증 통과) → metrics.py  지표: PER·자기 밴드 백분위·동종 할인·PEG·낙폭·추정치 궤적
                → gates.py    G0 프리어닝 / G1 밸류트랩 / G2 레이어 하단 / G3 승자독식
                → scoring.py  가중 점수 × 신뢰도 → **레이어 내부 랭킹**
                → report.py   reports/duckling_YYYY-MM-DD.md
```

판정은 5가지다: 통과 / 탈락 / 워치리스트 / **보류** / 잣대미구현.
`보류`가 따로 있는 이유는 "판정에 필요한 데이터가 없음"을 절대 `통과`로 흘리지 않기
위해서다. 추정치 스냅샷이 30일 이상 쌓이기 전에는 G1을 판정할 수 없고, 그동안
대부분의 종목이 `보류`로 남는 것이 정상 동작이다.

---

## 5. 오픈 항목

- FMP free tier의 역할 확정 (estimates 원천 vs 크로스체크용)
- quality 오류 등급의 허용 오차 수치 (크로스소스 EPS 오차 범위 등) → 실데이터 보고 튜닝
- GitHub Actions cron 시각 (장 마감 + 데이터 반영 지연 고려) — DB 보존 위치 결정이 선행
- **FFO배수·EV/Sales 잣대 미구현**: ②(EQIX·DLR)와 ⑧(PLTR·NOW·APP·DUOL)·MBLY는
  현재 `잣대미구현`으로 분류만 된다. PER을 대신 적용하지 않는다 (철학 §2에서 명시적으로 금지).
  구현하려면 ingest에 D&A(FFO)·매출·발행주식수·순부채(EV) 개념 수집이 먼저 필요하다.
- 실적 예상일: 지금은 EDGAR 공시일 간격의 중앙값으로 근사한다. 정식 캘린더 원천으로 교체 예정.
