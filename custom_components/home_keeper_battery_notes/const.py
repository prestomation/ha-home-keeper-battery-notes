"""Constants for the Home Keeper ↔ Battery Notes glue integration.

This integration owns *no* domain logic of its own: it translates Battery Notes
events into Home Keeper ``triggered`` tasks and mirrors completions back. It talks
to both sides purely over the Home Assistant event bus and services — no Python
imports in either direction — so it degrades gracefully if either is absent.
"""

from __future__ import annotations

DOMAIN = "home_keeper_battery_notes"

# ── Home Keeper side ─────────────────────────────────────────────────────────
HK_DOMAIN = "home_keeper"
HK_EVENT_TASK_COMPLETED = "home_keeper_task_completed"
# Home Keeper fires this (at its setup and on reload) to ask companion integrations
# to (re-)announce themselves to its discovery registry. We both register at our own
# setup and respond to this ping, so discovery works regardless of startup order.
HK_EVENT_REGISTER_COMPANIONS = "home_keeper_register_companions"
HK_SERVICE_REGISTER_COMPANION = "register_companion"
# Namespace for the opaque ``source`` dict we attach to tasks we create, so we can
# recognise our own tasks later (``source[SOURCE_NS] == {"device_id": ...}``).
SOURCE_NS = DOMAIN
# Opaque ``origin`` marker we pass to complete_task so we can ignore the completion
# event it echoes back (loop prevention — see Home Keeper INTEGRATING.md §4).
ORIGIN = DOMAIN

# ── Battery Notes side (EXTERNAL CONTRACT — verify against a pinned release) ──
# These event/field names are Battery Notes' surface, not ours. They are asserted
# in the docker integration test; if Battery Notes renames them, update here.
BN_DOMAIN = "battery_notes"
BN_EVENT_THRESHOLD = "battery_notes_battery_threshold"
BN_EVENT_REPLACED = "battery_notes_battery_replaced"
# A dead battery usually stops reporting (its level goes unknown/unavailable), so it
# never crosses the *low* threshold — no THRESHOLD event fires and the battery-low
# binary sensor never reads "on". Battery Notes surfaces this instead as "not
# reported", but only when the check_battery_last_reported action is called (it's not
# a continuous sensor); so the glue drives that action and listens for this event.
BN_EVENT_NOT_REPORTED = "battery_notes_battery_not_reported"
BN_SERVICE_SET_REPLACED = "set_battery_replaced"
BN_SERVICE_CHECK_LAST_REPORTED = "check_battery_last_reported"
# Battery-low binary_sensor device class, used to find Battery Notes' low sensors in
# the entity registry during reconciliation (robust to entity_id renames/i18n).
BN_BATTERY_LOW_DEVICE_CLASS = "battery"

# Event data field names.
FIELD_DEVICE_ID = "device_id"
FIELD_DEVICE_NAME = "device_name"
FIELD_BATTERY_LOW = "battery_low"
FIELD_BATTERY_LEVEL = "battery_level"
FIELD_BATTERY_TYPE = "battery_type"
FIELD_BATTERY_QUANTITY = "battery_quantity"
# Carried on the not-reported event: how many days since the battery last reported.
FIELD_LAST_REPORTED_DAYS = "battery_last_reported_days"

# check_battery_last_reported action parameters.
BN_FIELD_DAYS_LAST_REPORTED = "days_last_reported"
BN_FIELD_RAISE_EVENTS = "raise_events"

# ── Options (config_flow) ────────────────────────────────────────────────────
OPT_NAME_TEMPLATE = "name_template"
OPT_TWO_WAY = "two_way"
OPT_CLEAR_ON_RECOVERY = "clear_on_recovery"
OPT_TREAT_NOT_REPORTED = "treat_not_reported"
OPT_NOT_REPORTED_DAYS = "not_reported_days"
OPT_RECHARGEABLE_MODE = "rechargeable_mode"
OPT_CHARGE_NAME_TEMPLATE = "charge_name_template"
# Legacy: the boolean OPT_RECHARGEABLE_MODE replaced. Still read (never written) so an
# entry configured before the mode existed keeps behaving as its owner chose — see
# ``wiring.BatteryNotesGlue._rechargeable_mode``.
OPT_SKIP_RECHARGEABLE = "skip_rechargeable"

DEFAULT_NAME_TEMPLATE = "Replace battery: {device_name}"
DEFAULT_CHARGE_NAME_TEMPLATE = "Charge battery: {device_name}"
DEFAULT_TWO_WAY = True
DEFAULT_CLEAR_ON_RECOVERY = True
# Opt-in: a dead/non-reporting battery is ambiguous (could be an offline device), so
# leave it off by default. The day threshold is also the debounce that filters
# transient unknown/unavailable blips (e.g. a restart or a brief network dropout).
DEFAULT_TREAT_NOT_REPORTED = False
DEFAULT_NOT_REPORTED_DAYS = 3

# ── What to do about a rechargeable battery ──────────────────────────────────
# A rechargeable going low means "charge it", not "replace the battery", so the
# replace-battery task a disposable cell earns is the wrong signal for one. Three
# honest answers, because which is right depends on the device:
#
# * ``charge``  — raise a *charge* task. Right for a device whose pack you top up as a
#   chore (radiator valves, smart locks): the arm-on-drain/clear-on-charge cycle is the
#   chore, and the completions accumulate into a charging log.
# * ``skip``    — raise nothing. Right for a phone or watch you charge without being
#   told, where a task would just churn. The default, so an upgrade is a no-op.
# * ``replace`` — treat it like a disposable. For users who track rechargeable
#   *replacements* by hand; Battery Notes can only see instantaneous charge level, never
#   the capacity degradation that would actually justify one, so this is a manual call.
RECHARGEABLE_MODE_CHARGE = "charge"
RECHARGEABLE_MODE_SKIP = "skip"
RECHARGEABLE_MODE_REPLACE = "replace"
RECHARGEABLE_MODES = [
    RECHARGEABLE_MODE_CHARGE,
    RECHARGEABLE_MODE_SKIP,
    RECHARGEABLE_MODE_REPLACE,
]
DEFAULT_RECHARGEABLE_MODE = RECHARGEABLE_MODE_SKIP
# Battery Notes labels a rechargeable device's battery type with this string (from its
# device library). Matched case-insensitively as a substring so variants still hit.
RECHARGEABLE_BATTERY_TYPE = "rechargeable"

# ── Task kinds ───────────────────────────────────────────────────────────────
# Stamped into our ``source`` namespace so we can tell what an existing task was
# created as. A task written before this existed carries no kind and reads as
# ``replace`` (see ``logic.task_kind``); reconcile converts one whose kind no longer
# matches the configured mode.
KIND_REPLACE = "replace"
KIND_CHARGE = "charge"

# Display metadata for the "Managed by" chip Home Keeper renders on our tasks. The chip
# names the *integration*, so it is the same icon whichever kind of task this is.
MANAGED_DISPLAY_NAME = "Battery Notes"
MANAGED_ICON = "mdi:battery-alert"
COMPLETION_PROMPT = "Mark battery as replaced?"
# A charge completion is recorded in Home Keeper only — Battery Notes has no notion of
# "charged", and mirroring it as a *replacement* would falsify its replacement history.
CHARGE_COMPLETION_PROMPT = "Mark battery as charged?"
# Icon for the battery-spec chip, per kind.
CHIP_ICON = "mdi:battery"
CHARGE_CHIP_ICON = "mdi:battery-charging"
