"""Integration tests for the glue against Home Keeper's real test fake.

These exercise the full contract — event in → Home Keeper service call → task state
— using ``home_keeper.testing.async_setup_fake_home_keeper`` (the real model/event
code), plus a stub ``battery_notes.set_battery_replaced`` service to observe two-way
sync. They need a real HA test environment (pytest-homeassistant-custom-component).
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.home_keeper_battery_notes.const import (
    BN_DOMAIN,
    BN_EVENT_REPLACED,
    BN_EVENT_THRESHOLD,
    BN_SERVICE_SET_REPLACED,
    DOMAIN,
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
