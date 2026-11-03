"""Guard test for the ``blockbuster`` fixture in conftest.py.

Every test in this folder runs the glue inside a real Home Assistant event
loop. The autouse fixture fails a test when code blocks that loop. This test
proves the fixture is active, because without it the other tests prove nothing.
"""

import asyncio
import time

import pytest
from blockbuster import BlockingError


def test_blockbuster_finds_a_blocking_call():
    async def blocking() -> None:
        # The blocking call is the point of this test.
        time.sleep(0.01)  # noqa: ASYNC251

    with pytest.raises(BlockingError):
        asyncio.run(blocking())
