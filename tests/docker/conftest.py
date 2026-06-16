"""Fixtures for the Docker end-to-end tier.

Drives a real Home Assistant container (with Home Keeper + Battery Notes + this
glue all installed) over the REST API. Auth is bootstrapped via the onboarding API,
mirroring ha-home-keeper's integration conftest.
"""

from __future__ import annotations

import time

import pytest
import requests

HA_URL = "http://localhost:8123"
CLIENT_ID = f"{HA_URL}/"
STARTUP_TIMEOUT = 180


def _wait_for_ha() -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            r = requests.get(f"{HA_URL}/api/", timeout=5)
            if r.status_code in (200, 401):
                return
        except requests.ConnectionError:
            pass
        time.sleep(2)
    raise TimeoutError(f"Home Assistant did not start within {STARTUP_TIMEOUT}s")


def _onboard_and_token() -> str:
    r = requests.post(
        f"{HA_URL}/api/onboarding/users",
        json={
            "client_id": CLIENT_ID,
            "name": "Test",
            "username": "test",
            "password": "testtest1",
            "language": "en",
        },
        timeout=10,
    )
    if r.status_code == 200:
        code = r.json()["auth_code"]
    else:
        # Already onboarded — log in.
        lf = requests.post(
            f"{HA_URL}/auth/login_flow",
            json={
                "client_id": CLIENT_ID,
                "handler": ["homeassistant", None],
                "redirect_uri": f"{HA_URL}/?auth_callback=1",
            },
            timeout=10,
        ).json()
        res = requests.post(
            f"{HA_URL}/auth/login_flow/{lf['flow_id']}",
            json={"username": "test", "password": "testtest1", "client_id": CLIENT_ID},
            timeout=10,
        ).json()
        code = res["result"]
    tok = requests.post(
        f"{HA_URL}/auth/token",
        data={"grant_type": "authorization_code", "code": code, "client_id": CLIENT_ID},
        timeout=10,
    )
    tok.raise_for_status()
    return tok.json()["access_token"]


@pytest.fixture(scope="session")
def token() -> str:
    _wait_for_ha()
    access = _onboard_and_token()
    # Wait for Home Keeper's entities to appear (integration finished setup).
    deadline = time.monotonic() + 120
    headers = {"Authorization": f"Bearer {access}"}
    while time.monotonic() < deadline:
        r = requests.get(f"{HA_URL}/api/states", headers=headers, timeout=10)
        if r.ok and any(
            s["entity_id"] == "todo.home_keeper_tasks" for s in r.json()
        ):
            return access
        time.sleep(2)
    raise TimeoutError("Home Keeper entities did not appear")


@pytest.fixture
def api(token):
    """A tiny REST client bound to the authenticated session."""

    class _Api:
        headers = {"Authorization": f"Bearer {token}"}

        def fire(self, event_type: str, data: dict) -> None:
            r = requests.post(
                f"{HA_URL}/api/events/{event_type}",
                headers=self.headers,
                json=data,
                timeout=10,
            )
            r.raise_for_status()

        def state(self, entity_id: str) -> str | None:
            r = requests.get(
                f"{HA_URL}/api/states/{entity_id}", headers=self.headers, timeout=10
            )
            return r.json()["state"] if r.ok else None

        def poll_state(self, entity_id: str, want: str, timeout: float = 20) -> str:
            deadline = time.monotonic() + timeout
            last = None
            while time.monotonic() < deadline:
                last = self.state(entity_id)
                if last == want:
                    return last
                time.sleep(1)
            raise AssertionError(
                f"{entity_id} did not reach {want!r} (last={last!r})"
            )

    return _Api()
