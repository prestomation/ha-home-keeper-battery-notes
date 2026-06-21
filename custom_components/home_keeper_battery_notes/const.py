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
OPT_SKIP_RECHARGEABLE = "skip_rechargeable"

DEFAULT_NAME_TEMPLATE = "Replace battery: {device_name}"
DEFAULT_TWO_WAY = True
DEFAULT_CLEAR_ON_RECOVERY = True
# Opt-in: a dead/non-reporting battery is ambiguous (could be an offline device), so
# leave it off by default. The day threshold is also the debounce that filters
# transient unknown/unavailable blips (e.g. a restart or a brief network dropout).
DEFAULT_TREAT_NOT_REPORTED = False
DEFAULT_NOT_REPORTED_DAYS = 3
# On by default: a rechargeable device (phone, watch, …) going low means "charge it",
# not "replace the battery", so a *replace*-battery task is the wrong signal and would
# churn — re-armed on every drain, cleared on every charge — forever. Battery Notes can
# only see instantaneous charge level, never the capacity degradation that would
# actually justify a replacement, so the honest default is to suppress these entirely.
# Users who do track rechargeable replacements by hand can turn it off.
DEFAULT_SKIP_RECHARGEABLE = True
# Battery Notes labels a rechargeable device's battery type with this string (from its
# device library). Matched case-insensitively as a substring so variants still hit.
RECHARGEABLE_BATTERY_TYPE = "rechargeable"

# Display metadata for the "Managed by" chip Home Keeper renders on our tasks.
MANAGED_DISPLAY_NAME = "Battery Notes"
MANAGED_ICON = "mdi:battery-alert"
COMPLETION_PROMPT = "Mark battery as replaced?"
