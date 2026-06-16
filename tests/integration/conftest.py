"""Fixtures for the HA-runtime integration tests (need pytest-homeassistant-custom-component)."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load our custom_components/ during tests."""
    yield
