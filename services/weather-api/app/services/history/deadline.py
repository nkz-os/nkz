"""Request deadline shared by every archive call of one history request."""
import time
from typing import Callable, Tuple

CONNECT_TIMEOUT_S = 3
MIN_READ_TIMEOUT_S = 1
INITIAL_ESTIMATE_S = 4.0       # one archive variant fetch, until one has been measured
COLD_FACTOR = 2                # a cold year fetches up to two variants


class DeadlineExceeded(Exception):
    """A cold year was not started: the time left cannot cover it."""


class Deadline:
    def __init__(self, total_s: float, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._end = clock() + total_s
        self.estimate = INITIAL_ESTIMATE_S

    def remaining(self) -> float:
        return self._end - self._clock()

    def timeout(self) -> Tuple[float, float]:
        """requests (connect, read) timeout for the next fetch."""
        return (CONNECT_TIMEOUT_S, max(MIN_READ_TIMEOUT_S, self.remaining() / 2))

    def can_start_cold(self) -> bool:
        return self.remaining() >= COLD_FACTOR * self.estimate

    def record(self, seconds: float) -> None:
        """Last measured duration of one variant fetch."""
        self.estimate = seconds

    def now(self) -> float:
        return self._clock()
