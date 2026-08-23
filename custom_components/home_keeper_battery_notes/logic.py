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
    CHARGE_CHIP_ICON,
    CHARGE_COMPLETION_PROMPT,
    CHIP_ICON,
    COMPLETION_PROMPT,
    DEFAULT_CHARGE_NAME_TEMPLATE,
    DEFAULT_NAME_TEMPLATE,
    DEFAULT_RECHARGEABLE_MODE,
    KIND_CHARGE,
    KIND_REPLACE,
    MANAGED_DISPLAY_NAME,
    MANAGED_ICON,
    OPT_RECHARGEABLE_MODE,
    OPT_SKIP_RECHARGEABLE,
    RECHARGEABLE_BATTERY_TYPE,
    RECHARGEABLE_MODE_CHARGE,
    RECHARGEABLE_MODE_SKIP,
    RECHARGEABLE_MODES,
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


@dataclass(frozen=True)
class DeleteTask:
    """Remove a task entirely (call ``home_keeper.delete_task`` with ``force``).

    Used to retire a task that should never have existed — e.g. a rechargeable
    device's replace-battery task when ``skip_rechargeable`` is on. Unlike
    :class:`ClearTask` it records no completion (a phantom replacement) and leaves
    nothing lingering in Home Keeper's "Monitored" list.
    """

    task_id: str
    device_id: str


@dataclass(frozen=True)
class RecreateTask:
    """Retire a task and create it afresh from *payload* (delete + ``add_task``).

    The escape hatch for changing a field Home Keeper will not let us edit. ``name`` is
    in our ``managed_by.locked_fields``, and Home Keeper strips locked fields from
    *every* ``update_task`` payload — the owning integration's included — so turning a
    "Replace battery: …" task into a "Charge battery: …" one (or back) cannot be a
    rename. The device's completion history goes with the old task; for a rechargeable
    that history is the phantom "replacements" the wrong task kind was recording.
    """

    task_id: str
    device_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class UpdateChips:
    """Update ``task_chips`` on an existing task when the battery spec becomes known.

    Emitted during reconcile when a task was created before battery_type was
    available (chips=[]) but the Battery Notes entity now exposes it.
    """

    task_id: str
    device_id: str
    chips: list[dict[str, str]]


Action = CreateTask | ArmTask | ClearTask | DeleteTask | RecreateTask | UpdateChips


# ── helpers over the Home Keeper task list ───────────────────────────────────
def _is_ours(task: Any) -> bool:
    """Whether *task* is a well-formed task dict we own (has an id + our source ns)."""
    if not isinstance(task, dict) or not task.get("id"):
        return False
    return isinstance((task.get("source") or {}).get(SOURCE_NS), dict)


def task_for_device(tasks: list[dict], device_id: str) -> dict | None:
    """Return our task for *device_id* (matched by our ``source`` namespace), or None."""
    for task in tasks:
        if _is_ours(task) and task["source"][SOURCE_NS].get("device_id") == device_id:
            return task
    return None


def is_armed(task: dict) -> bool:
    """A triggered task is armed (due-now) when it has a ``next_due``; dormant otherwise."""
    return bool(task.get("next_due"))


def is_rechargeable(battery_type: Any) -> bool:
    """Whether *battery_type* names a rechargeable battery (Battery Notes' label).

    A rechargeable's low charge means "charge it", not "replace it", so the configured
    ``rechargeable_mode`` — not the disposable-cell default — decides what these get.
    Matched case-insensitively as a substring to tolerate library variants.
    """
    return (
        isinstance(battery_type, str)
        and RECHARGEABLE_BATTERY_TYPE in battery_type.strip().lower()
    )


