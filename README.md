# Home Keeper — Battery Notes

[![GitHub Downloads][downloads-shield]][releases]
[![GitHub Release][release-shield]][releases]
[![GitHub Release Date][release-date-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)
[![hacs][hacs-shield]][hacs]
![Project Maintenance][maintenance-shield]
[![ko-fi][kofi-shield]][kofi]
[![HACS Validation][hacs-validation-shield]][hacs-validation]
[![HA Version][ha-version-shield]][ha-version]

A small glue integration that turns [Battery Notes](https://github.com/andrew-codechimp/HA-Battery-Notes)
signals into [Home Keeper](https://github.com/prestomation/ha-home-keeper) tasks — so
*"replace this battery"* shows up in your to-do list, on the device page, and in the
mobile app, and is recorded when you do it.

## What it does

- **Battery goes low** → a Home Keeper **"Replace battery: …"** task becomes **due now**
  on the same device, with a *"Managed by Battery Notes"* chip.
- **Rechargeable?** → it can raise a **"Charge battery: …"** task instead, for the
  devices you top up rather than re-cell (radiator valves, smart locks). See
  [Rechargeable batteries](#rechargeable-batteries).
- **You replace it, from either side** → the two stay in sync (check the task off in Home
  Keeper, or press Battery Notes' *Battery Replaced* button / let the level recover).
- **Between low events the task is dormant** — it leaves the to-do list and calendar and
  sits in Home Keeper's collapsed **Monitored** section, so only batteries that actually
  need attention show as due, while replacement **history** accumulates on the task.

It uses Home Keeper's **`triggered`** (condition-driven) task type and is fully decoupled:
it talks to both integrations only over the event bus and services (with `has_service`
guards), so nothing breaks if one is missing.

![A low battery becomes a due task in Home Keeper](docs/images/flow-1-battery-low.png)
![The replaced battery's task goes dormant in the Monitored section](docs/images/flow-2-monitored.png)

> Screenshots are produced by the browser e2e tier driving the real stack, so they
> always reflect current behaviour.

## How it works

| Battery Notes signal | What the glue does |
|---|---|
| `battery_notes_battery_threshold` `battery_low: true` | create the task (born armed) if new, else `home_keeper.trigger_task` to re-arm |
| `battery_notes_battery_threshold` `battery_low: false` | `home_keeper.complete_task` (records the replacement, goes dormant) |
| `battery_notes_battery_replaced` | `home_keeper.complete_task` (idempotent) |
| `home_keeper_task_completed` (ours) | `battery_notes.set_battery_replaced` (two-way), with an `origin` guard so it never loops — skipped for charge tasks, which aren't replacements |

The glue is **stateless**: it re-derives everything from `home_keeper.list_tasks` (matched
by its `source` namespace) and Battery Notes' registry entities, and reconciles once on
start — so it self-heals across restarts and never creates duplicate tasks.

The glue also **registers itself with Home Keeper's companion discovery**, so it shows up
as a *connected* companion under Home Keeper's **Settings → Companions** (with a
*Configure* link to this integration's settings page). Home Keeper will likewise *suggest*
this bridge to anyone who has Battery Notes installed but hasn't added it yet.

## Install

1. Install **Home Keeper** and **Battery Notes**.
2. Add this repo to HACS as a custom repository (category: Integration), install, restart.
3. Settings → Devices & Services → **Add Integration** → *Home Keeper — Battery Notes*.

### Options

- **Task name template** — default `Replace battery: {device_name}`.
- **Two-way sync** — completing in Home Keeper marks the battery replaced in Battery Notes (default on).
- **Clear on recovery** — clear the task if a battery's level recovers on its own (default on).
- **Rechargeable batteries** — charge them, ignore them, or replace them (default **ignore**; see below).
- **Charge task name template** — default `Charge battery: {device_name}`.
- **Flag batteries that stop reporting** — also flag a battery that's gone silent (default **off**; see below).
- **Days with no report before flagging** — staleness threshold for the option above (default `3`).

## Rechargeable batteries

A rechargeable hitting a low charge means *plug it in*, not *replace the battery* — the
"low → replace" model is for **disposable** cells. But whether that's worth a task depends
entirely on the device, so **Rechargeable batteries** offers three answers:

| Mode | What a low rechargeable gets | Good for |
|---|---|---|
| **Charge it** | a **"Charge battery: …"** task, due now | a radiator valve, a smart lock — a pack you top up as a *chore*, on a cadence worth recording |
| **Ignore them** (default) | nothing | a phone or watch you charge without being told, where a task would just be noise |
| **Replace it** | a **"Replace battery: …"** task | tracking rechargeable *replacements* by hand |

**Charge it** leans into the churn that makes a replace task wrong: the task arms on every
drain and clears on every charge, and those completions accumulate into a charging log on
one persistent task. Completing it is **not** mirrored to Battery Notes — you charged the
battery, you didn't change it, so stamping a replacement date would falsify the device's
real replacement history.

**Ignore them** stays the default, so nothing starts appearing for devices you never asked
about. It also *retires* an existing rechargeable task on the next reconcile — even after
the device has charged back up.

Battery Notes identifies these by battery type (*Rechargeable*), so the mode applies to
every rechargeable it tracks.

> **Switching between Charge it and Replace it recreates the affected tasks**, which
> starts their completion history over. A task's name is locked to the managing
> integration and Home Keeper won't let even us rename it, so the conversion can't be an
> edit. It also renames the task's device-page entities — a `Replace battery: Hallway
> Lock` task's `button.hallway_lock_replace_battery_hallway_lock_mark_done` becomes
> `…_charge_battery_…` — so update any automation or dashboard card that points at one.
> Only rechargeables are touched; disposable-cell tasks are never affected.

## Dead / non-reporting batteries

A *dead* battery usually just stops reporting (`unknown`/`unavailable`) rather than
crossing the low threshold, so by default it never becomes a task. Turn on **Flag
batteries that stop reporting** and the glue periodically asks Battery Notes which
batteries haven't reported in **N days** (`check_battery_last_reported`) and raises the
same task, noted *"not reporting for N days"*. The day threshold doubles as a debounce.
It's **off by default** because a silent device isn't always a dead battery.

## Development & tests

Three tiers (see `ci/`):

- **`ci/test-unit.sh`** — pure decision logic (`logic.py`), no Home Assistant required.
- **`ci/test-integration.sh`** — the glue against Home Keeper's real test fake in a HA runtime.
- **`ci/test-docker.sh`** — full end-to-end (REST): **real** Home Keeper + Battery Notes + this
  glue in a container. `ci/fetch-upstreams.sh` clones the upstreams (pin with `HK_REF` / `BN_REF`)
  and this tier also serves as the contract test for Battery Notes' event shapes.
- **`ci/e2e-up.sh`** — browser end-to-end: the same stack with the Home Keeper panel built, where
  Playwright asserts/screenshots the real panel. Refresh the images with
  `SHOT_DIR=docs/images CAPTURE=1 bash ci/e2e-up.sh`.

> **External contract note.** Battery Notes' event/field names are an external surface (see
> `const.py`), pinned and asserted by the Docker tier; if they change, update `const.py` and re-pin `BN_REF`.

## Design

The full design — why a persistent armed/dormant task (rather than create/delete per cycle)
and how it preserves history — lives in Home Keeper's
[`docs/BATTERY_NOTES_PLAN.md`](https://github.com/prestomation/ha-home-keeper/blob/main/docs/BATTERY_NOTES_PLAN.md)
and the contract in [`docs/INTEGRATING.md`](https://github.com/prestomation/ha-home-keeper/blob/main/docs/INTEGRATING.md) §7.

<!--
Badge reference links.
-->

[downloads-shield]: https://img.shields.io/github/downloads/prestomation/ha-home-keeper-battery-notes/total.svg?style=for-the-badge
[releases]: https://github.com/prestomation/ha-home-keeper-battery-notes/releases
[release-shield]: https://img.shields.io/github/release/prestomation/ha-home-keeper-battery-notes.svg?style=for-the-badge
[release-date-shield]: https://img.shields.io/github/release-date/prestomation/ha-home-keeper-battery-notes?style=for-the-badge
[commits-shield]: https://img.shields.io/github/last-commit/prestomation/ha-home-keeper-battery-notes?style=for-the-badge
[commits]: https://github.com/prestomation/ha-home-keeper-battery-notes/commits/main
[license-shield]: https://img.shields.io/github/license/prestomation/ha-home-keeper-battery-notes.svg?style=for-the-badge
[hacs-shield]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge
[hacs]: https://github.com/hacs/integration
[maintenance-shield]: https://img.shields.io/badge/maintainer-%40prestomation-blue.svg?style=for-the-badge
[hacs-validation-shield]: https://github.com/prestomation/ha-home-keeper-battery-notes/actions/workflows/hacs.yml/badge.svg
[hacs-validation]: https://github.com/prestomation/ha-home-keeper-battery-notes/actions/workflows/hacs.yml
[ha-version-shield]: https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue.svg?style=for-the-badge
[ha-version]: https://www.home-assistant.io/
[kofi-shield]: https://img.shields.io/badge/Ko--fi-donate-FF5E5B?style=for-the-badge&logo=kofi&logoColor=white
[kofi]: https://ko-fi.com/prestomation
