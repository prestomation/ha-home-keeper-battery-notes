"""Home Assistant wiring for the Battery Notes glue.

Turns the pure decisions in :mod:`logic` into Home Keeper service calls, and wires
Battery Notes events + a startup reconcile to drive them. Everything that crosses to
another integration is guarded with ``has_service`` so we degrade gracefully when
Home Keeper (or Battery Notes) isn't present.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_track_time_interval

from . import logic
from .const import (
    BN_BATTERY_LOW_DEVICE_CLASS,
    BN_DOMAIN,
    BN_EVENT_NOT_REPORTED,
    BN_EVENT_REPLACED,
    BN_EVENT_THRESHOLD,
    BN_FIELD_DAYS_LAST_REPORTED,
    BN_FIELD_RAISE_EVENTS,
    BN_SERVICE_CHECK_LAST_REPORTED,
    BN_SERVICE_SET_REPLACED,
    DEFAULT_CLEAR_ON_RECOVERY,
    DEFAULT_NAME_TEMPLATE,
    DEFAULT_NOT_REPORTED_DAYS,
    DEFAULT_SKIP_RECHARGEABLE,
    DEFAULT_TREAT_NOT_REPORTED,
    DEFAULT_TWO_WAY,
    FIELD_BATTERY_LEVEL,
    FIELD_BATTERY_LOW,
    FIELD_BATTERY_QUANTITY,
    FIELD_BATTERY_TYPE,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_NAME,
    FIELD_LAST_REPORTED_DAYS,
    DOMAIN,
    HK_DOMAIN,
    HK_EVENT_REGISTER_COMPANIONS,
    HK_EVENT_TASK_COMPLETED,
    HK_SERVICE_REGISTER_COMPANION,
    OPT_CLEAR_ON_RECOVERY,
    OPT_NAME_TEMPLATE,
    OPT_NOT_REPORTED_DAYS,
    OPT_SKIP_RECHARGEABLE,
    OPT_TREAT_NOT_REPORTED,
    OPT_TWO_WAY,
    ORIGIN,
    SOURCE_NS,
)

# How often the glue asks Battery Notes to re-check which batteries have stopped
# reporting. Battery Notes only computes "not reported" on demand (no continuous
# sensor), so we drive its check action on this cadence; a dead battery is days-scale
# news, so daily is plenty and cheap.
_NOT_REPORTED_SCAN_INTERVAL = timedelta(days=1)

_LOGGER = logging.getLogger(__name__)


class BatteryNotesGlue:
    """Listens to Battery Notes and drives Home Keeper triggered tasks."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        # Serialize the list-tasks → decide → execute span so two rapid Battery
        # Notes events (it re-fires on coordinator refresh) can't both read an empty
        # task list and each create a task. The glue is stateless, so this lock is
        # the only thing preventing a create/create interleave.
        self._lock = asyncio.Lock()

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

    @property
    def _treat_not_reported(self) -> bool:
        return self.entry.options.get(
            OPT_TREAT_NOT_REPORTED, DEFAULT_TREAT_NOT_REPORTED
        )

    @property
    def _not_reported_days(self) -> int:
        return int(
            self.entry.options.get(OPT_NOT_REPORTED_DAYS, DEFAULT_NOT_REPORTED_DAYS)
        )

    @property
    def _skip_rechargeable(self) -> bool:
        return self.entry.options.get(
            OPT_SKIP_RECHARGEABLE, DEFAULT_SKIP_RECHARGEABLE
        )

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
            bus.async_listen(BN_EVENT_NOT_REPORTED, self._on_not_reported)
        )
        self.entry.async_on_unload(
            bus.async_listen(HK_EVENT_TASK_COMPLETED, self._on_hk_completed)
        )
        # Announce this glue to Home Keeper's companion discovery so it shows up under
        # Home Keeper's Settings → Companions as a connected pairing (with a Configure
        # link back to our options). Register now, and again whenever Home Keeper asks
        # companions to re-announce (covers Home Keeper starting after us).
        self.entry.async_on_unload(
            bus.async_listen(HK_EVENT_REGISTER_COMPANIONS, self._on_register_request)
        )
        await self._register_companion()

        # Poll Battery Notes for batteries that have stopped reporting (opt-in). The
        # timer fires the check on a cadence; the resulting events flow to
        # _on_not_reported. Only armed when the option is on (setup re-runs on an
        # options change), so a disabled glue schedules nothing.
        if self._treat_not_reported:
            self.entry.async_on_unload(
                async_track_time_interval(
                    self.hass,
                    self._check_not_reported,
                    _NOT_REPORTED_SCAN_INTERVAL,
                )
            )

        # Reconcile once everything is up. after_dependencies only orders setup; it
        # doesn't guarantee Battery Notes' entities or Home Keeper's services exist
        # yet, so wait for HA-started (or run now if we're already past start-up).
        if self.hass.is_running:
            await self._reconcile()
            await self._check_not_reported()
        else:
            self.entry.async_on_unload(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, self._on_started
                )
            )

    async def _on_started(self, _event: Event) -> None:
        await self._reconcile()
        await self._check_not_reported()

    async def _on_register_request(self, _event: Event) -> None:
        await self._register_companion()

    async def _register_companion(self) -> None:
        """Announce this glue to Home Keeper's companion registry (best-effort)."""
        if not self._hk_ready(HK_SERVICE_REGISTER_COMPANION):
            return
        try:
            await self.hass.services.async_call(
                HK_DOMAIN,
                HK_SERVICE_REGISTER_COMPANION,
                {
                    "domain": DOMAIN,
                    "name": "Battery Notes",
                    "icon": "mdi:battery-alert-variant-outline",
                    "description": (
                        "Turns Battery Notes low-battery alerts into Home Keeper "
                        "“replace battery” tasks, kept in sync both ways."
                    ),
                    "config_entry_id": self.entry.entry_id,
                    "docs_url": (
                        "https://github.com/prestomation/ha-home-keeper-battery-notes"
                    ),
                    "capabilities": ["battery_replacement"],
                },
                blocking=False,
            )
        except Exception:  # noqa: BLE001 — discovery is best-effort; never break setup
            _LOGGER.debug("Home Keeper companion registration failed", exc_info=True)

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
        elif isinstance(action, logic.DeleteTask):
            if self._hk_ready("delete_task"):
                # ``force`` because our own tasks are deletion-protected while our
                # config entry resolves; we're retiring a task that shouldn't exist.
                await self.hass.services.async_call(
                    HK_DOMAIN,
                    "delete_task",
                    {"task_id": action.task_id, "force": True},
                    blocking=True,
                )
                _LOGGER.debug("Deleted battery task %s", action.task_id)

    # ── Battery Notes event handlers ─────────────────────────────────────────
    async def _on_threshold(self, event: Event) -> None:
        data = event.data
        device_id = data.get(FIELD_DEVICE_ID)
        if not device_id:
            return
        async with self._lock:
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
                    skip_rechargeable=self._skip_rechargeable,
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
        async with self._lock:
            tasks = await self._list_tasks()
            action = logic.plan_battery_cleared(tasks, device_id=device_id)
            if action is not None:
                await self._execute(action)

    async def _on_not_reported(self, event: Event) -> None:
        """A battery hasn't reported in a while → treat as suspected-dead, arm a task.

        Same create-or-arm decision as a low battery (keyed on the device), so a
        battery that was already low and then went dark just stays armed rather than
        spawning a second task. Guarded by the opt-in option.
        """
        if not self._treat_not_reported:
            return
        device_id = event.data.get(FIELD_DEVICE_ID)
        if not device_id:
            return
        async with self._lock:
            tasks = await self._list_tasks()
            action = logic.plan_battery_low(
                tasks,
                device_id=device_id,
                device_name=event.data.get(FIELD_DEVICE_NAME) or device_id,
                config_entry_id=self.entry.entry_id,
                name_template=self._name_template,
                battery_type=event.data.get(FIELD_BATTERY_TYPE),
                battery_quantity=event.data.get(FIELD_BATTERY_QUANTITY),
                reason="not_reported",
                last_reported_days=event.data.get(FIELD_LAST_REPORTED_DAYS),
                skip_rechargeable=self._skip_rechargeable,
            )
            if action is not None:
                await self._execute(action)

    async def _check_not_reported(self, _now: Any = None) -> None:
        """Ask Battery Notes which batteries have stopped reporting.

        Battery Notes computes "not reported" only on demand (no continuous sensor),
        raising a ``battery_notes_battery_not_reported`` event per matching device,
        which _on_not_reported turns into a task. A no-op unless the option is on and
        Battery Notes exposes the action.
        """
        if not self._treat_not_reported:
            return
        if not self.hass.services.has_service(
            BN_DOMAIN, BN_SERVICE_CHECK_LAST_REPORTED
        ):
            return
        try:
            await self.hass.services.async_call(
                BN_DOMAIN,
                BN_SERVICE_CHECK_LAST_REPORTED,
                {
                    BN_FIELD_DAYS_LAST_REPORTED: self._not_reported_days,
                    BN_FIELD_RAISE_EVENTS: True,
                },
                blocking=True,
            )
        except HomeAssistantError as err:
            _LOGGER.debug("check_battery_last_reported failed: %s", err)

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
            try:
                await self.hass.services.async_call(
                    BN_DOMAIN,
                    BN_SERVICE_SET_REPLACED,
                    {FIELD_DEVICE_ID: device_id},
                    blocking=True,
                )
            except HomeAssistantError as err:
                # Battery Notes rejects an unknown/removed device. Don't let a stale
                # task's mirror attempt bubble an exception out of the event listener.
                _LOGGER.debug(
                    "set_battery_replaced failed for device %s: %s", device_id, err
                )
                return
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
        async with self._lock:
            low_devices, recovered_devices, rechargeable_devices = (
                self._scan_battery_sensors()
            )
            tasks = await self._list_tasks()
            actions = logic.plan_reconcile(
                tasks,
                low_devices,
                recovered_devices,
                config_entry_id=self.entry.entry_id,
                name_template=self._name_template,
                skip_rechargeable=self._skip_rechargeable,
                rechargeable_devices=rechargeable_devices,
            )
            for action in actions:
                # Honour the clear-on-recovery option during reconcile too: skip clears
                # for recovered batteries if the user disabled automatic recovery.
                if isinstance(action, logic.ClearTask) and not self._clear_on_recovery:
                    continue
                await self._execute(action)
            if actions:
                _LOGGER.debug("Reconcile applied %d action(s)", len(actions))

    def _scan_battery_sensors(
        self,
    ) -> tuple[dict[str, dict[str, Any]], set[str], frozenset[str]]:
        """Snapshot Battery Notes' battery-low sensors for a reconcile.

        Returns ``(low, recovered, rechargeable)``: *low* maps ``device_id`` → battery
        info for sensors reading ``on`` (arm/create), *recovered* is the set of devices
        whose sensor reads ``off`` — an affirmative "reporting and not low" signal we
        clear on — and *rechargeable* is the set of devices whose battery type is
        rechargeable (any state), so a reconcile can retire their tasks regardless of
        whether they're currently low, recovered, or silent. A sensor that's
        ``unknown``/``unavailable`` (or absent) lands in neither *low* nor *recovered*:
        that's the suspected-dead case, so we neither arm from it here (the
        not-reported path, with its day threshold, handles that) nor clear on it.
        """
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        low: dict[str, dict[str, Any]] = {}
        recovered: set[str] = set()
        rechargeable: set[str] = set()
        for entity in ent_reg.entities.values():
            if entity.platform != BN_DOMAIN or entity.domain != "binary_sensor":
                continue
            if (entity.device_class or entity.original_device_class) != BN_BATTERY_LOW_DEVICE_CLASS:
                continue
            if not entity.device_id:
                continue
            state = self.hass.states.get(entity.entity_id)
            if state is None:
                continue
            # Battery Notes carries battery_type as an attribute regardless of the
            # low sensor's on/off/unknown state, so we can classify rechargeables even
            # for a device that has since recovered or gone silent.
            if logic.is_rechargeable(state.attributes.get(FIELD_BATTERY_TYPE)):
                rechargeable.add(entity.device_id)
            if state.state == "off":
                recovered.add(entity.device_id)
                continue
            if state.state != "on":
                continue
            device = dev_reg.async_get(entity.device_id)
            name = (device.name_by_user or device.name) if device else None
            # Battery Notes exposes battery_type/quantity/level as attributes on the
            # battery-low sensor, so a reconcile-created task gets the same notes as
            # one created from a live event (rather than an empty note).
            attrs = state.attributes
            low[entity.device_id] = {
                "name": name or entity.device_id,
                "battery_type": attrs.get(FIELD_BATTERY_TYPE),
                "battery_quantity": attrs.get(FIELD_BATTERY_QUANTITY),
                "battery_level": attrs.get(FIELD_BATTERY_LEVEL),
            }
        return low, recovered, frozenset(rechargeable)