def task_kind(task: dict) -> str:
    """What kind of task this is — ``KIND_REPLACE`` or ``KIND_CHARGE``.

    Read from the ``kind`` we stamp into our own ``source`` namespace. A task created
    before kinds existed carries none and is a replace task by definition, so that's
    the fallback (and what makes the conversion in ``plan_reconcile`` fire once).
    """
    src = (task.get("source") or {}).get(SOURCE_NS)
    kind = src.get("kind") if isinstance(src, dict) else None
    return kind if kind in (KIND_REPLACE, KIND_CHARGE) else KIND_REPLACE


def our_tasks(tasks: list[dict]) -> list[dict]:
    """Every well-formed task we own (carries our ``source`` namespace + an id)."""
    return [t for t in tasks if _is_ours(t)]


# ── options ──────────────────────────────────────────────────────────────────
def resolve_rechargeable_mode(options: Any) -> str:
    """Read the rechargeable mode out of an entry's options, honouring the old key.

    ``rechargeable_mode`` replaced a ``skip_rechargeable`` boolean. An entry saved
    before the rename still carries only the boolean, and its two positions map onto
    the modes exactly: ``True`` meant "raise nothing" (``skip``), ``False`` meant
    "a low rechargeable is a task" — which is a *charge* task now that we can say so
    (ha-home-keeper-battery-notes#18). Shared by the wiring and the options form so
    the form opens on the mode the user is actually getting. Read-only: the old key is
    never written back, and disappears the first time the form is saved.
    """
    mode = (options or {}).get(OPT_RECHARGEABLE_MODE)
    if mode in RECHARGEABLE_MODES:
        return str(mode)
    if (options or {}).get(OPT_SKIP_RECHARGEABLE) is False:
        return RECHARGEABLE_MODE_CHARGE
    return DEFAULT_RECHARGEABLE_MODE


# ── payload construction ─────────────────────────────────────────────────────
def _format_name(name_template: str, device_name: str, *, kind: str) -> str:
    """Render the task name from the configurable template, defensively.

    A user can mis-type the template (e.g. a stray ``{foo}``); fall back to the default
    for this *kind* rather than raising and dropping the task.
    """
    try:
        return name_template.format(device_name=device_name)
    except (KeyError, IndexError, ValueError):
        fallback = (
            DEFAULT_CHARGE_NAME_TEMPLATE
            if kind == KIND_CHARGE
            else DEFAULT_NAME_TEMPLATE
        )
        return fallback.format(device_name=device_name)


def _format_notes(
    battery_type: Any,
    battery_quantity: Any,
    battery_level: Any,
    *,
    reason: str = "low",
    last_reported_days: Any = None,
) -> str:
    """Compact battery description for the task notes (best-effort, may be empty).

    *reason* tailors the why: a ``"low"`` battery records the level it was at; a
    ``"not_reported"`` (suspected-dead) one records how long it's been dark, so the
    task explains itself at a glance rather than looking like a normal low battery.
    """
    bits: list[str] = []
    if battery_quantity and battery_type:
        bits.append(f"{battery_quantity}× {battery_type}")
    elif battery_type:
        bits.append(str(battery_type))
    if reason == "not_reported":
        if last_reported_days not in (None, ""):
            bits.append(f"not reporting for {last_reported_days} days")
        else:
            bits.append("not reporting")
    elif battery_level not in (None, ""):
        bits.append(f"was at {battery_level}%")
    return " · ".join(bits)


def build_battery_chip(
    battery_type: Any,
    battery_quantity: Any,
    *,
    kind: str = KIND_REPLACE,
) -> dict[str, str] | None:
    """Build a Home Keeper task chip for the battery spec, or ``None`` if unknown.

    Returns ``{"label": "2× AAA", "icon": "mdi:battery"}`` when the battery type is
    known. ``battery_quantity`` is incorporated when present (e.g. ``2× AAA``), and a
    charge task gets the charging icon so the two kinds read apart at a glance.
    Returns ``None`` when battery_type is absent or blank so callers can omit the
    chip rather than rendering an empty label.
    """
    if not battery_type:
        return None
    label = (
        f"{battery_quantity}× {battery_type}" if battery_quantity else str(battery_type)
    )
    icon = CHARGE_CHIP_ICON if kind == KIND_CHARGE else CHIP_ICON
    return {"label": label, "icon": icon}


