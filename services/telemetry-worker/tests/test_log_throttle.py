"""ThrottledWarnings: first occurrence per key at WARNING, then one summary per window."""

import logging
import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
_SERVICES_DIR = os.path.normpath(os.path.join(_SVC_DIR, ".."))
for _p in [_SVC_DIR, _SERVICES_DIR]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from telemetry_worker.log_throttle import WINDOW_S, ThrottledWarnings

LOGGER = logging.getLogger("test.log_throttle")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _lines(caplog):
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == LOGGER.name]


def test_default_window_is_ten_minutes():
    assert WINDOW_S == 600.0


def test_first_occurrence_logs_then_suppresses(caplog):
    clock = Clock()
    tw = ThrottledWarnings(LOGGER, clock=clock)
    caplog.set_level(logging.WARNING, logger=LOGGER.name)
    key = ("t1", "urn:dev:1", "no_deployment")
    assert tw.warning(key, "skip tenant_id=%s at=%s", "t1", "a") is True
    for i in range(5):
        clock.t += 10
        assert tw.warning(key, "skip tenant_id=%s at=%s", "t1", f"b{i}") is False
    assert _lines(caplog) == [(logging.WARNING, "skip tenant_id=t1 at=a")]


def test_one_summary_per_window_with_the_suppressed_count(caplog):
    clock = Clock()
    tw = ThrottledWarnings(LOGGER, window_s=600, clock=clock)
    caplog.set_level(logging.WARNING, logger=LOGGER.name)
    key = ("t1", "urn:dev:1", "no_deployment")
    tw.warning(key, "skip at=%s", "0")
    for i in range(3):
        clock.t += 100
        tw.warning(key, "skip at=%s", str(i))
    clock.t += 400  # 700 s after the first line
    assert tw.warning(key, "skip at=%s", "late") is True
    lines = _lines(caplog)
    assert len(lines) == 2
    assert lines[1] == (logging.WARNING, "skip at=late (3 similar suppressed in the last 600 s)")
    # The window restarts at the summary line.
    clock.t += 599
    assert tw.warning(key, "skip at=%s", "x") is False


def test_after_a_quiet_window_the_next_line_has_no_suffix(caplog):
    clock = Clock()
    tw = ThrottledWarnings(LOGGER, clock=clock)
    caplog.set_level(logging.WARNING, logger=LOGGER.name)
    key = ("t1", "d", "r")
    tw.warning(key, "m1")
    clock.t += 601
    assert tw.warning(key, "m2") is True
    assert [m for _, m in _lines(caplog)] == ["m1", "m2"]


def test_keys_are_independent(caplog):
    clock = Clock()
    tw = ThrottledWarnings(LOGGER, clock=clock)
    caplog.set_level(logging.WARNING, logger=LOGGER.name)
    assert tw.warning(("t1", "d1", "r"), "a")
    assert tw.warning(("t2", "d1", "r"), "b")
    assert tw.warning(("t1", "d2", "r"), "c")
    assert tw.warning(("t1", "d1", "other"), "d")
    assert not tw.warning(("t1", "d1", "r"), "a again")
    assert [m for _, m in _lines(caplog)] == ["a", "b", "c", "d"]


def test_flush_emits_pending_counts_with_the_last_context(caplog):
    clock = Clock()
    tw = ThrottledWarnings(LOGGER, clock=clock)
    caplog.set_level(logging.WARNING, logger=LOGGER.name)
    tw.warning(("t1", "d1", "r"), "first %s", "1")
    tw.warning(("t1", "d1", "r"), "later %s", "2")
    tw.warning(("t1", "d1", "r"), "later %s", "3")
    tw.warning(("t1", "d2", "r"), "only")
    assert tw.flush() == 1
    assert _lines(caplog)[-1] == (
        logging.WARNING, "later 3 (2 similar suppressed in the last 600 s)")
    assert tw.flush() == 0  # nothing left to report


def test_reset_forgets_every_key(caplog):
    tw = ThrottledWarnings(LOGGER, clock=Clock())
    caplog.set_level(logging.WARNING, logger=LOGGER.name)
    tw.warning("k", "a")
    tw.reset()
    assert tw.warning("k", "a") is True


def test_key_count_is_bounded_and_eviction_keeps_the_count(caplog):
    clock = Clock()
    tw = ThrottledWarnings(LOGGER, clock=clock, max_keys=2)
    caplog.set_level(logging.WARNING, logger=LOGGER.name)
    tw.warning("k1", "a")
    tw.warning("k1", "a2")  # suppressed, pending
    tw.warning("k2", "b")
    tw.warning("k3", "c")   # evicts k1, which reports its pending count
    assert len(tw) == 2
    assert (logging.WARNING, "a2 (1 similar suppressed in the last 600 s)") in _lines(caplog)


def test_preformatted_message_with_percent_is_safe(caplog):
    clock = Clock()
    tw = ThrottledWarnings(LOGGER, clock=clock)
    caplog.set_level(logging.WARNING, logger=LOGGER.name)
    tw.warning("k", "battery 50% low")
    tw.warning("k", "battery 40% low")
    clock.t += 601
    tw.warning("k", "battery 30% low")
    assert [m for _, m in _lines(caplog)] == [
        "battery 50% low", "battery 30% low (1 similar suppressed in the last 600 s)"]
