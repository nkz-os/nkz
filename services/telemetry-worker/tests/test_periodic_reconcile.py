"""The reconcile loop: one early extra pass, then the regular interval; survives failures."""
import asyncio
import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from telemetry_worker import subscription_manager as sm  # noqa: E402


def test_defaults():
    assert sm.FIRST_EXTRA_PASS_DELAY_S == 240
    assert sm.RECONCILE_INTERVAL_S == 3600
    assert sm.FIRST_EXTRA_PASS_DELAY_S > sm.RETIRE_MIN_AGE_S


def test_extra_pass_then_regular_interval_and_failures_do_not_stop_it():
    async def scenario():
        sleeps, runs = [], []

        async def fake_sleep(s):
            sleeps.append(s)
            if len(sleeps) == 4:
                raise asyncio.CancelledError

        def reconcile():
            runs.append(1)
            if len(runs) == 1:
                raise RuntimeError("orion down")

        try:
            await sm.run_periodic_reconcile(reconcile, sleep=fake_sleep, pending=lambda: False)
        except asyncio.CancelledError:
            pass
        return sleeps, runs

    sleeps, runs = asyncio.run(scenario())
    assert sleeps == [240, 3600, 3600, 3600]
    assert len(runs) == 3  # the first one failed and the loop went on


def test_cancellation_while_sleeping_is_clean():
    async def scenario():
        task = asyncio.create_task(sm.run_periodic_reconcile(lambda: None, pending=lambda: False))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return "cancelled"
        return "finished"

    assert asyncio.run(scenario()) == "cancelled"


def test_app_uses_the_loop_and_awaits_its_cancellation():
    with open(os.path.join(_SVC_DIR, "app.py"), encoding="utf-8") as f:
        src = f.read()
    assert "run_periodic_reconcile" in src
    assert "periodic_task.cancel()" in src
    assert "await periodic_task" in src
    assert "wait_for(periodic" not in src


def _drive(pending_seq, cap_sleeps):
    """Run the loop; pending_seq(i) says whether a retirement is pending after pass i."""
    async def scenario():
        sleeps, runs = [], []

        async def fake_sleep(s):
            sleeps.append(s)
            if len(sleeps) == cap_sleeps:
                raise asyncio.CancelledError

        try:
            await sm.run_periodic_reconcile(
                lambda: runs.append(1), sleep=fake_sleep,
                pending=lambda: pending_seq(len(runs)),
            )
        except asyncio.CancelledError:
            pass
        return sleeps

    return asyncio.run(scenario())


def test_short_interval_while_pending_with_an_upper_bound():
    sleeps = _drive(lambda n: True, 13)
    assert sleeps[:10] == [240] * 10
    assert sleeps[10:] == [3600] * 3


def test_back_to_the_hour_as_soon_as_nothing_is_pending():
    sleeps = _drive(lambda n: n < 2, 5)
    assert sleeps == [240, 240, 3600, 3600, 3600]
