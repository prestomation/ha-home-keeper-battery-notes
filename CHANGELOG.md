# Changelog

All notable changes to the Home Keeper — Battery Notes glue are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/) and the project uses
semantic versioning (with PEP 440 pre-release suffixes — `bN`/`aN`/`rcN` — for betas).

## [Unreleased]

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
