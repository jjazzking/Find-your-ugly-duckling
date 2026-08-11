"""수집 모듈 공통 반환형.

한 종목의 실패가 나머지 종목의 수집을 막으면 안 된다. 각 ingest는 종목 단위로
예외를 흡수하고, 무엇이 왜 실패했는지를 failures에 담아 돌려준다.
호출자(jobs/daily_ingest)가 이를 정합성 findings로 바꾼다.
"""

from dataclasses import dataclass, field


@dataclass
class Result:
    inserted: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)  # (ticker, 사유)

    def fail(self, ticker: str, exc: BaseException) -> None:
        detail = str(exc).replace("\n", " ")[:200]
        self.failures.append((ticker, f"{type(exc).__name__}: {detail}"))
