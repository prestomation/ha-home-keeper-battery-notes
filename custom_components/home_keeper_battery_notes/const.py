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
BN_SERVICE_SET_REPLACED = "set_battery_replaced"
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

# ── Options (config_flow) ────────────────────────────────────────────────────
OPT_NAME_TEMPLATE = "name_template"
OPT_TWO_WAY = "two_way"
OPT_CLEAR_ON_RECOVERY = "clear_on_recovery"

DEFAULT_NAME_TEMPLATE = "Replace battery: {device_name}"
DEFAULT_TWO_WAY = True
DEFAULT_CLEAR_ON_RECOVERY = True

# Display metadata for the "Managed by" chip Home Keeper renders on our tasks.
MANAGED_DISPLAY_NAME = "Battery Notes"
MANAGED_ICON = "mdi:battery-alert"
COMPLETION_PROMPT = "Mark battery as replaced?"
