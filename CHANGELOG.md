# Changelog

All notable changes to the Home Keeper — Battery Notes glue are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/) and the project uses
semantic versioning (with PEP 440 pre-release suffixes — `bN`/`aN`/`rcN` — for betas).

## [Unreleased]

### Added

- **Skip rechargeable batteries (on by default).** A rechargeable device (phone, watch,
  tablet, …) going low means "charge it", not "replace the battery", so a replace-battery
  task is the wrong signal — it would churn forever (re-armed on every drain, cleared on
  every charge) and pile up phantom replacements, while the only thing that would justify
  a real replacement (capacity degradation) is something Battery Notes can't see. The glue
  now raises no task for a rechargeable battery going low or non-reporting, and a startup
  reconcile retires any existing rechargeable task (including one created before the
  upgrade, even if the device has since recovered). New option **Skip rechargeable
  batteries**; turn it off to keep tracking rechargeable replacements by hand.

## [0.1.0b2] - 2026-06-19

### Added

- **Flag batteries that stop reporting (suspected dead).** A dead battery usually goes
  silent (`unknown`/`unavailable`) rather than crossing the *low* threshold, so it
  never became a task. New opt-in option drives Battery Notes'
  `check_battery_last_reported` on a daily cadence (and at startup) and raises a
  *"Replace battery: …"* task — noted *"not reporting for N days"* — for any battery
  stale beyond a configurable day threshold. Off by default; the threshold also
  debounces transient dropouts. New options: **Flag batteries that stop reporting**
  and **Days with no report before flagging**.

### Fixed

- Startup reconcile no longer clears an armed task when its device has gone silent
  (`unknown`/`unavailable`). Clearing is now **affirmative** — only a battery actually
  reporting a not-low level again (low sensor `off`) clears the task — so a battery
  that goes from low to dead keeps its task instead of recording a phantom replacement.

## [0.1.0b1] - 2026-06-16

First beta. Bridges [Battery Notes](https://github.com/andrew-codechimp/HA-Battery-Notes)
low-battery signals to [Home Keeper](https://github.com/prestomation/ha-home-keeper)
`triggered` tasks.

- A battery going low creates a Home Keeper **"Replace battery: …"** task, armed
  (due-now), attached to the same device with a *"Managed by Battery Notes"* chip.
- Replacing it — from either side — keeps the two in sync: completing in Home Keeper
  pushes `battery_notes.set_battery_replaced`; the Battery Notes button or a recovered
  level clears the Home Keeper task. The task records the replacement and goes dormant
  (into the **Monitored** section) until the battery is low again, so replacement
  cadence accumulates on a single persistent task.
- Stateless and self-healing: state is re-derived from `home_keeper.list_tasks` +
  Battery Notes' registry entities, reconciled on Home Assistant start; every
  cross-integration call is `has_service`-guarded.
- Options: task name template, two-way sync, clear-on-recovery.

> **Beta note.** Requires Home Keeper with the `triggered` task type
> (ha-home-keeper#21). Until that ships in a Home Keeper release, install Home Keeper
> from its matching branch. This beta is offered only to HACS users who enabled
> "Show beta versions".
