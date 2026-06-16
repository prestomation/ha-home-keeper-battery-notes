"""Pure decision logic for the Battery Notes glue.

Given the current Home Keeper task list and a Battery Notes signal, decide what to
do — create/arm/clear a ``triggered`` task — without touching Home Assistant. This
mirrors the purity of ``home_keeper/reconcile.py``: every branch is a plain
transformation over dicts, so it is exhaustively unit-testable in isolation. The
HA-facing wiring (``wiring.py``) turns these decisions into service calls.

The whole design rests on Home Keeper's ``triggered`` task model:

* a battery going low → the task should be **armed** (due-now). If we've never seen
  this device, create the task (born armed); otherwise re-arm the existing dormant
  task with ``trigger_task`` (keeping its replacement history).
* a battery replaced / level recovered → **clear** the task with ``complete_task``,
  which records the replacement in history and returns the task to dormant.

Every decision is idempotent: arming an already-armed task or clearing an already
-dormant one is a no-op (we return ``None``), so repeated Battery Notes events and
startup reconciliation never create duplicates or loops.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import (
    COMPLETION_PROMPT,
    MANAGED_DISPLAY_NAME,
    MANAGED_ICON,
    SOURCE_NS,
)


# ── action descriptors (what wiring.py should do) ────────────────────────────
@dataclass(frozen=True)
class CreateTask:
    """Create a new triggered task for *device_id*, born armed (due-now)."""

    device_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ArmTask:
    """Re-arm an existing dormant task (call ``home_keeper.trigger_task``)."""

    task_id: str
    device_id: str


@dataclass(frozen=True)
class ClearTask:
    """Clear an armed task (call ``home_keeper.complete_task``)."""

    task_id: str
    device_id: str


Action = CreateTask | ArmTask | ClearTask


# ── helpers over the Home Keeper task list ───────────────────────────────────
def task_for_device(tasks: list[dict], device_id: str) -> dict | None:
    """Return our task for *device_id* (matched by our ``source`` namespace), or None."""
    for task in tasks:
        src = (task.get("source") or {}).get(SOURCE_NS)
        if isinstance(src, dict) and src.get("device_id") == device_id:
            return task
    return None


def is_armed(task: dict) -> bool:
    """A triggered task is armed (due-now) when it has a ``next_due``; dormant otherwise."""
    return bool(task.get("next_due"))


def our_tasks(tasks: list[dict]) -> list[dict]:
    """Every task we own (carries our ``source`` namespace)."""
    return [t for t in tasks if isinstance((t.get("source") or {}).get(SOURCE_NS), dict)]


# ── payload construction ─────────────────────────────────────────────────────
def _format_name(name_template: str, device_name: str) -> str:
    """Render the task name from the configurable template, defensively.

    A user can mis-type the template (e.g. a stray ``{foo}``); fall back to the raw
    device name rather than raising and dropping the task.
    """
    try:
        return name_template.format(device_name=device_name)
    except (KeyError, IndexError, ValueError):
        return f"Replace battery: {device_name}"


def _format_notes(battery_type: Any, battery_quantity: Any, battery_level: Any) -> str:
    """Compact battery description for the task notes (best-effort, may be empty)."""
    bits: list[str] = []
    if battery_quantity and battery_type:
        bits.append(f"{battery_quantity}× {battery_type}")
    elif battery_type:
        bits.append(str(battery_type))
    if battery_level not in (None, ""):
        bits.append(f"was at {battery_level}%")
    return " · ".join(bits)


def build_add_task_payload(
    *,
    device_id: str,
    device_name: str,
    config_entry_id: str,
    name_template: str,
    battery_type: Any = None,
    battery_quantity: Any = None,
    battery_level: Any = None,
) -> dict[str, Any]:
    """The ``home_keeper.add_task`` payload for a new battery task (born armed).

    Carries a ``source`` namespaced to us (so we recognise it later) and a
    ``managed_by`` block so Home Keeper renders the "Managed by Battery Notes" chip,
    locks the name/device, shows the completion prompt, and protects deletion while
    we're installed (with ``config_entry_id`` so the protection lifts if we're
    removed). No schedule fields — it's a ``triggered`` task.
    """
    return {
        "name": _format_name(name_template, device_name),
        "notes": _format_notes(battery_type, battery_quantity, battery_level),
        "recurrence_type": "triggered",
        "device_id": device_id,
        "source": {SOURCE_NS: {"device_id": device_id}},
        "managed_by": {
            "integration": SOURCE_NS,
            "display_name": MANAGED_DISPLAY_NAME,
            "icon": MANAGED_ICON,
            "config_entry_id": config_entry_id,
            "deletion_protected": True,
            "completion_prompt": COMPLETION_PROMPT,
            "locked_fields": ["name", "device_id"],
        },
    }


# ── planners ─────────────────────────────────────────────────────────────────
def plan_battery_low(
    tasks: list[dict],
    *,
    device_id: str,
    device_name: str,
    config_entry_id: str,
    name_template: str,
    battery_type: Any = None,
    battery_quantity: Any = None,
    battery_level: Any = None,
) -> Action | None:
    """Decide what to do when *device_id* reports a low battery.

    Absent → create (born armed). Dormant → arm. Already armed → nothing.
    """
    task = task_for_device(tasks, device_id)
    if task is None:
        return CreateTask(
            device_id,
            build_add_task_payload(
                device_id=device_id,
                device_name=device_name,
                config_entry_id=config_entry_id,
                name_template=name_template,
                battery_type=battery_type,
                battery_quantity=battery_quantity,
                battery_level=battery_level,
            ),
        )
    if is_armed(task):
        return None
    return ArmTask(task["id"], device_id)


def plan_battery_cleared(tasks: list[dict], *, device_id: str) -> Action | None:
    """Decide what to do when *device_id*'s battery is replaced or recovers.

    Armed → clear (records a completion, goes dormant). Dormant/absent → nothing.
    """
    task = task_for_device(tasks, device_id)
    if task is None or not is_armed(task):
        return None
    return ClearTask(task["id"], device_id)


def plan_reconcile(
    tasks: list[dict],
    low_devices: dict[str, dict[str, Any]],
    *,
    config_entry_id: str,
    name_template: str,
) -> list[Action]:
    """Converge the full state at startup (catch up on signals missed while down).

    *low_devices* maps ``device_id`` → its info (name + optional battery fields) for
    every Battery Notes device currently reporting low. Every low device gets a
    created/armed task; every task we own whose device is **not** low and is still
    armed gets cleared. Idempotent no-ops are dropped.
    """
    actions: list[Action] = []
    for device_id, info in low_devices.items():
        action = plan_battery_low(
            tasks,
            device_id=device_id,
            device_name=info.get("name") or device_id,
            config_entry_id=config_entry_id,
            name_template=name_template,
            battery_type=info.get("battery_type"),
            battery_quantity=info.get("battery_quantity"),
            battery_level=info.get("battery_level"),
        )
        if action is not None:
            actions.append(action)

    low_ids = set(low_devices)
    for task in our_tasks(tasks):
        device_id = (task["source"][SOURCE_NS]).get("device_id")
        if device_id not in low_ids and is_armed(task):
            actions.append(ClearTask(task["id"], device_id))
    return actions
