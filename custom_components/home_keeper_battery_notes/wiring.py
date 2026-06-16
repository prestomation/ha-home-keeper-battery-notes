"""Home Assistant wiring for the Battery Notes glue.

Turns the pure decisions in :mod:`logic` into Home Keeper service calls, and wires
Battery Notes events + a startup reconcile to drive them. Everything that crosses to
another integration is guarded with ``has_service`` so we degrade gracefully when
Home Keeper (or Battery Notes) isn't present.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from . import logic
from .const import (
    BN_BATTERY_LOW_DEVICE_CLASS,
    BN_DOMAIN,
    BN_EVENT_REPLACED,
    BN_EVENT_THRESHOLD,
    BN_SERVICE_SET_REPLACED,
    DEFAULT_CLEAR_ON_RECOVERY,
    DEFAULT_NAME_TEMPLATE,
    DEFAULT_TWO_WAY,
    FIELD_BATTERY_LEVEL,
    FIELD_BATTERY_LOW,
    FIELD_BATTERY_QUANTITY,
    FIELD_BATTERY_TYPE,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_NAME,
    HK_DOMAIN,
    HK_EVENT_TASK_COMPLETED,
    OPT_CLEAR_ON_RECOVERY,
    OPT_NAME_TEMPLATE,
    OPT_TWO_WAY,
    ORIGIN,
    SOURCE_NS,
)

_LOGGER = logging.getLogger(__name__)


class BatteryNotesGlue:
    """Listens to Battery Notes and drives Home Keeper triggered tasks."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

    # ── options ──────────────────────────────────────────────────────────────
    @property
    def _name_template(self) -> str:
        return self.entry.options.get(OPT_NAME_TEMPLATE, DEFAULT_NAME_TEMPLATE)

    @property
    def _two_way(self) -> bool:
        return self.entry.options.get(OPT_TWO_WAY, DEFAULT_TWO_WAY)

    @property
    def _clear_on_recovery(self) -> bool:
        return self.entry.options.get(OPT_CLEAR_ON_RECOVERY, DEFAULT_CLEAR_ON_RECOVERY)

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def async_setup(self) -> None:
        """Subscribe to events and schedule the startup reconcile."""
        bus = self.hass.bus
        self.entry.async_on_unload(
            bus.async_listen(BN_EVENT_THRESHOLD, self._on_threshold)
        )
        self.entry.async_on_unload(
            bus.async_listen(BN_EVENT_REPLACED, self._on_replaced)
        )
        self.entry.async_on_unload(
            bus.async_listen(HK_EVENT_TASK_COMPLETED, self._on_hk_completed)
        )

        # Reconcile once everything is up. after_dependencies only orders setup; it
        # doesn't guarantee Battery Notes' entities or Home Keeper's services exist
        # yet, so wait for HA-started (or run now if we're already past start-up).
        if self.hass.is_running:
            await self._reconcile()
        else:
            self.entry.async_on_unload(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, self._on_started
                )
            )

    async def _on_started(self, _event: Event) -> None:
        await self._reconcile()

    # ── Home Keeper helpers ──────────────────────────────────────────────────
    def _hk_ready(self, service: str) -> bool:
        return self.hass.services.has_service(HK_DOMAIN, service)

    async def _list_tasks(self) -> list[dict[str, Any]]:
        if not self._hk_ready("list_tasks"):
            return []
        resp = await self.hass.services.async_call(
            HK_DOMAIN, "list_tasks", {}, blocking=True, return_response=True
        )
        return list((resp or {}).get("tasks", []))

    async def _execute(self, action: logic.Action) -> None:
        if isinstance(action, logic.CreateTask):
            if self._hk_ready("add_task"):
                await self.hass.services.async_call(
                    HK_DOMAIN, "add_task", action.payload, blocking=True
                )
                _LOGGER.debug("Created battery task for device %s", action.device_id)
        elif isinstance(action, logic.ArmTask):
            if self._hk_ready("trigger_task"):
                await self.hass.services.async_call(
                    HK_DOMAIN, "trigger_task", {"task_id": action.task_id}, blocking=True
                )
                _LOGGER.debug("Armed battery task %s", action.task_id)
        elif isinstance(action, logic.ClearTask):
            if self._hk_ready("complete_task"):
                await self.hass.services.async_call(
                    HK_DOMAIN,
                    "complete_task",
                    {"task_id": action.task_id, "origin": ORIGIN},
                    blocking=True,
                )
                _LOGGER.debug("Cleared battery task %s", action.task_id)

    # ── Battery Notes event handlers ─────────────────────────────────────────
    async def _on_threshold(self, event: Event) -> None:
        data = event.data
        device_id = data.get(FIELD_DEVICE_ID)
        if not device_id:
            return
        tasks = await self._list_tasks()
        if data.get(FIELD_BATTERY_LOW):
            action = logic.plan_battery_low(
                tasks,
                device_id=device_id,
                device_name=data.get(FIELD_DEVICE_NAME) or device_id,
                config_entry_id=self.entry.entry_id,
                name_template=self._name_template,
                battery_type=data.get(FIELD_BATTERY_TYPE),
                battery_quantity=data.get(FIELD_BATTERY_QUANTITY),
                battery_level=data.get(FIELD_BATTERY_LEVEL),
            )
        elif self._clear_on_recovery:
            action = logic.plan_battery_cleared(tasks, device_id=device_id)
        else:
            action = None
        if action is not None:
            await self._execute(action)

    async def _on_replaced(self, event: Event) -> None:
        device_id = event.data.get(FIELD_DEVICE_ID)
        if not device_id:
            return
        tasks = await self._list_tasks()
        action = logic.plan_battery_cleared(tasks, device_id=device_id)
        if action is not None:
            await self._execute(action)

    async def _on_hk_completed(self, event: Event) -> None:
        """Two-way sync: mirror a Home-Keeper-side completion to Battery Notes.

        Only for *our* tasks, and only completions we did NOT initiate (origin guard).
        We just push "replaced" to Battery Notes — Home Keeper has already recorded the
        completion and set the task dormant, so we must not re-complete or re-arm
        (that would loop). See Home Keeper INTEGRATING.md §4.
        """
        if not self._two_way:
            return
        if event.data.get("origin") == ORIGIN:
            return  # the echo of a completion we triggered
        src = (event.data.get("source") or {}).get(SOURCE_NS)
        if not isinstance(src, dict):
            return  # not one of our tasks
        device_id = src.get("device_id")
        if not device_id:
            return
        if self.hass.services.has_service(BN_DOMAIN, BN_SERVICE_SET_REPLACED):
            await self.hass.services.async_call(
                BN_DOMAIN,
                BN_SERVICE_SET_REPLACED,
                {FIELD_DEVICE_ID: device_id},
                blocking=True,
            )
            _LOGGER.debug("Mirrored completion to Battery Notes for device %s", device_id)

    # ── startup reconcile ────────────────────────────────────────────────────
    async def _reconcile(self) -> None:
        """Catch up on signals missed while we were down.

        Enumerate Battery Notes' battery-low binary sensors from the entity registry
        (robust to entity_id renames) and converge tasks to match: arm/create for
        devices currently low, clear tasks whose device is no longer low.
        """
        if not self._hk_ready("list_tasks"):
            return
        low_devices = self._current_low_devices()
        tasks = await self._list_tasks()
        actions = logic.plan_reconcile(
            tasks,
            low_devices,
            config_entry_id=self.entry.entry_id,
            name_template=self._name_template,
        )
        for action in actions:
            # Honour the clear-on-recovery option during reconcile too: skip clears
            # for recovered batteries if the user disabled automatic recovery.
            if isinstance(action, logic.ClearTask) and not self._clear_on_recovery:
                continue
            await self._execute(action)
        if actions:
            _LOGGER.debug("Reconcile applied %d action(s)", len(actions))

    def _current_low_devices(self) -> dict[str, dict[str, Any]]:
        """Battery Notes devices currently reporting low, keyed by device_id."""
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        low: dict[str, dict[str, Any]] = {}
        for entity in ent_reg.entities.values():
            if entity.platform != BN_DOMAIN or entity.domain != "binary_sensor":
                continue
            if (entity.device_class or entity.original_device_class) != BN_BATTERY_LOW_DEVICE_CLASS:
                continue
            if not entity.device_id:
                continue
            state = self.hass.states.get(entity.entity_id)
            if state is None or state.state != "on":
                continue
            device = dev_reg.async_get(entity.device_id)
            name = (device.name_by_user or device.name) if device else None
            low[entity.device_id] = {"name": name or entity.device_id}
        return low