def build_add_task_payload(
    *,
    device_id: str,
    device_name: str,
    config_entry_id: str,
    name_template: str,
    battery_type: Any = None,
    battery_quantity: Any = None,
    battery_level: Any = None,
    reason: str = "low",
    last_reported_days: Any = None,
    kind: str = KIND_REPLACE,
) -> dict[str, Any]:
    """The ``home_keeper.add_task`` payload for a new battery task (born armed).

    Carries a ``source`` namespaced to us (so we recognise it later, and carrying the
    *kind* so we can tell a charge task from a replace one) and a ``managed_by`` block
    so Home Keeper renders the "Managed by Battery Notes" chip, locks the name/device,
    shows the completion prompt, and protects deletion while we're installed (with
    ``config_entry_id`` so the protection lifts if we're removed). No schedule fields —
    it's a ``triggered`` task.

    *name_template* must already be the one for this *kind*; the caller picks it.
    """
    chip = build_battery_chip(battery_type, battery_quantity, kind=kind)
    return {
        "name": _format_name(name_template, device_name, kind=kind),
        "notes": _format_notes(
            battery_type,
            battery_quantity,
            battery_level,
            reason=reason,
            last_reported_days=last_reported_days,
        ),
        "recurrence_type": "triggered",
        "device_id": device_id,
        "source": {SOURCE_NS: {"device_id": device_id, "kind": kind}},
        "task_chips": [chip] if chip else [],
        "managed_by": {
            "integration": SOURCE_NS,
            "display_name": MANAGED_DISPLAY_NAME,
            "icon": MANAGED_ICON,
            "config_entry_id": config_entry_id,
            "deletion_protected": True,
            "completion_prompt": (
                CHARGE_COMPLETION_PROMPT if kind == KIND_CHARGE else COMPLETION_PROMPT
            ),
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
    reason: str = "low",
    last_reported_days: Any = None,
    charge_name_template: str = DEFAULT_CHARGE_NAME_TEMPLATE,
    rechargeable_mode: str = DEFAULT_RECHARGEABLE_MODE,
) -> Action | None:
    """Decide what to do when *device_id*'s battery needs attention.

    Drives both signals — a battery crossing the *low* threshold and one that's
    stopped reporting (``reason="not_reported"``, suspected dead) — into the same
    create-or-arm decision keyed on the device, so a battery that's low and then goes
    dark never produces a second task. Absent → create (born armed). Dormant → arm.
    Already armed → nothing.

    A *rechargeable* battery is routed by ``rechargeable_mode``: ``skip`` raises nothing
    and *deletes* any task the device already has (so enabling it, or upgrading into it,
    retires a stale one); ``charge`` raises a charge task named from
    *charge_name_template*; ``replace`` treats it exactly like a disposable. A task
    whose kind no longer matches is recreated, since its name cannot be edited.
    """
    task = task_for_device(tasks, device_id)
    rechargeable = is_rechargeable(battery_type)
    if rechargeable and rechargeable_mode == RECHARGEABLE_MODE_SKIP:
        return DeleteTask(task["id"], device_id) if task is not None else None

    charging = rechargeable and rechargeable_mode == RECHARGEABLE_MODE_CHARGE
    kind = KIND_CHARGE if charging else KIND_REPLACE
    # The task we already have is the right kind: the cheap arm-or-nothing path, no
    # payload to build.
    if task is not None and task_kind(task) == kind:
        return None if is_armed(task) else ArmTask(task["id"], device_id)

    payload = build_add_task_payload(
        device_id=device_id,
        device_name=device_name,
        config_entry_id=config_entry_id,
        name_template=charge_name_template if charging else name_template,
        battery_type=battery_type,
        battery_quantity=battery_quantity,
        battery_level=battery_level,
        reason=reason,
        last_reported_days=last_reported_days,
        kind=kind,
    )
    if task is None:
        return CreateTask(device_id, payload)
    return RecreateTask(task["id"], device_id, payload)


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
    recovered_devices: set[str],
    *,
    config_entry_id: str,
    name_template: str,
    charge_name_template: str = DEFAULT_CHARGE_NAME_TEMPLATE,
    rechargeable_mode: str = DEFAULT_RECHARGEABLE_MODE,
    rechargeable_devices: frozenset[str] = frozenset(),
) -> list[Action]:
    """Converge the full state at startup (catch up on signals missed while down).

    *low_devices* maps ``device_id`` → its info (name + optional battery fields) for
    every Battery Notes device currently reporting low; each gets a created/armed task.

    Clearing is **affirmative**: we only clear an armed task whose device is in
    *recovered_devices* — a battery that's actually reporting a not-low level again
    (its low sensor reads ``off``). A device that's merely absent/unknown/unavailable
    is *not* treated as recovered: that's exactly the suspected-dead case, and
    clearing it would record a phantom replacement (and fight the not-reported path).
    Idempotent no-ops are dropped.

    *rechargeable_devices* holds the device ids whose battery is rechargeable regardless
    of current low state, so a rechargeable's task can be dealt with even when its device
    has since recovered or gone silent — cases the low/recovered passes can't see. Under
    ``skip`` any such task is *deleted*; under ``charge``/``replace`` a task of the
    *wrong kind* is deleted, unless the device is currently low, in which case the low
    pass below recreates it in one step (a task must never be re-created armed for a
    device that isn't actually low).
    """
    actions: list[Action] = []

    # Deal with rechargeable devices first. Track the ones fully settled here so the
    # later passes don't also act on the same device.
    handled: set[str] = set()
    for task in our_tasks(tasks):
        device_id = (task["source"][SOURCE_NS]).get("device_id")
        if device_id not in rechargeable_devices:
            continue
        if rechargeable_mode == RECHARGEABLE_MODE_SKIP:
            actions.append(DeleteTask(task["id"], device_id))
            handled.add(device_id)
            continue
        wanted = (
            KIND_CHARGE
            if rechargeable_mode == RECHARGEABLE_MODE_CHARGE
            else KIND_REPLACE
        )
        if task_kind(task) == wanted or device_id in low_devices:
            continue
        actions.append(DeleteTask(task["id"], device_id))
        handled.add(device_id)

    for device_id, info in low_devices.items():
        if device_id in handled:
            continue
        battery_type = info.get("battery_type")
        battery_quantity = info.get("battery_quantity")
        action = plan_battery_low(
            tasks,
            device_id=device_id,
            device_name=info.get("name") or device_id,
            config_entry_id=config_entry_id,
            name_template=name_template,
            battery_type=battery_type,
            battery_quantity=battery_quantity,
            battery_level=info.get("battery_level"),
            charge_name_template=charge_name_template,
            rechargeable_mode=rechargeable_mode,
        )
        if action is not None:
            actions.append(action)
        # If the task already existed (ArmTask or already-armed no-op) but has no
        # chips yet, and we now know the battery spec, patch the chips so the type
        # shows up on the card without requiring the user to trigger a new event. A
        # recreated task is already carrying the chip in its fresh payload, and its
        # old id is about to stop existing, so skip the patch there.
        existing = task_for_device(tasks, device_id)
        if (
            existing
            and not isinstance(action, RecreateTask)
            and not existing.get("task_chips")
        ):
            chip = build_battery_chip(
                battery_type, battery_quantity, kind=task_kind(existing)
            )
            if chip:
                actions.append(UpdateChips(existing["id"], device_id, [chip]))

    for task in our_tasks(tasks):
        device_id = (task["source"][SOURCE_NS]).get("device_id")
        if device_id in handled:
            continue
        if device_id in recovered_devices and is_armed(task):
            actions.append(ClearTask(task["id"], device_id))
    return actions
