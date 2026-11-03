"""Fixtures for the HA-runtime integration tests (need pytest-homeassistant-custom-component)."""

from collections.abc import Iterator

import pytest
from blockbuster import BlockBuster, blockbuster_ctx


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load our custom_components/ during tests."""
    yield


@pytest.fixture(autouse=True)
def blockbuster() -> Iterator[BlockBuster]:
    """Fail a test when code blocks the event loop thread.

    Blockbuster patches file, socket, sleep and similar calls. When such a call
    runs while an event loop runs in the same thread, it raises BlockingError
    with the exact file and line. Home Assistant gives the same warning at run
    time ("Detected blocking call"); this fixture finds it in the tests instead.
    """
    with blockbuster_ctx() as bb:
        yield bb
