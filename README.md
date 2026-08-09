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
python -m jobs.daily_analyze   # 정합성 통과 시에만 분석 (2차 마일스톤)
```

DB는 `data/duckling.db`(SQLite, git 제외)에 쌓인다. raw 테이블은 append-only.
