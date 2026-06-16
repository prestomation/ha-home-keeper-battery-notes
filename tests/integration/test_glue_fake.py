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
    BN_EVENT_REPLACED,
    BN_EVENT_THRESHOLD,
    BN_SERVICE_SET_REPLACED,
    DOMAIN,
    HK_DOMAIN,
    OPT_CLEAR_ON_RECOVERY,
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
    # With clear_on_recovery off, a startup reconcile (no Battery Notes entities, so
    # the device reads as "not low") must NOT clear an armed task.
    hk = await async_setup_fake_home_keeper(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={OPT_CLEAR_ON_RECOVERY: False}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _fire_threshold(hass, low=True)
    assert hk.get_task_by_source(DOMAIN, device_id=DEVICE)["next_due"]  # armed

    await entry.runtime_data._reconcile()
    await hass.async_block_till_done()

    assert hk.get_task_by_source(DOMAIN, device_id=DEVICE)["next_due"]  # still armed


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
