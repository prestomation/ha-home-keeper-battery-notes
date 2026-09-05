"""Integration tests for the glue against Home Keeper's real test fake.

These exercise the full contract — event in → Home Keeper service call → task state
— using ``home_keeper.testing.async_setup_fake_home_keeper`` (the real model/event
code), plus a stub ``battery_notes.set_battery_replaced`` service to observe two-way
sync. They need a real HA test environment (pytest-homeassistant-custom-component).
"""

from __future__ import annotations

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, SupportsResponse
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.home_keeper_battery_notes.const import (
    BN_DOMAIN,
    BN_EVENT_NOT_REPORTED,
    BN_EVENT_REPLACED,
    BN_EVENT_THRESHOLD,
    BN_FIELD_DAYS_LAST_REPORTED,
    BN_SERVICE_CHECK_LAST_REPORTED,
    BN_SERVICE_SET_REPLACED,
    DOMAIN,
    HK_DOMAIN,
    OPT_CHARGE_NAME_TEMPLATE,
    OPT_CLEAR_ON_RECOVERY,
    OPT_NOT_REPORTED_DAYS,
    OPT_RECHARGEABLE_MODE,
    OPT_SKIP_RECHARGEABLE,
    OPT_TREAT_NOT_REPORTED,
)

try:
    from home_keeper.testing import async_setup_fake_home_keeper
except ImportError:  # pragma: no cover - home-keeper not installed in this env
    async_setup_fake_home_keeper = None

pytestmark = pytest.mark.skipif(
    async_setup_fake_home_keeper is None,
    reason="home-keeper (test fake) not installed",
)

DEVICE = "dev_front_door"


async def _setup_glue(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _stub_set_replaced(hass: HomeAssistant) -> list[dict]:
    """Register a stub Battery Notes set_battery_replaced and capture its calls."""
    calls: list[dict] = []

    async def _handler(call):
        calls.append(dict(call.data))

    hass.services.async_register(BN_DOMAIN, BN_SERVICE_SET_REPLACED, _handler)
    return calls


async def _fire_threshold(hass: HomeAssistant, *, low: bool) -> None:
    hass.bus.async_fire(
        BN_EVENT_THRESHOLD,
        {"device_id": DEVICE, "device_name": "Front door sensor", "battery_low": low},
    )
    await hass.async_block_till_done()


def _make_bn_low_sensor(hass: HomeAssistant, *, unique: str, state: str) -> str:
    """Register a Battery Notes battery-low binary sensor in *state*; return device_id."""
    bn_entry = MockConfigEntry(domain=BN_DOMAIN, data={})
    bn_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=bn_entry.entry_id,
        identifiers={(BN_DOMAIN, unique)},
        name=f"{unique} sensor",
    )
    ent = er.async_get(hass).async_get_or_create(
        "binary_sensor", BN_DOMAIN, f"{unique}_low",
        device_id=device.id, original_device_class="battery",
    )
    hass.states.async_set(ent.entity_id, state)
    return device.id


async def test_low_creates_armed_task(hass: HomeAssistant) -> None:
    hk = await async_setup_fake_home_keeper(hass)
    await _setup_glue(hass)

    await _fire_threshold(hass, low=True)

    task = hk.get_task_by_source(DOMAIN, device_id=DEVICE)
    assert task is not None
    assert task["recurrence_type"] == "triggered"
    assert task["next_due"]  # armed / due-now


async def test_low_with_battery_type_sets_task_chip(hass: HomeAssistant) -> None:
    hk = await async_setup_fake_home_keeper(hass)
    await _setup_glue(hass)

    hass.bus.async_fire(
        BN_EVENT_THRESHOLD,
        {
            "device_id": DEVICE,
            "device_name": "Motion sensor",
            "battery_low": True,
            "battery_type": "AAA",
            "battery_quantity": 2,
        },
    )
    await hass.async_block_till_done()

    task = hk.get_task_by_source(DOMAIN, device_id=DEVICE)
    assert task is not None
    assert task.get("task_chips") == [{"label": "2× AAA", "icon": "mdi:battery"}]


