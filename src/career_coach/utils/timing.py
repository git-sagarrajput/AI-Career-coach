from __future__ import annotations

import time
from types import TracebackType

from career_coach.models.analysis import StageTiming


class Stopwatch:
    """Context manager that appends a StageTiming to `sink` on exit."""

    def __init__(self, stage: str, sink: list[StageTiming]) -> None:
        self.stage = stage
        self.sink = sink
        self.status: str = "ok"
        self._start = 0.0

    def __enter__(self) -> Stopwatch:
        self._start = time.perf_counter()
        return self

    def skip(self) -> None:
        self.status = "skipped"

    def fail(self) -> None:
        self.status = "failed"

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if exc_type is not None:
            self.status = "failed"
        self.sink.append(
            StageTiming(stage=self.stage, duration_ms=elapsed_ms(self._start), status=self.status)
        )


def elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
