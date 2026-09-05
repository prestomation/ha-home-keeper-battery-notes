# Changelog

All notable changes to the Home Keeper — Battery Notes glue are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/) and the project uses
semantic versioning (with PEP 440 pre-release suffixes — `bN`/`aN`/`rcN` — for betas).

## [0.3.0b1] - 2026-08-23

### Added

- **"Charge battery" tasks for rechargeables.** A rechargeable going low means *plug it
  in*, not *replace the battery* — and for a radiator valve or a smart lock that's a real
  recurring chore. The **Rechargeable batteries** option now offers three answers:
  **Charge it** raises a *"Charge battery: …"* task (its own name template, a charging
  chip icon and a *"Mark battery as charged?"* prompt), **Ignore them** raises nothing
  (still the default), and **Replace it** treats the battery like a disposable. A charge
  task arms on every drain and clears on every charge, so its completions accumulate into
  a charging log on one persistent task. Completing one is deliberately **not** mirrored
  to Battery Notes: you charged the battery, you didn't change it, and stamping a
  replacement date would falsify the device's replacement history. (Fixes #18)

### Changed

- **The *Skip rechargeable batteries* switch became the *Rechargeable batteries*
  choice.** Existing setups keep behaving as configured with nothing to do: the switch
  **on** is the *Ignore them* default, and **off** — which used to mean a rechargeable got
  a replace task — becomes *Charge it*, the task it should have been raising all along.
  Setups that really do want the replacement task can pick *Replace it*.
- **Rechargeable tasks of the wrong kind are converted on the next reconcile.** Home
  Keeper locks a managed task's name against every edit, the owning integration's
  included, so a *"Replace battery: …"* task cannot be renamed into a
  *"Charge battery: …"* one — it is removed and re-created instead. **That device's
  completion history starts over**, and so do the task's device-page entity ids
  (`…_replace_battery_…` becomes `…_charge_battery_…`), so update any automation or
  dashboard card pointing at one. Only rechargeables are affected; disposable-cell
  tasks are never touched.

### Fixed

- **A task created by a reconcile records the battery level again.** Battery Notes
  keeps the charge level on its "battery plus" sensor, not on the battery-low sensor
  the reconcile read, so a task it created was noted *"1× AAA"* where a task from a
  live event said *"1× AAA · was at 8%"*. It now reads both. This shows up far more
  often with rechargeable modes, since every switch between them recreates tasks
  through that path.
- **Changing an option no longer logs an error.** The first options change after a
  restart removed an already-removed `homeassistant_started` listener, which Home
  Assistant reported as `Unable to remove unknown job listener` with a traceback. The
  glue was working fine; only the log said otherwise.

## [0.2.1] - 2026-08-17

### Fixed

- **Ships a brand icon**, so the HACS default-store submission's brands check passes
  without needing to ignore it.

## [0.2.0] - 2026-06-30

### Added

- **Battery type chip on tasks.** Battery replacement tasks now include a compact
  metadata chip showing the required battery spec (e.g. **2× AAA**, **CR2032**) in
  both the Home Keeper panel task list and the dashboard card. The chip appears
  automatically when Battery Notes reports the battery type — no configuration needed.
  Tasks created before battery type is known get the chip retroactively when Home
  Keeper reconciles at startup.

- **Announces itself to Home Keeper's companion discovery.** When set up, the glue
  registers with Home Keeper (via its `register_companion` service) so it appears as a
  **connected** companion under Home Keeper's **Settings → Companions** section, with a
  *Configure* button that opens this glue's own settings page. Home Keeper also
  *suggests* installing this glue to anyone who has Battery Notes but not the bridge.
  Best-effort and re-announced on Home Keeper reload; a no-op on older Home Keeper
  versions without companion discovery.

## [0.1.0] - 2026-06-21

First stable release. Bridges [Battery Notes](https://github.com/andrew-codechimp/HA-Battery-Notes)
low-battery signals to [Home Keeper](https://github.com/prestomation/ha-home-keeper)
`triggered` tasks. **Requires Home Keeper ≥ 0.3.0**, the first stable Home Keeper to
ship the `triggered` task type this glue depends on.

### Added

- **Low battery → a Home Keeper task, kept in sync both ways.** A battery going low
  creates a Home Keeper **"Replace battery: …"** task, armed (due-now), attached to the
  same device with a *"Managed by Battery Notes"* chip. Replacing it from either side
  keeps the two in sync: completing in Home Keeper pushes
  `battery_notes.set_battery_replaced`; the Battery Notes button or a recovered level
  clears the Home Keeper task. The task records the replacement and goes dormant (into the
  **Monitored** section) until the battery is low again, so replacement cadence
  accumulates on a single persistent task. Clearing is **affirmative** — only a battery
  actually reporting a not-low level clears the task — so a battery that goes from low to
  dead keeps its task instead of recording a phantom replacement. Stateless and
  self-healing: state is re-derived from `home_keeper.list_tasks` + Battery Notes'
  registry entities, reconciled on Home Assistant start, and every cross-integration call
  is `has_service`-guarded. Options: task name template, two-way sync, clear-on-recovery.
- **Flag batteries that stop reporting (suspected dead).** A dead battery usually goes
  silent (`unknown`/`unavailable`) rather than crossing the *low* threshold, so it would
  never become a task. An opt-in option drives Battery Notes'
  `check_battery_last_reported` on a daily cadence (and at startup) and raises a
  *"Replace battery: …"* task — noted *"not reporting for N days"* — for any battery
  stale beyond a configurable day threshold. Off by default; the threshold also debounces
  transient dropouts. New options: **Flag batteries that stop reporting** and **Days with
  no report before flagging**.
- **Skip rechargeable batteries (on by default).** A rechargeable device (phone, watch,
  tablet, …) going low means "charge it", not "replace the battery", so a replace-battery
  task is the wrong signal — it would churn forever (re-armed on every drain, cleared on
  every charge) and pile up phantom replacements, while the only thing that would justify
  a real replacement (capacity degradation) is something Battery Notes can't see. The glue
  raises no task for a rechargeable battery going low or non-reporting, and a startup
  reconcile retires any existing rechargeable task (even one created before the upgrade).
  New option **Skip rechargeable batteries**; turn it off to keep tracking rechargeable
  replacements by hand.

## [0.1.0b3] - 2026-06-21

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