async def test_low_without_battery_type_has_no_chip(hass: HomeAssistant) -> None:
    hk = await async_setup_fake_home_keeper(hass)
    await _setup_glue(hass)

    await _fire_threshold(hass, low=True)  # no battery_type in event

    task = hk.get_task_by_source(DOMAIN, device_id=DEVICE)
    assert task is not None
    assert task.get("task_chips") == []


async def test_replaced_clears_to_dormant(hass: HomeAssistant) -> None:
    hk = await async_setup_fake_home_keeper(hass)
    await _setup_glue(hass)
    await _fire_threshold(hass, low=True)

    hass.bus.async_fire(BN_EVENT_REPLACED, {"device_id": DEVICE})
    await hass.async_block_till_done()

    task = hk.get_task_by_source(DOMAIN, device_id=DEVICE)
    assert task is not None
    assert task["next_due"] is None        # dormant
    assert len(task["completions"]) == 1   # the replacement is recorded


async def test_low_again_rearms_same_task_keeping_history(hass: HomeAssistant) -> None:
    hk = await async_setup_fake_home_keeper(hass)
    await _setup_glue(hass)
    await _fire_threshold(hass, low=True)
    hass.bus.async_fire(BN_EVENT_REPLACED, {"device_id": DEVICE})
    await hass.async_block_till_done()
    first_id = hk.get_task_by_source(DOMAIN, device_id=DEVICE)["id"]

    await _fire_threshold(hass, low=True)  # low again

    task = hk.get_task_by_source(DOMAIN, device_id=DEVICE)
    assert task["id"] == first_id          # same task, not a duplicate
    assert task["next_due"]                # re-armed
    assert len(task["completions"]) == 1   # prior replacement preserved


async def test_recovery_clears_when_enabled(hass: HomeAssistant) -> None:
    hk = await async_setup_fake_home_keeper(hass)
    await _setup_glue(hass)
    await _fire_threshold(hass, low=True)

    await _fire_threshold(hass, low=False)  # level recovered on its own

    task = hk.get_task_by_source(DOMAIN, device_id=DEVICE)
    assert task["next_due"] is None


async def test_two_way_completion_pushes_set_replaced_without_looping(
    hass: HomeAssistant,
) -> None:
    hk = await async_setup_fake_home_keeper(hass)
    calls = _stub_set_replaced(hass)
    await _setup_glue(hass)
    await _fire_threshold(hass, low=True)
    task_id = hk.get_task_by_source(DOMAIN, device_id=DEVICE)["id"]

    # User checks the task off in Home Keeper (origin=None).
    hk.fire_user_completion(task_id)
    await hass.async_block_till_done()

    # We mirrored it to Battery Notes exactly once (no loop) and didn't re-complete.
    assert calls == [{"device_id": DEVICE}]
    task = hk.get_task_by_source(DOMAIN, device_id=DEVICE)
    assert len(task["completions"]) == 1  # only the user's completion, no echo


async def test_duplicate_low_events_do_not_create_duplicate_tasks(
    hass: HomeAssistant,
) -> None:
    hk = await async_setup_fake_home_keeper(hass)
    await _setup_glue(hass)
    await _fire_threshold(hass, low=True)
    await _fire_threshold(hass, low=True)  # Battery Notes re-fires

    ours = [t for t in hk.tasks.values() if (t.get("source") or {}).get(DOMAIN)]
    assert len(ours) == 1


async def test_no_home_keeper_is_a_safe_noop(hass: HomeAssistant) -> None:
    # Home Keeper absent (no fake): setup + events must not raise.
    await async_setup_component(hass, "homeassistant", {})
    await _setup_glue(hass)
    await _fire_threshold(hass, low=True)  # no service to call → guarded no-op


async def test_concurrent_low_events_do_not_duplicate(hass: HomeAssistant) -> None:
    # Two low events fired back-to-back WITHOUT blocking between them: the lock must
    # serialize the list→create span so they don't both create a task. (Distinct from
    # test_duplicate_low_events_*, which blocks between events.)
    hk = await async_setup_fake_home_keeper(hass)
    await _setup_glue(hass)

    payload = {"device_id": DEVICE, "device_name": "Front door", "battery_low": True}
    hass.bus.async_fire(BN_EVENT_THRESHOLD, dict(payload))
    hass.bus.async_fire(BN_EVENT_THRESHOLD, dict(payload))
    await hass.async_block_till_done()

    ours = [t for t in hk.tasks.values() if (t.get("source") or {}).get(DOMAIN)]
    assert len(ours) == 1


