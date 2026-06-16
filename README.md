# Home Keeper — Battery Notes

A small glue integration that turns [Battery Notes](https://github.com/andrew-codechimp/HA-Battery-Notes)
low-battery signals into [Home Keeper](https://github.com/prestomation/ha-home-keeper)
maintenance tasks — so *"replace this battery"* shows up in your to-do list, on the
device's page, and in the mobile app, and is recorded when you do it.

## What it does

- **Battery goes low** → a Home Keeper **"Replace battery: …"** task becomes **due now**,
  attached to the same device, with a *"Managed by Battery Notes"* chip.
- **You replace it** — from *either* side — and the two stay in sync:
  - Check the task off in Home Keeper → Battery Notes is told the battery was replaced.
  - Press Battery Notes' "Battery Replaced" button (or the level just recovers) → the
    Home Keeper task is marked done and tucked away.
- **Between low events the task is dormant**: it leaves the to-do list and calendar and
  sits in Home Keeper's collapsed **"Monitored"** section, so only batteries that
  actually need attention show as due.
- **History accumulates** on the persistent task, so you get the real replacement
  cadence ("every ~13 months") instead of losing it on each change.

It uses Home Keeper's **`triggered`** (condition-driven) task type and is completely
decoupled: it talks to both integrations only over the Home Assistant event bus and
services, with `has_service` guards, so nothing breaks if one is missing.

## How it works

| Battery Notes signal | What the glue does |
|---|---|
| `battery_notes_battery_threshold` `battery_low: true` | create the task (born armed) if new, else `home_keeper.trigger_task` to re-arm |
| `battery_notes_battery_threshold` `battery_low: false` | `home_keeper.complete_task` (records the replacement, goes dormant) |
| `battery_notes_battery_replaced` | `home_keeper.complete_task` (idempotent) |
| `home_keeper_task_completed` (ours) | `battery_notes.set_battery_replaced` (two-way), with an `origin` guard so it never loops |

The glue is **stateless** — it re-derives everything from `home_keeper.list_tasks`
(matched by its `source` namespace) and Battery Notes' registry entities, and reconciles
once on Home Assistant start, so it self-heals across restarts and never creates
duplicate tasks.

## Install

1. Install **Home Keeper** and **Battery Notes**.
2. Add this repo to HACS as a custom repository (category: Integration), install, restart.
3. Settings → Devices & Services → **Add Integration** → *Home Keeper — Battery Notes*.

### Options

- **Task name template** — default `Replace battery: {device_name}`.
- **Two-way sync** — completing in Home Keeper marks the battery replaced in Battery
  Notes (default on).
- **Clear on recovery** — clear the task if a battery's level recovers on its own
  (default on).

## Development & tests

Three tiers (see `ci/`):

- **`ci/test-unit.sh`** — pure decision logic (`logic.py`), no Home Assistant required.
- **`ci/test-integration.sh`** — the glue against Home Keeper's real test fake
  (`home_keeper.testing`) in a HA test runtime: arm/clear/re-arm, two-way sync, and
  idempotency.
- **`ci/test-docker.sh`** — full end-to-end: **real** Home Keeper + Battery Notes + this
  glue in a Home Assistant container, driven over REST. `ci/fetch-upstreams.sh` clones
  the two upstreams (pin with `HK_REF` / `BN_REF`); this tier also serves as the
  contract test for Battery Notes' event shapes.

> **External contract note.** Battery Notes' event/field names are an external surface
> this glue depends on (see `const.py`). They're pinned and asserted by the Docker tier;
> if Battery Notes changes them, update `const.py` and re-pin `BN_REF`.

## Design

The full design — including why a persistent armed/dormant task (rather than
create/delete per cycle) and how it preserves history — lives in Home Keeper's
[`docs/BATTERY_NOTES_PLAN.md`](https://github.com/prestomation/ha-home-keeper/blob/main/docs/BATTERY_NOTES_PLAN.md)
and the integration contract in
[`docs/INTEGRATING.md`](https://github.com/prestomation/ha-home-keeper/blob/main/docs/INTEGRATING.md) §7.
