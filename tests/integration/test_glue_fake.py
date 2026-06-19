"""Integration tests for the glue against Home Keeper's real test fake.

These exercise the full contract — event in → Home Keeper service call → task state
— using ``home_keeper.testing.async_setup_fake_home_keeper`` (the real model/event
code), plus a stub ``battery_notes.set_battery_replaced`` service to observe two-way
sync. They need a real HA test environment (pytest-homeassistant-custom-component).
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers import device_registry as dr, entity_registry as er
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
    OPT_CLEAR_ON_RECOVERY,
    OPT_NOT_REPORTED_DAYS,
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