async def test_reconcile_skips_clear_when_recovery_disabled(hass: HomeAssistant) -> None:
    # With clear_on_recovery off, a startup reconcile must NOT clear an armed task even
    # for a device whose battery affirmatively recovered (low sensor reads "off").
    hk = await async_setup_fake_home_keeper(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={OPT_CLEAR_ON_RECOVERY: False}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device_id = _make_bn_low_sensor(hass, unique="recovered", state="off")
    hass.bus.async_fire(
        BN_EVENT_THRESHOLD,
        {"device_id": device_id, "device_name": "x", "battery_low": True},
    )
    await hass.async_block_till_done()
    assert hk.get_task_by_source(DOMAIN, device_id=device_id)["next_due"]  # armed

    await entry.runtime_data._reconcile()
    await hass.async_block_till_done()

    assert hk.get_task_by_source(DOMAIN, device_id=device_id)["next_due"]  # still armed


async def test_reconcile_clears_on_affirmative_recovery(hass: HomeAssistant) -> None:
    # A device whose low sensor reads "off" (reporting, not low) clears the armed task.
    hk = await async_setup_fake_home_keeper(hass)
    entry = await _setup_glue(hass)
    device_id = _make_bn_low_sensor(hass, unique="backok", state="off")
    hass.bus.async_fire(
        BN_EVENT_THRESHOLD,
        {"device_id": device_id, "device_name": "x", "battery_low": True},
    )
    await hass.async_block_till_done()
    assert hk.get_task_by_source(DOMAIN, device_id=device_id)["next_due"]  # armed

    await entry.runtime_data._reconcile()
    await hass.async_block_till_done()

    assert hk.get_task_by_source(DOMAIN, device_id=device_id)["next_due"] is None  # cleared


async def test_reconcile_keeps_armed_task_for_silent_device(hass: HomeAssistant) -> None:
    # A device that's gone dark (low sensor "unknown", neither low nor recovered) must
    # keep its armed task — clearing it would record a phantom replacement.
    hk = await async_setup_fake_home_keeper(hass)
    entry = await _setup_glue(hass)
    device_id = _make_bn_low_sensor(hass, unique="dead", state="unknown")
    hass.bus.async_fire(
        BN_EVENT_THRESHOLD,
        {"device_id": device_id, "device_name": "x", "battery_low": True},
    )
    await hass.async_block_till_done()
    assert hk.get_task_by_source(DOMAIN, device_id=device_id)["next_due"]  # armed

    await entry.runtime_data._reconcile()
    await hass.async_block_till_done()

    assert hk.get_task_by_source(DOMAIN, device_id=device_id)["next_due"]  # still armed


async def test_reconcile_reads_battery_attributes_into_notes(hass: HomeAssistant) -> None:
    # A reconcile-created task should pick up battery_type/quantity from the Battery
    # Notes low sensor's attributes, matching a live-event-created task's notes.
    hk = await async_setup_fake_home_keeper(hass)
    entry = await _setup_glue(hass)

    bn_entry = MockConfigEntry(domain=BN_DOMAIN, data={})
    bn_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=bn_entry.entry_id,
        identifiers={(BN_DOMAIN, "remote1")},
        name="Hall remote",
    )
    ent = er.async_get(hass).async_get_or_create(
        "binary_sensor", BN_DOMAIN, "remote1_low",
        device_id=device.id, original_device_class="battery",
    )
    hass.states.async_set(
        ent.entity_id, "on", {"battery_type": "CR2032", "battery_quantity": 1}
    )

    await entry.runtime_data._reconcile()
    await hass.async_block_till_done()

    task = hk.get_task_by_source(DOMAIN, device_id=device.id)
    assert task is not None and task["next_due"]  # created + armed
    assert "CR2032" in task["notes"]
    assert task.get("task_chips") == [{"label": "1× CR2032", "icon": "mdi:battery"}]


