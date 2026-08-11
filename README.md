# Find Your Ugly Duckling

AI 밸류체인에서 "억울하게 싼" 종목(미운 오리)을 찾는 대시보드.

- **[PHILOSOPHY.md](PHILOSOPHY.md)** — 무엇을 왜 찾는가 (투자 철학, 상위 규범)
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — 시스템을 어떻게 쪼개는가 (모듈 설계)
- **[universe/universe.yaml](universe/universe.yaml)** — 유니버스 (분기 1회 수동 리뷰)

## 실행

```bash
pip install -r requirements.txt

# SEC EDGAR는 연락처가 든 User-Agent를 요구한다
export SEC_EDGAR_USER_AGENT="find-your-ugly-duckling (you@example.com)"

python -m jobs.daily_ingest    # 수집 → 정합성 검증 → reports/quality_*.md
python -m jobs.daily_analyze   # 정합성 통과 시에만 분석 → 리포트 + 대시보드
python -m jobs.dashboard       # 대시보드만 다시 렌더 (분석 재실행 없이)
```

대시보드는 `reports/dashboard_YYYY-MM-DD.html` 파일 하나로 떨어진다.
서버 없이 브라우저로 바로 열면 되고, 데이터는 HTML 안에 심겨 있다.

테스트는 네트워크 없이 돈다:

```bash
python -m tests.test_quality    # 정합성 규칙 (위반 7종 검출)
python -m tests.test_analysis   # 게이트·점수·룩어헤드 차단
python -m tests.test_ingest     # 수집 실패 처리 (가짜 응답 주입)
```

DB는 `data/duckling.db`(SQLite, git 제외)에 쌓인다. `raw_*`는 append-only,
`derived_*`는 raw에서 언제든 전량 재계산된다.

## 지금 상태

수집·검증·분석까지 동작한다. 다만 **추정치 스냅샷이 30일 이상 쌓이기 전에는
대부분의 종목이 `보류`로 나오는 것이 정상**이다 — 미운 오리 판정의 핵심인
"이익 추정이 온전한가"를 forward EPS 궤적 없이는 판단할 수 없기 때문이다.
그날까지는 리포트를 지표 대시보드로 읽으면 된다.
