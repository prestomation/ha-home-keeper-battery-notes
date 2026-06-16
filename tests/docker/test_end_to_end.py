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

TODO = "todo.home_keeper_tasks"
DEVICE = "e2e_battery_device"


def _count(api) -> int:
    return int(api.state(TODO) or 0)


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
