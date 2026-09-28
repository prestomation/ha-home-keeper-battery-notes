"""Home Keeper ↔ Battery Notes glue integration.

Creates a Home Keeper ``triggered`` task when a Battery Notes battery goes low and
clears it (recording the replacement) when it's replaced or recovers — so a low
battery surfaces as a maintenance task and the two surfaces behave as one button.

The integration is stateless: it persists no device→task mapping (foreign device
ids can change). Everything is re-derived from Home Keeper's ``list_tasks`` (matched
by our ``source`` namespace) plus Battery Notes' registry entities, so it self-heals
across restarts. See the design in ha-home-keeper/docs/BATTERY_NOTES_PLAN.md.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import logic
from .const import (
    HK_DOMAIN,
    HK_SERVICE_DELETE_ASSET,
    HK_SERVICE_LIST_ASSETS,
    HK_SERVICE_UPDATE_ASSET,
    SOURCE_NS,
)
from .wiring import BatteryNotesGlue

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the glue from its config entry."""
    glue = BatteryNotesGlue(hass, entry)
    await glue.async_setup()
    entry.runtime_data = glue
    # Re-run setup (re-subscribe + reconcile) when options change.
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the entry. Event listeners are removed via ``entry.async_on_unload``.

    Deliberately does NOT delete the tasks we created: a transient unload/reload
    (e.g. an options change) must not wipe a household's battery history. Permanent
    cleanup happens in ``async_remove_entry`` when the integration is truly removed;
    until then Home Keeper's orphan detection lifts deletion protection so the user
    can clean up if they uninstall us.
    """
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """On permanent removal, delete the tasks we own and hand the appliance back.

    Best-effort and guarded: if Home Keeper is gone, its orphan detection already
    lets the user remove our (now unprotected) tasks. We pass ``force`` because our
    own tasks are deletion-protected while our config entry still resolves.
    """
    await _remove_battery_appliance(hass)
    if not hass.services.has_service(HK_DOMAIN, "list_tasks"):
        return
    try:
        resp = await hass.services.async_call(
            HK_DOMAIN, "list_tasks", {}, blocking=True, return_response=True
        )
    except Exception:
        _LOGGER.debug("Could not list Home Keeper tasks during removal", exc_info=True)
        return
    for task in (resp or {}).get("tasks", []):
        src = (task.get("source") or {}).get(SOURCE_NS)
        if isinstance(src, dict) and hass.services.has_service(HK_DOMAIN, "delete_task"):
            try:
                await hass.services.async_call(
                    HK_DOMAIN,
                    "delete_task",
                    {"task_id": task["id"], "force": True},
                    blocking=True,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Failed to delete task %s on removal", task.get("id"))


async def _remove_battery_appliance(hass: HomeAssistant) -> None:
    """Hand the battery appliance back to the user, or remove an empty one.

    A part the user counted holds spares that are still in the drawer, so the
    appliance stays and only the ownership goes: Home Keeper unlocks the name and the
    part list, and the user keeps a plain appliance with the counts. An appliance
    whose parts track no stock holds nothing to keep, so it goes with us.
    """
    if not hass.services.has_service(HK_DOMAIN, HK_SERVICE_LIST_ASSETS):
        return
    try:
        resp = await hass.services.async_call(
            HK_DOMAIN, HK_SERVICE_LIST_ASSETS, {}, blocking=True, return_response=True
        )
        asset = logic.find_our_asset(list((resp or {}).get("assets", [])))
        if asset is None:
            return
        counted = any(
            part.get("stock") is not None for part in asset.get("parts") or []
        )
        if counted:
            if hass.services.has_service(HK_DOMAIN, HK_SERVICE_UPDATE_ASSET):
                await hass.services.async_call(
                    HK_DOMAIN,
                    HK_SERVICE_UPDATE_ASSET,
                    {"asset_id": asset["id"], "managed_by": None},
                    blocking=True,
                )
        elif hass.services.has_service(HK_DOMAIN, HK_SERVICE_DELETE_ASSET):
            await hass.services.async_call(
                HK_DOMAIN,
                HK_SERVICE_DELETE_ASSET,
                {"asset_id": asset["id"], "force": True},
                blocking=True,
            )
    except Exception:
        _LOGGER.debug("Could not release the battery appliance", exc_info=True)
