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
# This glue's domain — also the namespace it stamps into a task's ``source``.
GLUE_DOMAIN = "home_keeper_battery_notes"


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
        def __init__(self) -> None:
            self.headers = {"Authorization": f"Bearer {token}"}

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

        # ── services ─────────────────────────────────────────────────────────
        def call(
            self, domain: str, service: str, data: dict | None = None, *, response=False
        ):
            """Call a service, optionally returning its response payload."""
            query = "?return_response" if response else ""
            r = requests.post(
                f"{HA_URL}/api/services/{domain}/{service}{query}",
                headers=self.headers,
                json=data or {},
                timeout=30,
            )
            r.raise_for_status()
            return r.json() if r.text else None

        def tasks(self) -> list[dict]:
            """Every Home Keeper task. Retries: a reload briefly 400s the service."""
            last: Exception | None = None
            for _ in range(10):
                try:
                    out = self.call("home_keeper", "list_tasks", response=True)
                    return list(out["service_response"]["tasks"])
                except requests.HTTPError as err:
                    last = err
                    time.sleep(1)
            raise AssertionError(f"home_keeper.list_tasks never succeeded: {last}")

        def glue_task(self, device_id: str) -> dict | None:
            """The glue's task for *device_id*, matched by its source namespace."""
            for task in self.tasks():
                src = (task.get("source") or {}).get(GLUE_DOMAIN)
                if isinstance(src, dict) and src.get("device_id") == device_id:
                    return task
            return None

        def poll_glue_task(
            self, device_id: str, predicate, want: str, timeout: float = 25
        ) -> dict:
            deadline = time.monotonic() + timeout
            last = None
            while time.monotonic() < deadline:
                last = self.glue_task(device_id)
                if predicate(last):
                    return last
                time.sleep(1)
            raise AssertionError(f"task for {device_id} never {want} (last={last!r})")

        def delete_glue_task(self, device_id: str) -> None:
            task = self.glue_task(device_id)
            if task is not None:
                # force: our tasks are deletion-protected while the entry resolves.
                self.call(
                    "home_keeper",
                    "delete_task",
                    {"task_id": task["id"], "force": True},
                )

        # ── options ──────────────────────────────────────────────────────────
        def glue_entry_id(self) -> str:
            r = requests.get(
                f"{HA_URL}/api/config/config_entries/entry",
                headers=self.headers,
                timeout=10,
            )
            r.raise_for_status()
            for entry in r.json():
                if entry["domain"] == GLUE_DOMAIN:
                    return entry["entry_id"]
            raise AssertionError(f"no {GLUE_DOMAIN} config entry in the container")

        def set_options(self, **overrides) -> None:
            """Drive the real options flow, submitting the form the UI would.

            Starts from the schema's own defaults so every field round-trips, then
            applies *overrides* — the same shape as a user editing one control.
            """
            entry_id = self.glue_entry_id()
            r = requests.post(
                f"{HA_URL}/api/config/config_entries/options/flow",
                headers=self.headers,
                json={"handler": entry_id},
                timeout=20,
            )
            r.raise_for_status()
            flow = r.json()
            assert flow["type"] == "form", flow
            data = {
                field["name"]: field["default"]
                for field in flow["data_schema"]
                if "default" in field
            }
            data.update(overrides)
            r = requests.post(
                f"{HA_URL}/api/config/config_entries/options/flow/{flow['flow_id']}",
                headers=self.headers,
                json=data,
                timeout=20,
            )
            r.raise_for_status()
            assert r.json()["type"] == "create_entry", r.text
            # Saving reloads the entry; wait until it is serving again.
            self.tasks()

    return _Api()
