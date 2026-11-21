"""The inbox consumer task ending outside shutdown is logged CRITICAL, with its error."""

import asyncio
import logging

import pytest


def _finished(coro_fn):
    """A done task from `coro_fn` (raised, returned or cancelled)."""

    async def main():
        task = asyncio.create_task(coro_fn())
        try:
            await task
        except BaseException:
            pass
        return task

    return asyncio.run(main())


async def _boom():
    raise RuntimeError("lock pool exploded")


async def _returns():
    return None


async def _cancelled():
    asyncio.current_task().cancel()
    await asyncio.sleep(1)


def _critical(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]


def test_consumer_exception_outside_shutdown_is_critical_with_the_error(caplog):
    from app import _on_consumer_task_done

    with caplog.at_level(logging.WARNING):
        _on_consumer_task_done(_finished(_boom), shutting_down=False)
    [msg] = _critical(caplog)
    assert "inbox_consumer_task_ended" in msg
    assert "lock pool exploded" in msg


@pytest.mark.parametrize("fn", [_returns, _cancelled], ids=["returned", "cancelled"])
def test_consumer_ending_without_error_outside_shutdown_is_critical(caplog, fn):
    from app import _on_consumer_task_done

    with caplog.at_level(logging.WARNING):
        _on_consumer_task_done(_finished(fn), shutting_down=False)
    [msg] = _critical(caplog)
    assert "inbox_consumer_task_ended" in msg


@pytest.mark.parametrize("fn", [_boom, _returns, _cancelled], ids=["raised", "returned", "cancelled"])
def test_consumer_ending_during_shutdown_is_not_critical(caplog, fn):
    from app import _on_consumer_task_done

    with caplog.at_level(logging.WARNING):
        _on_consumer_task_done(_finished(fn), shutting_down=True)
    assert _critical(caplog) == []