async def test_rechargeable_low_creates_no_task(hass: HomeAssistant) -> None:
    # Default (skip on): a rechargeable going low means "charge it", not "replace it",
    # so no replace-battery task is created.
    hk = await async_setup_fake_home_keeper(hass)
    await _setup_glue(hass)

    hass.bus.async_fire(
        BN_EVENT_THRESHOLD,
        {
            "device_id": DEVICE,
            "device_name": "Fold7",
            "battery_low": True,
            "battery_type": "Rechargeable",
        },
    )
    await hass.async_block_till_done()

    assert hk.get_task_by_source(DOMAIN, device_id=DEVICE) is None


async def test_rechargeable_low_creates_a_charge_task(hass: HomeAssistant) -> None:
    # "charge": a rechargeable going low is a charge chore, with its own wording,
    # chip icon and completion prompt.
    hk = await async_setup_fake_home_keeper(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={OPT_RECHARGEABLE_MODE: "charge"}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.bus.async_fire(
        BN_EVENT_THRESHOLD,
        {
            "device_id": DEVICE,
            "device_name": "Bedroom valve",
            "battery_low": True,
            "battery_type": "Rechargeable",
            "battery_quantity": 1,
        },
    )
    await hass.async_block_till_done()

    task = hk.get_task_by_source(DOMAIN, device_id=DEVICE)
    assert task is not None and task["next_due"]  # created + armed
    assert task["name"] == "Charge battery: Bedroom valve"
    assert task["source"][DOMAIN]["kind"] == "charge"
    assert task["task_chips"] == [
        {"label": "1× Rechargeable", "icon": "mdi:battery-charging"}
    ]
    assert task["managed_by"]["completion_prompt"] == "Mark battery as charged?"


async def test_rechargeable_low_creates_replace_task_in_replace_mode(
    hass: HomeAssistant,
) -> None:
    # Opt-out: a user who tracks rechargeable replacements by hand still gets one.
    hk = await async_setup_fake_home_keeper(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={OPT_RECHARGEABLE_MODE: "replace"}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.bus.async_fire(
        BN_EVENT_THRESHOLD,
        {
            "device_id": DEVICE,
            "device_name": "Fold7",
            "battery_low": True,
            "battery_type": "Rechargeable",
        },
    )
    await hass.async_block_till_done()

    task = hk.get_task_by_source(DOMAIN, device_id=DEVICE)
    assert task is not None
    assert task["name"] == "Replace battery: Fold7"


async def test_legacy_skip_rechargeable_off_now_means_charge(
    hass: HomeAssistant,
) -> None:
    # An entry saved before rechargeable_mode existed carries only the old boolean.
    # Off meant "a low rechargeable is a task" — which is a charge task now.
    hk = await async_setup_fake_home_keeper(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={OPT_SKIP_RECHARGEABLE: False}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.bus.async_fire(
        BN_EVENT_THRESHOLD,
        {
            "device_id": DEVICE,
            "device_name": "Bedroom valve",
            "battery_low": True,
            "battery_type": "Rechargeable",
        },
    )
    await hass.async_block_till_done()

    task = hk.get_task_by_source(DOMAIN, device_id=DEVICE)
    assert task is not None
    assert task["name"] == "Charge battery: Bedroom valve"


async def test_charge_completion_is_not_mirrored_to_battery_notes(
    hass: HomeAssistant,
) -> None:
    # Charging isn't replacing. Mirroring it would stamp a replacement date on the
    # device that never happened, falsifying Battery Notes' own history.
    hk = await async_setup_fake_home_keeper(hass)
    calls = _stub_set_replaced(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={OPT_RECHARGEABLE_MODE: "charge"}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.bus.async_fire(
        BN_EVENT_THRESHOLD,
        {
            "device_id": DEVICE,
            "device_name": "Bedroom valve",
            "battery_low": True,
            "battery_type": "Rechargeable",
        },
    )
    await hass.async_block_till_done()
    task_id = hk.get_task_by_source(DOMAIN, device_id=DEVICE)["id"]

    hk.fire_user_completion(task_id)  # user checks the charge task off
    await hass.async_block_till_done()

    assert calls == []
    # Home Keeper still recorded it: the charge log is the point of the cycle.
    assert len(hk.get_task_by_source(DOMAIN, device_id=DEVICE)["completions"]) == 1


async def test_reconcile_converts_a_legacy_replace_task_to_a_charge_task(
    hass: HomeAssistant,
) -> None:
    # Switching to "charge" with the rechargeable currently low converts its task.
    # The name is locked and Home Keeper strips locked fields from update_task, so the
    # conversion is a delete + create — the old id is gone, the new task is armed.
    hk = await async_setup_fake_home_keeper(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={OPT_RECHARGEABLE_MODE: "charge"}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    bn_entry = MockConfigEntry(domain=BN_DOMAIN, data={})
    bn_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=bn_entry.entry_id,
        identifiers={(BN_DOMAIN, "valve")},
        name="Bedroom valve",
    )
    ent = er.async_get(hass).async_get_or_create(
        "binary_sensor", BN_DOMAIN, "valve_low",
        device_id=device.id, original_device_class="battery",
    )
    hass.states.async_set(
        ent.entity_id, "on", {"battery_type": "Rechargeable", "battery_quantity": 1}
    )
    hk.tasks["t_valve"] = {
        "id": "t_valve",
        "name": "Replace battery: Bedroom valve",
        "recurrence_type": "triggered",
        "next_due": None,
        "device_id": device.id,
        "source": {DOMAIN: {"device_id": device.id}},  # no kind — a legacy task
        "completions": [],
    }

    await entry.runtime_data._reconcile()
    await hass.async_block_till_done()

    assert "t_valve" not in hk.tasks
    task = hk.get_task_by_source(DOMAIN, device_id=device.id)
    assert task is not None and task["next_due"]  # recreated, armed
    assert task["name"] == "Charge battery: Bedroom valve"
    assert task["source"][DOMAIN]["kind"] == "charge"
    ours = [t for t in hk.tasks.values() if (t.get("source") or {}).get(DOMAIN)]
    assert len(ours) == 1  # converted, not duplicated


async def test_reconcile_removes_existing_rechargeable_task(hass: HomeAssistant) -> None:
    # An upgrade (or enabling the option) retires a stale replace-battery task for a
    # rechargeable device — even one that has since recovered (sensor "off"), which the
    # recovery path alone would only mark dormant, not remove.
    hk = await async_setup_fake_home_keeper(hass)
    entry = await _setup_glue(hass)  # default: skip_rechargeable on

    bn_entry = MockConfigEntry(domain=BN_DOMAIN, data={})
    bn_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=bn_entry.entry_id,
        identifiers={(BN_DOMAIN, "fold7")},
        name="Fold7",
    )
    ent = er.async_get(hass).async_get_or_create(
        "binary_sensor", BN_DOMAIN, "fold7_low",
        device_id=device.id, original_device_class="battery",
    )
    # Recovered (sensor "off") but still carrying the rechargeable battery type.
    hass.states.async_set(
        ent.entity_id, "off", {"battery_type": "Rechargeable", "battery_quantity": 1}
    )
    hk.tasks["t_fold7"] = {
        "id": "t_fold7",
        "name": "Replace battery: Fold7",
        "recurrence_type": "triggered",
        "next_due": None,
        "device_id": device.id,
        "source": {DOMAIN: {"device_id": device.id}},
        "completions": [],
    }

    await entry.runtime_data._reconcile()
    await hass.async_block_till_done()

    assert hk.get_task_by_source(DOMAIN, device_id=device.id) is None


async def test_not_reported_arms_task_when_enabled(hass: HomeAssistant) -> None:
    # A battery that's stopped reporting (suspected dead) arms a task when opted in,
    # with notes that explain why rather than looking like a normal low battery.
    hk = await async_setup_fake_home_keeper(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={OPT_TREAT_NOT_REPORTED: True}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.bus.async_fire(
        BN_EVENT_NOT_REPORTED,
        {"device_id": DEVICE, "device_name": "Attic sensor", "battery_last_reported_days": 9},
    )
    await hass.async_block_till_done()

    task = hk.get_task_by_source(DOMAIN, device_id=DEVICE)
    assert task is not None and task["next_due"]  # armed
    assert "not reporting for 9 days" in task["notes"]


async def test_not_reported_ignored_when_disabled(hass: HomeAssistant) -> None:
    # Default (opt-in off): a not-reported event must not create any task.
    hk = await async_setup_fake_home_keeper(hass)
    await _setup_glue(hass)

    hass.bus.async_fire(
        BN_EVENT_NOT_REPORTED,
        {"device_id": DEVICE, "device_name": "Attic sensor", "battery_last_reported_days": 9},
    )
    await hass.async_block_till_done()

    assert hk.get_task_by_source(DOMAIN, device_id=DEVICE) is None


async def test_startup_drives_check_last_reported_when_enabled(hass: HomeAssistant) -> None:
    # When opted in, the glue asks Battery Notes to check for stale batteries on
    # startup, passing the configured day threshold.
    await async_setup_fake_home_keeper(hass)
    calls: list[dict] = []

    async def _handler(call):
        calls.append(dict(call.data))

    hass.services.async_register(BN_DOMAIN, BN_SERVICE_CHECK_LAST_REPORTED, _handler)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={OPT_TREAT_NOT_REPORTED: True, OPT_NOT_REPORTED_DAYS: 5},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert calls and calls[0][BN_FIELD_DAYS_LAST_REPORTED] == 5


async def test_startup_does_not_check_when_disabled(hass: HomeAssistant) -> None:
    # Default off: the glue must not call Battery Notes' check action.
    await async_setup_fake_home_keeper(hass)
    calls: list[dict] = []

    async def _handler(call):
        calls.append(dict(call.data))

    hass.services.async_register(BN_DOMAIN, BN_SERVICE_CHECK_LAST_REPORTED, _handler)
    await _setup_glue(hass)

    assert calls == []


async def test_remove_entry_deletes_only_our_tasks(hass: HomeAssistant) -> None:
    hk = await async_setup_fake_home_keeper(hass)
    entry = await _setup_glue(hass)
    await _fire_threshold(hass, low=True)
    ours_id = hk.get_task_by_source(DOMAIN, device_id=DEVICE)["id"]
    hk.tasks["foreign"] = {"id": "foreign", "source": {"other": {"x": 1}}, "next_due": None}

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert ours_id not in hk.tasks      # our task cleaned up on removal
    assert "foreign" in hk.tasks        # someone else's task untouched


async def test_remove_entry_tolerates_list_tasks_error(hass: HomeAssistant) -> None:
    await async_setup_fake_home_keeper(hass)
    entry = await _setup_glue(hass)

    async def _boom(call):
        raise RuntimeError("list_tasks exploded")

    # Override the fake's list_tasks with one that raises; removal must not propagate.
    hass.services.async_register(
        HK_DOMAIN, "list_tasks", _boom, supports_response=SupportsResponse.ONLY
    )
    await hass.config_entries.async_remove(entry.entry_id)  # must not raise
    await hass.async_block_till_done()


async def test_options_flow_offers_the_rechargeable_modes(hass: HomeAssistant) -> None:
    # The options form renders and round-trips. Worth asserting directly: a schema
    # this rich is only otherwise exercised by a user opening the Configure dialog.
    await async_setup_fake_home_keeper(hass)
    entry = await _setup_glue(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    defaults = {
        key.schema: key.default() for key in result["data_schema"].schema if key.default
    }
    assert defaults[OPT_RECHARGEABLE_MODE] == "skip"  # untouched entry → the default
    assert defaults[OPT_CHARGE_NAME_TEMPLATE] == "Charge battery: {device_name}"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={**defaults, OPT_RECHARGEABLE_MODE: "charge"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[OPT_RECHARGEABLE_MODE] == "charge"
    assert entry.runtime_data._rechargeable_mode == "charge"


async def test_options_flow_opens_on_the_migrated_legacy_mode(
    hass: HomeAssistant,
) -> None:
    # An entry still carrying the old boolean must open on the mode it's actually
    # getting, not on the default — otherwise saving anything silently changes it.
    await async_setup_fake_home_keeper(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={OPT_SKIP_RECHARGEABLE: False}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)

    default = next(
        key.default()
        for key in result["data_schema"].schema
        if key.schema == OPT_RECHARGEABLE_MODE
    )
    assert default == "charge"

def _make_bn_battery_device(
    hass: HomeAssistant,
    *,
    unique: str,
    low: str,
    attributes: dict | None = None,
    level: str | None = None,
) -> str:
    """Register a full Battery Notes device: low binary sensor + level sensor.

    Mirrors how Battery Notes really splits a battery across two entities — the
    battery-low binary sensor carries type/quantity, the "battery plus" sensor carries
    the charge level. Returns the device id.
    """
    bn_entry = MockConfigEntry(domain=BN_DOMAIN, data={})
    bn_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=bn_entry.entry_id,
        identifiers={(BN_DOMAIN, unique)},
        name=f"{unique} device",
    )
    low_ent = er.async_get(hass).async_get_or_create(
        "binary_sensor", BN_DOMAIN, f"{unique}_low",
        device_id=device.id, original_device_class="battery",
    )
    hass.states.async_set(low_ent.entity_id, low, attributes or {})
    if level is not None:
        level_ent = er.async_get(hass).async_get_or_create(
            "sensor", BN_DOMAIN, f"{unique}_battery_plus",
            device_id=device.id, original_device_class="battery",
        )
        hass.states.async_set(level_ent.entity_id, level)
    return device.id


async def test_reconcile_reads_the_level_from_the_battery_plus_sensor(
    hass: HomeAssistant,
) -> None:
    # Battery Notes splits the battery across two entities: type/quantity sit on the
    # battery-low binary sensor, but the *level* only exists on the "battery plus"
    # sensor. Read only the first and a reconcile-created task silently loses the
    # "was at N%" note that a task created from a live event records — which every
    # rechargeable-mode switch now goes through.
    hk = await async_setup_fake_home_keeper(hass)
    entry = await _setup_glue(hass)

    device_id = _make_bn_battery_device(
        hass, unique="valve2", low="on",
        attributes={"battery_type": "AA", "battery_quantity": 2}, level="17",
    )

    await entry.runtime_data._reconcile()
    await hass.async_block_till_done()

    task = hk.get_task_by_source(DOMAIN, device_id=device_id)
    assert task is not None
    assert task["notes"] == "2× AA · was at 17%"


async def test_reconcile_omits_an_unavailable_battery_level(
    hass: HomeAssistant,
) -> None:
    # An unknown/unavailable level sensor must not leak "was at unavailable%" into the
    # notes — the rest of the description still stands on its own.
    hk = await async_setup_fake_home_keeper(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={OPT_RECHARGEABLE_MODE: "charge"}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device_id = _make_bn_battery_device(
        hass, unique="valve3", low="on",
        attributes={"battery_type": "Rechargeable", "battery_quantity": 1},
        level="unavailable",
    )

    await entry.runtime_data._reconcile()
    await hass.async_block_till_done()

    task = hk.get_task_by_source(DOMAIN, device_id=device_id)
    assert task is not None
    assert task["notes"] == "1× Rechargeable"


async def test_unload_after_start_does_not_double_remove_the_listener(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    # Setting up before HA has started registers a one-shot homeassistant_started
    # listener, which Home Assistant removes itself once it fires. Handing its canceller
    # straight to async_on_unload made the next unload — the first options change after
    # a restart — remove it a second time, logging an error with a traceback.
    await async_setup_fake_home_keeper(hass)
    hass.set_state(CoreState.not_running)
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    glue = entry.runtime_data
    assert glue._cancel_started is not None  # waiting for HA to finish starting

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert glue._cancel_started is None  # fired, so HA already removed it

    caplog.clear()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert "Unable to remove unknown job listener" not in caplog.text


async def test_unload_before_start_still_removes_the_listener(
    hass: HomeAssistant,
) -> None:
    # The other half of the contract: unloading while HA is still starting must
    # actually cancel the listener, so no reconcile runs for an entry that is gone.
    await async_setup_fake_home_keeper(hass)
    hass.set_state(CoreState.not_running)
    before = hass.bus.async_listeners().get(EVENT_HOMEASSISTANT_STARTED, 0)
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    glue = entry.runtime_data
    assert hass.bus.async_listeners()[EVENT_HOMEASSISTANT_STARTED] == before + 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert glue._cancel_started is None
    assert hass.bus.async_listeners().get(EVENT_HOMEASSISTANT_STARTED, 0) == before


async def test_reconcile_ignores_a_non_numeric_battery_level(
    hass: HomeAssistant,
) -> None:
    # unknown/unavailable is not the only thing that isn't a level: an upstream can put
    # any placeholder in a sensor state. Anything that doesn't parse as a finite number
    # is no level at all, or the task reads "was at --%".
    hk = await async_setup_fake_home_keeper(hass)
    entry = await _setup_glue(hass)

    device_id = _make_bn_battery_device(
        hass, unique="valve4", low="on",
        attributes={"battery_type": "AA", "battery_quantity": 2}, level="--",
    )

    await entry.runtime_data._reconcile()
    await hass.async_block_till_done()

    assert hk.get_task_by_source(DOMAIN, device_id=device_id)["notes"] == "2× AA"


async def test_reconcile_takes_the_lowest_of_two_level_sensors(
    hass: HomeAssistant,
) -> None:
    # A device with two battery notes carries two "battery plus" sensors. Report the
    # lowest — the one a low-battery task is about — rather than whichever the entity
    # registry happened to yield last, which would differ run to run.
    hk = await async_setup_fake_home_keeper(hass)
    entry = await _setup_glue(hass)

    device_id = _make_bn_battery_device(
        hass, unique="valve5", low="on",
        attributes={"battery_type": "AA", "battery_quantity": 2}, level="9",
    )
    # Registered second, so "whichever came last" would answer 40 — the healthy pack.
    second = er.async_get(hass).async_get_or_create(
        "sensor", BN_DOMAIN, "valve5_second_battery_plus",
        device_id=device_id, original_device_class="battery",
    )
    hass.states.async_set(second.entity_id, "40")

    await entry.runtime_data._reconcile()
    await hass.async_block_till_done()

    assert hk.get_task_by_source(DOMAIN, device_id=device_id)["notes"] == (
        "2× AA · was at 9%"
    )


async def test_reconcile_ignores_a_level_sensor_with_no_low_sensor(
    hass: HomeAssistant,
) -> None:
    # Widening the walk past the binary_sensor guard must not make a level sensor a
    # signal in its own right: only the battery-low sensor says a battery needs
    # attention, so a device with just a level raises nothing.
    hk = await async_setup_fake_home_keeper(hass)
    entry = await _setup_glue(hass)

    bn_entry = MockConfigEntry(domain=BN_DOMAIN, data={})
    bn_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=bn_entry.entry_id,
        identifiers={(BN_DOMAIN, "orphan")},
        name="Orphan device",
    )
    ent = er.async_get(hass).async_get_or_create(
        "sensor", BN_DOMAIN, "orphan_battery_plus",
        device_id=device.id, original_device_class="battery",
    )
    hass.states.async_set(ent.entity_id, "3")

    await entry.runtime_data._reconcile()
    await hass.async_block_till_done()

    assert hk.get_task_by_source(DOMAIN, device_id=device.id) is None


async def test_reconcile_is_idempotent(hass: HomeAssistant) -> None:
    # Reconcile runs on every start and every options change, so a second pass over
    # unchanged state must be a no-op — not a second task, and not a re-arm that
    # rewrites next_due on a task already armed.
    hk = await async_setup_fake_home_keeper(hass)
    entry = await _setup_glue(hass)

    device_id = _make_bn_battery_device(
        hass, unique="valve6", low="on",
        attributes={"battery_type": "AA", "battery_quantity": 2}, level="12",
    )

    await entry.runtime_data._reconcile()
    await hass.async_block_till_done()
    first = dict(hk.get_task_by_source(DOMAIN, device_id=device_id))

    await entry.runtime_data._reconcile()
    await hass.async_block_till_done()

    ours = [t for t in hk.tasks.values() if (t.get("source") or {}).get(DOMAIN)]
    assert len(ours) == 1
    assert ours[0]["id"] == first["id"]
    assert ours[0]["next_due"] == first["next_due"]
    assert ours[0]["notes"] == first["notes"]
