"""End-to-end: what a rechargeable battery earns, against the real integrations.

The rest of this tier proves the disposable-cell loop. This module covers the
rechargeable side of it — the **charge** task from ha-home-keeper-battery-notes#18 —
against the real Home Keeper store rather than the test fake, because the pieces this
feature leans on are Home Keeper's, not ours: a task whose ``name`` is locked against
every edit (so converting one is a delete + ``add_task``, not a rename), deletion
protection that ``force`` has to get past, and the ``source`` payload — including our
``kind`` — surviving the round trip out through ``list_tasks``.

Each test drives the real options flow, so the option itself is under test too.
"""

from __future__ import annotations

import pytest

RECHARGEABLE = "e2e_rechargeable_device"
DISPOSABLE = "e2e_disposable_device"


def _low(api, device_id: str, name: str, battery_type: str) -> None:
    api.fire(
        "battery_notes_battery_threshold",
        {
            "device_id": device_id,
            "device_name": name,
            "battery_low": True,
            "battery_type": battery_type,
            "battery_quantity": 1,
            "battery_level": 8,
        },
    )


def _armed(task) -> bool:
    return bool(task and task.get("next_due"))


def _dormant(task) -> bool:
    return bool(task and not task.get("next_due"))


@pytest.fixture
def glue(api):
    """Hand back the API with a clean slate, and restore the default afterwards."""
    for device in (RECHARGEABLE, DISPOSABLE):
        api.delete_glue_task(device)
    yield api
    api.set_options(rechargeable_mode="skip")
    for device in (RECHARGEABLE, DISPOSABLE):
        api.delete_glue_task(device)


def test_rechargeable_low_raises_nothing_by_default(glue):
    # "Ignore them" is the default, so an upgrade is a no-op for anyone who never
    # touched the option: a low rechargeable earns no task at all.
    glue.set_options(rechargeable_mode="skip")

    _low(glue, RECHARGEABLE, "E2E valve", "Rechargeable")
    _low(glue, DISPOSABLE, "E2E remote", "AAA")

    # The disposable's task is the control: it proves the event was delivered and
    # processed, so the rechargeable's absence is a decision rather than a dropped event.
    glue.poll_glue_task(DISPOSABLE, _armed, "became armed")
    assert glue.glue_task(RECHARGEABLE) is None


def test_rechargeable_low_creates_a_charge_task(glue):
    # The ask in #18: a low rechargeable is a *charge* task, told apart from a
    # replacement by its name, its chip icon and the prompt shown on completion.
    glue.set_options(rechargeable_mode="charge")

    _low(glue, RECHARGEABLE, "E2E valve", "Rechargeable")
    task = glue.poll_glue_task(RECHARGEABLE, _armed, "became armed")

    assert task["name"] == "Charge battery: E2E valve"
    assert task["source"]["home_keeper_battery_notes"]["kind"] == "charge"
    assert task["task_chips"] == [
        {"label": "1× Rechargeable", "icon": "mdi:battery-charging"}
    ]
    assert task["managed_by"]["completion_prompt"] == "Mark battery as charged?"

    # A disposable in the same install is untouched by the mode.
    _low(glue, DISPOSABLE, "E2E remote", "AAA")
    other = glue.poll_glue_task(DISPOSABLE, _armed, "became armed")
    assert other["name"] == "Replace battery: E2E remote"
    assert other["source"]["home_keeper_battery_notes"]["kind"] == "replace"
    assert other["task_chips"] == [{"label": "1× AAA", "icon": "mdi:battery"}]


def test_charge_task_logs_every_charge_on_one_task(glue):
    # Charging is a cycle, not a one-off: the task arms on each drain and clears on
    # each charge, and the completions pile up on the *same* task — that accumulating
    # log is the whole point of raising a charge task rather than skipping it.
    glue.set_options(rechargeable_mode="charge")

    _low(glue, RECHARGEABLE, "E2E valve", "Rechargeable")
    first = glue.poll_glue_task(RECHARGEABLE, _armed, "became armed")

    glue.fire(
        "battery_notes_battery_threshold",
        {"device_id": RECHARGEABLE, "device_name": "E2E valve", "battery_low": False},
    )
    charged = glue.poll_glue_task(RECHARGEABLE, _dormant, "went dormant")
    assert charged["id"] == first["id"]
    assert len(charged["completions"]) == 1

    _low(glue, RECHARGEABLE, "E2E valve", "Rechargeable")
    again = glue.poll_glue_task(RECHARGEABLE, _armed, "re-armed")
    assert again["id"] == first["id"]  # one persistent task, not a new one each cycle
    assert len(again["completions"]) == 1  # history kept across the cycle


def test_switching_to_replace_converts_the_charge_task(glue):
    # Home Keeper locks a managed task's name against every edit — the owning
    # integration's included — so changing the mode can't rename the task. It has to
    # come back as a new one, which only the real store can prove.
    glue.set_options(rechargeable_mode="charge")
    _low(glue, RECHARGEABLE, "E2E valve", "Rechargeable")
    charge_task = glue.poll_glue_task(RECHARGEABLE, _armed, "became armed")

    glue.set_options(rechargeable_mode="replace")
    _low(glue, RECHARGEABLE, "E2E valve", "Rechargeable")
    converted = glue.poll_glue_task(
        RECHARGEABLE,
        lambda t: _armed(t) and t["id"] != charge_task["id"],
        "was recreated",
    )

    assert converted["name"] == "Replace battery: E2E valve"
    assert converted["source"]["home_keeper_battery_notes"]["kind"] == "replace"
    # Converted, not duplicated. Polled rather than read once: the options change
    # reloads the entry, so a reconcile is in flight alongside the event we fired.
    glue.poll_count(RECHARGEABLE, 1, "settled at exactly one task")


def test_switching_to_skip_retires_the_task(glue):
    # Picking "Ignore them" has to clean up after itself: the task is removed outright,
    # not completed, so it leaves no phantom charge in the history and nothing lingering
    # in Home Keeper's Monitored list.
    glue.set_options(rechargeable_mode="charge")
    _low(glue, RECHARGEABLE, "E2E valve", "Rechargeable")
    glue.poll_glue_task(RECHARGEABLE, _armed, "became armed")

    glue.set_options(rechargeable_mode="skip")
    _low(glue, RECHARGEABLE, "E2E valve", "Rechargeable")

    glue.poll_glue_task(RECHARGEABLE, lambda t: t is None, "was retired")
