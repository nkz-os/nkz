"""Rate-limited WARNING lines for conditions that repeat on every reading.

Most devices on the platform have no deployment, so "observation skipped" would
otherwise be logged for every reading of every such device. Per key (tenant,
device, reason) the first occurrence is logged at WARNING with its full context;
later ones are counted, and at most one line per window reports them, carrying
the context of the latest occurrence and the suppressed count. `flush` reports
pending counts on demand (end of a recompute run). No dependency; the clock is
injectable for tests.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Hashable, Tuple

WINDOW_S = 600.0
MAX_KEYS = 10_000

_SUMMARY = "%s (%d similar suppressed in the last %d s)"


class _State:
    __slots__ = ("emitted_at", "suppressed", "msg", "args")

    def __init__(self, emitted_at: float):
        self.emitted_at = emitted_at
        self.suppressed = 0
        self.msg = ""
        self.args: Tuple[Any, ...] = ()


class ThrottledWarnings:
    def __init__(
        self,
        logger: logging.Logger,
        *,
        window_s: float = WINDOW_S,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = MAX_KEYS,
    ):
        if window_s <= 0 or max_keys <= 0:
            raise ValueError("window_s and max_keys must be positive")
        self._logger = logger
        self._window_s = window_s
        self._clock = clock
        self._max_keys = max_keys
        self._states: Dict[Hashable, _State] = {}

    def __len__(self) -> int:
        return len(self._states)

    def warning(self, key: Hashable, msg: str, *args: Any) -> bool:
        """Log `msg % args` unless `key` already logged within the window. True when logged."""
        now = self._clock()
        state = self._states.get(key)
        if state is None:
            self._evict_if_full()
            self._states[key] = _State(now)
            self._logger.warning(msg, *args)
            return True
        if now - state.emitted_at < self._window_s:
            state.suppressed += 1
            state.msg, state.args = msg, args
            return False
        suppressed = state.suppressed
        state.emitted_at, state.suppressed = now, 0
        if suppressed:
            self._logger.warning(_SUMMARY, _text(msg, args), suppressed, int(self._window_s))
        else:
            self._logger.warning(msg, *args)
        return True

    def flush(self) -> int:
        """Report every pending suppressed count now. Returns the lines logged."""
        now = self._clock()
        logged = 0
        for state in self._states.values():
            if state.suppressed:
                self._emit_summary(state)
                state.emitted_at, state.suppressed = now, 0
                logged += 1
        return logged

    def reset(self) -> None:
        self._states.clear()

    def _emit_summary(self, state: _State) -> None:
        self._logger.warning(
            _SUMMARY, _text(state.msg, state.args), state.suppressed, int(self._window_s)
        )

    def _evict_if_full(self) -> None:
        """Drop the oldest key (reporting its pending count) to bound memory."""
        while len(self._states) >= self._max_keys:
            state = self._states.pop(next(iter(self._states)))
            if state.suppressed:
                self._emit_summary(state)


def _text(msg: str, args: Tuple[Any, ...]) -> str:
    """The message as logging would render it; a literal '%' in a preformatted one is safe."""
    return msg % args if args else msg
