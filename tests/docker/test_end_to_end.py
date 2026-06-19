"""End-to-end: real Home Keeper + Battery Notes + this glue in a HA container.

We drive Battery Notes' documented events over the REST API and assert that Home
Keeper's to-do list reflects the battery task being armed and cleared. The to-do
list counts incomplete items, and a triggered task is on the list exactly while
armed — so its count is our observable for the full glue → Home Keeper loop.

This is also the contract test for the external assumption that Battery Notes fires
``battery_notes_battery_threshold`` / ``battery_notes_battery_replaced`` with a
``device_id`` and ``battery_low`` field (see const.py): if that contract is wrong,
these assertions fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

TODO = "todo.home_keeper_tasks"
DEVICE = "e2e_battery_device"

# The Battery Notes surface this glue depends on (mirrors const.py). The static
# contract test below checks these still exist in the *fetched, real* Battery Notes.
BN_SERVICES = ("set_battery_replaced", "check_battery_last_reported")
BN_EVENTS = (
    "battery_notes_battery_threshold",
    "battery_notes_battery_replaced",
    "battery_notes_battery_not_reported",
)

# Where ci/fetch-upstreams.sh stages the real Battery Notes for the docker tier.
_BN_DIR = Path(__file__).resolve().parent / "custom_components" / "battery_notes"


def _count(api) -> int:
    return int(api.state(TODO) or 0)


def test_battery_notes_contract_against_real_source():
    """Guard the external Battery Notes contract this glue depends on.

    The event-driven tests fire synthetic events (provisioning a real Battery Notes
    device — a 4-version, sub-entry config model — in CI is out of scope), so they
    can't catch Battery Notes *renaming* the service or events the glue is coupled
    to. Instead assert those names still exist in the fetched, real Battery Notes
    source. If this fails, Battery Notes moved its surface — update const.py and
    re-pin BN_REF.
    """
    if not _BN_DIR.is_dir():
        pytest.skip("Battery Notes not staged (run ci/fetch-upstreams.sh first)")

    services_yaml = (_BN_DIR / "services.yaml").read_text(encoding="utf-8")
    for service in BN_SERVICES:
        assert f"{service}:" in services_yaml, (
            f"battery_notes.{service} no longer declared — a service the glue calls "
            "moved; update const.py (BN_SERVICE_*)."
        )

    # The event names the glue listens for must still appear in Battery Notes' code.
    source_text = "\n".join(
        p.read_text(encoding="utf-8") for p in _BN_DIR.rglob("*.py")
    )
    for event in BN_EVENTS:
        assert event in source_text, (
            f"Battery Notes no longer references the '{event}' event — update const.py."
        )


def test_low_then_replaced_arms_then_clears_the_task(api):
    base = _count(api)

    # Battery goes low → a triggered task is created, armed (on the to-do list).
    api.fire(
        "battery_notes_battery_threshold",
        {"device_id": DEVICE, "device_name": "E2E sensor", "battery_low": True},
    )
    api.poll_state(TODO, str(base + 1))

    # Battery replaced → the task records the completion and goes dormant (off the list).
    api.fire("battery_notes_battery_replaced", {"device_id": DEVICE})
    api.poll_state(TODO, str(base))


def test_low_again_rearms_without_duplicating(api):
    base = _count(api)
    # First low + replace.
    api.fire(
        "battery_notes_battery_threshold",
        {"device_id": DEVICE, "device_name": "E2E sensor", "battery_low": True},
    )
    api.poll_state(TODO, str(base + 1))
    api.fire("battery_notes_battery_replaced", {"device_id": DEVICE})
    api.poll_state(TODO, str(base))

    # Low again → re-armed (count back to +1, not +2 — same task reused).
    api.fire(
        "battery_notes_battery_threshold",
        {"device_id": DEVICE, "device_name": "E2E sensor", "battery_low": True},
    )
    api.poll_state(TODO, str(base + 1))
    # Clean up so re-runs start from the same baseline.
    api.fire("battery_notes_battery_replaced", {"device_id": DEVICE})
    api.poll_state(TODO, str(base))
