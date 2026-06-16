"""Unit tests for the pure glue decision logic."""

import bn_logic as L


CFG = "entry123"
TMPL = "Replace battery: {device_name}"


def _task(device_id, *, next_due="2026-06-11T00:00:00-04:00", extra_source=None):
    """A Home-Keeper-shaped task owned by us (or by *extra_source* if given)."""
    source = {"home_keeper_battery_notes": {"device_id": device_id}}
    if extra_source is not None:
        source = extra_source
    return {"id": f"task_{device_id}", "next_due": next_due, "source": source}


# ── helpers ──────────────────────────────────────────────────────────────────
def test_task_for_device_matches_only_our_namespace():
    ours = _task("dev1")
    foreign = _task("dev1", extra_source={"other_integration": {"device_id": "dev1"}})
    assert L.task_for_device([foreign, ours], "dev1") is ours
    assert L.task_for_device([foreign], "dev1") is None


def test_is_armed():
    assert L.is_armed(_task("d", next_due="2026-01-01T00:00:00-04:00")) is True
    assert L.is_armed(_task("d", next_due=None)) is False


# ── battery low ──────────────────────────────────────────────────────────────
def test_low_with_no_task_creates_armed_triggered_task():
    action = L.plan_battery_low(
        [],
        device_id="dev1",
        device_name="Front door",
        config_entry_id=CFG,
        name_template=TMPL,
        battery_type="AAA",
        battery_quantity=2,
        battery_level=8,
    )
    assert isinstance(action, L.CreateTask)
    p = action.payload
    assert p["recurrence_type"] == "triggered"
    assert p["name"] == "Replace battery: Front door"
    assert "2× AAA" in p["notes"] and "8%" in p["notes"]
    assert p["device_id"] == "dev1"
    assert p["source"]["home_keeper_battery_notes"]["device_id"] == "dev1"
    mb = p["managed_by"]
    assert mb["integration"] == "home_keeper_battery_notes"
    assert mb["config_entry_id"] == CFG
    assert mb["deletion_protected"] is True
    assert mb["locked_fields"] == ["name", "device_id"]


def test_low_with_dormant_task_arms_it():
    tasks = [_task("dev1", next_due=None)]
    action = L.plan_battery_low(
        tasks, device_id="dev1", device_name="x", config_entry_id=CFG, name_template=TMPL
    )
    assert action == L.ArmTask("task_dev1", "dev1")


def test_low_with_already_armed_task_is_noop():
    tasks = [_task("dev1", next_due="2026-06-01T00:00:00-04:00")]
    action = L.plan_battery_low(
        tasks, device_id="dev1", device_name="x", config_entry_id=CFG, name_template=TMPL
    )
    assert action is None


# ── battery cleared ──────────────────────────────────────────────────────────
def test_clear_armed_task_returns_clear_action():
    tasks = [_task("dev1", next_due="2026-06-01T00:00:00-04:00")]
    assert L.plan_battery_cleared(tasks, device_id="dev1") == L.ClearTask("task_dev1", "dev1")


def test_clear_dormant_or_absent_is_noop():
    assert L.plan_battery_cleared([_task("dev1", next_due=None)], device_id="dev1") is None
    assert L.plan_battery_cleared([], device_id="dev1") is None


# ── reconcile ────────────────────────────────────────────────────────────────
def test_reconcile_creates_arms_and_clears_to_converge():
    tasks = [
        _task("low_known_dormant", next_due=None),       # low now -> arm
        _task("recovered_still_armed", next_due="2026-06-01T00:00:00-04:00"),  # not low -> clear
        _task("low_already_armed", next_due="2026-06-01T00:00:00-04:00"),       # low + armed -> noop
    ]
    low = {
        "low_known_dormant": {"name": "A"},
        "low_already_armed": {"name": "B"},
        "brand_new_low": {"name": "C"},                  # no task -> create
    }
    actions = L.plan_reconcile(tasks, low, config_entry_id=CFG, name_template=TMPL)

    kinds = {type(a) for a in actions}
    assert L.CreateTask in kinds and L.ArmTask in kinds and L.ClearTask in kinds
    assert L.ArmTask("task_low_known_dormant", "low_known_dormant") in actions
    assert L.ClearTask("task_recovered_still_armed", "recovered_still_armed") in actions
    creates = [a for a in actions if isinstance(a, L.CreateTask)]
    assert len(creates) == 1 and creates[0].device_id == "brand_new_low"
    # The low+armed device produces no action (idempotent).
    assert not any(
        isinstance(a, L.ArmTask) and a.device_id == "low_already_armed" for a in actions
    )


def test_reconcile_ignores_foreign_tasks():
    tasks = [_task("dev1", extra_source={"pawsistant": {"x": 1}}, next_due="2026-01-01T00:00:00-04:00")]
    # No low devices and the only task isn't ours -> nothing to do.
    assert L.plan_reconcile(tasks, {}, config_entry_id=CFG, name_template=TMPL) == []


def test_malformed_tasks_are_ignored():
    # Defensive: a non-dict entry or a task missing "id" must not match or crash.
    malformed = [
        "not-a-dict",
        {"source": {"home_keeper_battery_notes": {"device_id": "dev1"}}},  # no id
        {"id": "x", "source": {"other": {"device_id": "dev1"}}},  # not ours
    ]
    assert L.task_for_device(malformed, "dev1") is None
    assert L.our_tasks(malformed) == []
    # And a low event for that device creates a fresh task rather than KeyError-ing.
    action = L.plan_battery_low(
        malformed, device_id="dev1", device_name="x", config_entry_id=CFG, name_template=TMPL
    )
    assert isinstance(action, L.CreateTask)


def test_name_template_falls_back_on_bad_template():
    action = L.plan_battery_low(
        [], device_id="d", device_name="Garage", config_entry_id=CFG,
        name_template="Battery {nonexistent}",
    )
    assert isinstance(action, L.CreateTask)
    assert action.payload["name"] == "Replace battery: Garage"
