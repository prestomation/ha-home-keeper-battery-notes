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

import math
from dataclasses import dataclass
from typing import Any

from .const import (
    ASSET_ICON,
    ASSET_LOCKED_FIELDS,
    ASSET_ROLE_STOCK,
    CHARGE_CHIP_ICON,
    CHARGE_COMPLETION_PROMPT,
    CHIP_ICON,
    COMPLETION_PROMPT,
    DEFAULT_CHARGE_NAME_TEMPLATE,
    DEFAULT_NAME_TEMPLATE,
    DEFAULT_RECHARGEABLE_MODE,
    KIND_CHARGE,
    KIND_REPLACE,
    LOCKED_FIELDS,
    MANAGED_DISPLAY_NAME,
    MANAGED_ICON,
    OPT_RECHARGEABLE_MODE,
    OPT_SKIP_RECHARGEABLE,
    PART_TYPE_CONSUMABLE,
    RECHARGEABLE_BATTERY_TYPE,
    RECHARGEABLE_MODE_CHARGE,
    RECHARGEABLE_MODE_SKIP,
    RECHARGEABLE_MODES,
    SOURCE_NS,
    USAGE_NOTE_MAX_NAMES,
    USAGE_NOTE_NONE,
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
    so Home Keeper renders the "Managed by Battery Notes" chip, locks the fields we own
    (``LOCKED_FIELDS``), shows the completion prompt, and protects deletion while we're
    installed (with
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
            "locked_fields": list(LOCKED_FIELDS),
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


# ── battery stock: the appliance we keep in Home Keeper ──────────────────────
# Home Keeper holds spares as *parts* of an appliance, and an integration can own an
# appliance and its part list (docs/INTEGRATING.md §8). The glue keeps one virtual
# appliance — "Batteries" by default — with one consumable part per battery type it
# sees. The user keeps every count on those parts, and Home Keeper takes the quantity
# a device holds off the count when its replace task is completed.
#
# Everything below is pure: it reads the appliance list, the task list and a snapshot
# of the Battery Notes devices, and returns the actions ``wiring.py`` runs.


@dataclass(frozen=True)
class EnsureAsset:
    """Create the battery appliance (call ``home_keeper.add_asset``)."""

    payload: dict[str, Any]


@dataclass(frozen=True)
class UpdateManagedAsset:
    """Write the appliance fields we own (``home_keeper.update_managed_asset``).

    *name* re-applies the appliance name from the option, *parts* is the whole part
    list as we want it. Home Keeper matches a part on its ``id``, so each stored part
    echoes its own id back; a part with no id is a new one, and it starts untracked.
    """

    asset_id: str
    name: str | None = None
    parts: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class LinkConsumable:
    """Link a replace task to its battery type (``home_keeper.set_task_consumable``).

    *quantity* is what one completion takes off the count: the number of cells the
    device holds.
    """

    task_id: str
    asset_id: str
    part_id: str
    quantity: float


@dataclass(frozen=True)
class UnlinkConsumable:
    """Drop a task's consumable link (``set_task_consumable`` with empty ids)."""

    task_id: str


StockAction = EnsureAsset | UpdateManagedAsset | LinkConsumable | UnlinkConsumable


def normalize_battery_type(raw: Any) -> str | None:
    """The display spelling of a battery type, or ``None`` when there is none.

    Battery Notes reports the type as free text, so ``"AAA"``, ``" aaa "`` and
    ``"AAA "`` all name one battery. The text is trimmed and its inner runs of
    whitespace collapse to single spaces; :func:`battery_type_key` then folds the case
    for the match. A value that is not a string, or is empty, has no type.
    """
    if not isinstance(raw, str):
        return None
    collapsed = " ".join(raw.split())
    return collapsed or None


def battery_type_key(display: str) -> str:
    """The match key for a battery type: its display spelling, case folded."""
    return display.casefold()


def _quantity(value: Any) -> float:
    """How many cells a device holds: a positive number, or 1 when it says nothing."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 1
    if not math.isfinite(amount) or amount <= 0:
        return 1
    return int(amount) if amount.is_integer() else amount


def device_batteries(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Every battery a device record describes.

    Battery Notes allows 2 entries on one device, so a record can carry a
    ``batteries`` list. A record without one describes a single battery in its own
    ``battery_type``/``battery_quantity``/``rechargeable`` fields.
    """
    batteries = record.get("batteries")
    if isinstance(batteries, list) and batteries:
        return [b for b in batteries if isinstance(b, dict)]
    return [record]


def _pool_batteries(record: dict[str, Any]) -> list[tuple[str, float]]:
    """The ``(type, quantity)`` pairs of *record* that belong in the battery pool.

    A rechargeable is left out. It is charged, not replaced, so it is no spare to
    keep in a drawer, and its Battery Notes type reads ``Rechargeable`` for every
    device that has one.
    """
    pool: list[tuple[str, float]] = []
    for battery in device_batteries(record):
        if is_rechargeable(battery.get("battery_type")):
            continue
        display = normalize_battery_type(battery.get("battery_type"))
        if display is None:
            continue
        pool.append((display, _quantity(battery.get("battery_quantity"))))
    return pool


def _format_quantity(value: float) -> str:
    """A quantity as it reads in the usage note (``2``, not ``2.0``)."""
    return str(int(value)) if float(value).is_integer() else str(value)


def usage_note(entries: list[tuple[str, float]]) -> str:
    """The part notes naming the devices that hold a battery type.

    *entries* is one ``(device name, quantity)`` pair per device. The text reads
    ``Used by 4 devices · 7 installed — Front door sensor (2), Hall remote (2)``, with
    the names in alphabetical order. After :data:`USAGE_NOTE_MAX_NAMES` names the rest
    are counted as ``+N more``. An empty list gives :data:`USAGE_NOTE_NONE`.
    """
    if not entries:
        return USAGE_NOTE_NONE
    ordered = sorted(entries, key=lambda item: (item[0].casefold(), item[0]))
    devices = len(ordered)
    installed = sum(quantity for _name, quantity in ordered)
    head = (
        f"Used by {devices} device{'' if devices == 1 else 's'}"
        f" · {_format_quantity(installed)} installed"
    )
    shown = ordered[:USAGE_NOTE_MAX_NAMES]
    names = [f"{name} ({_format_quantity(quantity)})" for name, quantity in shown]
    remaining = devices - len(shown)
    if remaining:
        names.append(f"+{remaining} more")
    return f"{head} — {', '.join(names)}"


def _usage_by_type(devices: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group the devices by battery type.

    The result maps the type key to ``{"display": …, "entries": [(name, quantity)]}``.
    The display spelling is the first one seen, in device order. A device with 2
    entries of one type counts once, with the quantities added.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for device_id, record in devices.items():
        name = str(record.get("name") or device_id)
        per_type: dict[str, float] = {}
        for display, quantity in _pool_batteries(record):
            key = battery_type_key(display)
            grouped.setdefault(key, {"display": display, "entries": []})
            per_type[key] = per_type.get(key, 0) + quantity
        for key, quantity in per_type.items():
            grouped[key]["entries"].append((name, quantity))
    return grouped


def desired_parts(
    devices: dict[str, dict[str, Any]], existing: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The part list we want on the appliance: one consumable part per battery type.

    Each part carries the id of the stored part with the same name, so Home Keeper
    updates that part rather than making a second one. A type that no device uses now
    is dropped, unless the user counted it: such a part is kept, with
    :data:`USAGE_NOTE_NONE` for its notes, because the spares are still in the drawer.
    """
    stored_by_key = {
        battery_type_key(str(part.get("name") or "")): part for part in existing
    }
    grouped = _usage_by_type(devices)
    parts: list[dict[str, Any]] = []
    for key in sorted(grouped):
        bucket = grouped[key]
        stored = stored_by_key.get(key)
        part: dict[str, Any] = {
            "name": str((stored or {}).get("name") or bucket["display"]),
            "type": PART_TYPE_CONSUMABLE,
            "notes": usage_note(bucket["entries"]),
        }
        if stored and stored.get("id"):
            part["id"] = str(stored["id"])
        parts.append(part)
    for key, stored in stored_by_key.items():
        if key in grouped or stored.get("stock") is None:
            continue
        parts.append(
            {
                "id": str(stored["id"]),
                "name": str(stored.get("name") or ""),
                "type": PART_TYPE_CONSUMABLE,
                "notes": USAGE_NOTE_NONE,
            }
        )
    return parts


def build_asset_managed_by(config_entry_id: str) -> dict[str, Any]:
    """The ownership block Home Keeper records on our appliance."""
    return {
        "integration": SOURCE_NS,
        "display_name": MANAGED_DISPLAY_NAME,
        "icon": ASSET_ICON,
        "config_entry_id": config_entry_id,
        "deletion_protected": True,
        "locked_fields": list(ASSET_LOCKED_FIELDS),
    }


def build_asset_payload(
    name: str,
    config_entry_id: str,
    parts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The ``home_keeper.add_asset`` payload for the battery appliance.

    A virtual appliance, because the batteries are a pool and not one device. The
    ``source`` namespace is how we find it again after a restart.
    """
    return {
        "name": name,
        "kind": "virtual",
        "icon": ASSET_ICON,
        "parts": [dict(part) for part in (parts or [])],
        "source": {SOURCE_NS: {"role": ASSET_ROLE_STOCK}},
        "managed_by": build_asset_managed_by(config_entry_id),
    }


def find_our_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Our battery appliance in *assets*, matched by our ``source`` namespace."""
    for asset in assets:
        if not isinstance(asset, dict) or not asset.get("id"):
            continue
        src = (asset.get("source") or {}).get(SOURCE_NS)
        if isinstance(src, dict) and src.get("role") == ASSET_ROLE_STOCK:
            return asset
    return None


def _parts_differ(stored: list[dict[str, Any]], wanted: list[dict[str, Any]]) -> bool:
    """Whether the appliance's part list needs a write.

    Only the keys we own are compared, on the parts both lists hold, plus the
    membership of the list itself. Every stock number belongs to the user, so a
    change to one is never a reason to write.
    """
    if len(stored) != len(wanted):
        return True
    by_id = {str(part.get("id")): part for part in stored}
    for part in wanted:
        current = by_id.get(str(part.get("id")))
        if current is None:
            return True
        for key in ("name", "type", "notes"):
            if str(current.get(key) or "") != str(part.get(key) or ""):
                return True
    return False


def _link_of(task: dict[str, Any]) -> dict[str, Any] | None:
    """The consumable link on *task*, or ``None`` when it has none."""
    link = (task.get("source") or {}).get("part")
    return link if isinstance(link, dict) else None


def _link_matches(
    link: dict[str, Any], asset_id: str, part_id: str, quantity: float
) -> bool:
    """Whether the stored link already says what we want it to say."""
    if str(link.get("asset_id")) != asset_id or str(link.get("part_id")) != part_id:
        return False
    stored = link.get("quantity")
    if stored is None:
        return False
    try:
        return float(stored) == float(quantity)
    except (TypeError, ValueError):
        return False


def _link_target(
    record: dict[str, Any], parts_by_key: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], float] | None:
    """The part a device's replace task draws from, and how much it takes.

    A device with 2 Battery Notes entries counts in the usage of both types, but one
    task can draw from one part. It draws from the type it holds most of.
    """
    best: tuple[dict[str, Any], float] | None = None
    for display, quantity in _pool_batteries(record):
        part = parts_by_key.get(battery_type_key(display))
        if part is None or not part.get("id"):
            continue
        if best is None or quantity > best[1]:
            best = (part, quantity)
    return best


def plan_stock_reconcile(
    assets: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    devices: dict[str, dict[str, Any]],
    *,
    config_entry_id: str,
    appliance_name: str,
) -> list[StockAction]:
    """Converge the battery appliance and the consumable links on our tasks.

    *devices* maps a device id to what Battery Notes reports for it: its name, its
    battery type and quantity, whether the battery is rechargeable, and a ``batteries``
    list when the device has 2 entries.

    The appliance comes first. Without one, the only action is to create it, with the
    parts it needs; the caller runs a second pass to link the tasks once the parts
    have ids. With one, a changed appliance name or part list is a single
    ``update_managed_asset``.

    Then every replace task we own is linked to the part for its battery type, for as
    many cells as the device holds. A charge task is never linked, because a charged
    battery is not a spare taken out of the drawer. A task whose device or battery
    type is gone keeps its count-free state: the link is dropped.
    """
    actions: list[StockAction] = []
    asset = find_our_asset(assets)
    stored_parts = [
        part for part in (asset or {}).get("parts") or [] if isinstance(part, dict)
    ]
    wanted = desired_parts(devices, stored_parts)
    if asset is None:
        return [
            EnsureAsset(build_asset_payload(appliance_name, config_entry_id, wanted))
        ]

    rename = appliance_name if str(asset.get("name") or "") != appliance_name else None
    repart = wanted if _parts_differ(stored_parts, wanted) else None
    if rename is not None or repart is not None:
        actions.append(UpdateManagedAsset(str(asset["id"]), name=rename, parts=repart))

    asset_id = str(asset["id"])
    parts_by_key = {
        battery_type_key(str(part.get("name") or "")): part for part in stored_parts
    }
    for task in our_tasks(tasks):
        link = _link_of(task)
        record = devices.get(task["source"][SOURCE_NS].get("device_id"))
        target = (
            _link_target(record, parts_by_key)
            if record is not None and task_kind(task) == KIND_REPLACE
            else None
        )
        if target is None:
            if link is not None:
                actions.append(UnlinkConsumable(task["id"]))
            continue
        part, quantity = target
        part_id = str(part["id"])
        if link is None or not _link_matches(link, asset_id, part_id, quantity):
            actions.append(LinkConsumable(task["id"], asset_id, part_id, quantity))
    return actions
