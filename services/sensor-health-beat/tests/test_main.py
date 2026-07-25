"""Tests for the one-shot CronJob entry point (sensor_health_beat.__main__)."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SVC_DIR = os.path.normpath(os.path.join(_TEST_DIR, ".."))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import sensor_health_beat.__main__ as runner


def _fake_beat():
    beat = MagicMock()
    beat.start = AsyncMock()
    beat.run_once = AsyncMock()
    beat.stop = AsyncMock()
    return beat


def test_run_executes_single_cycle_and_stops():
    beat = _fake_beat()
    with patch.object(runner, "SensorHealthBeat", return_value=beat), \
         patch.object(runner, "Settings", return_value=MagicMock()):
        asyncio.run(runner._run())
    beat.start.assert_awaited_once()
    beat.run_once.assert_awaited_once()
    beat.stop.assert_awaited_once()


def test_run_stops_even_if_cycle_raises():
    beat = _fake_beat()
    beat.run_once = AsyncMock(side_effect=RuntimeError("boom"))
    with patch.object(runner, "SensorHealthBeat", return_value=beat), \
         patch.object(runner, "Settings", return_value=MagicMock()):
        with pytest.raises(RuntimeError):
            asyncio.run(runner._run())
    beat.stop.assert_awaited_once()
