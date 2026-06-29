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


# ── rechargeable batteries ───────────────────────────────────────────────────
def test_is_rechargeable_matches_label_case_insensitively():
    assert L.is_rechargeable("Rechargeable") is True
    assert L.is_rechargeable("rechargeable") is True
    assert L.is_rechargeable("  Li-ion (Rechargeable)  ") is True
    assert L.is_rechargeable("AAA") is False
    assert L.is_rechargeable("CR2032") is False
    assert L.is_rechargeable(None) is False
    assert L.is_rechargeable(42) is False


def test_low_rechargeable_skipped_creates_no_task():
    # A low charge on a rechargeable means "charge it", not "replace it": no task.
    action = L.plan_battery_low(
        [],
        device_id="phone",
        device_name="Fold7",
        config_entry_id=CFG,
        name_template=TMPL,
        battery_type="Rechargeable",
        battery_quantity=1,
        battery_level=9,
        skip_rechargeable=True,
    )
    assert action is None


def test_low_rechargeable_deletes_existing_task():
    # An upgrade retires a stale replace-battery task created before the option.
    tasks = [_task("phone", next_due=None)]
    action = L.plan_battery_low(
        tasks,
        device_id="phone",
        device_name="Fold7",
        config_entry_id=CFG,
        name_template=TMPL,
        battery_type="Rechargeable",
        skip_rechargeable=True,
    )
    assert action == L.DeleteTask("task_phone", "phone")


def test_low_rechargeable_still_created_when_option_off():
    # Opt-out: a user who tracks rechargeable replacements by hand still gets a task.
    action = L.plan_battery_low(
        [],
        device_id="phone",
        device_name="Fold7",
        config_entry_id=CFG,
        name_template=TMPL,
        battery_type="Rechargeable",
        skip_rechargeable=False,
    )
    assert isinstance(action, L.CreateTask)


def test_not_reported_rechargeable_is_skipped_too():
    # The suspected-dead path honours the same skip (a rechargeable can't be "dead").
    action = L.plan_battery_low(
        [],
        device_id="phone",
        device_name="Fold7",
        config_entry_id=CFG,
        name_template=TMPL,
        battery_type="Rechargeable",
        reason="not_reported",
        last_reported_days=12,
        skip_rechargeable=True,
    )
    assert action is None


# ── battery cleared ──────────────────────────────────────────────────────────
def test_clear_armed_task_returns_clear_action():
    tasks = [_task("dev1", next_due="2026-06-01T00:00:00-04:00")]
    assert L.plan_battery_cleared(tasks, device_id="dev1") == L.ClearTask("task_dev1", "dev1")


def test_clear_dormant_or_absent_is_noop():
    assert L.plan_battery_cleared([_task("dev1", next_due=None)], device_id="dev1") is None
    assert L.plan_battery_cleared([], device_id="dev1") is None


# ── not reported (suspected dead) ────────────────────────────────────────────
def test_not_reported_with_no_task_creates_armed_task_with_dead_notes():
    action = L.plan_battery_low(
        [],
        device_id="dev1",
        device_name="Hall sensor",
        config_entry_id=CFG,
        name_template=TMPL,
        battery_type="CR2032",
        battery_quantity=1,
        reason="not_reported",
        last_reported_days=12,
    )
    assert isinstance(action, L.CreateTask)
    notes = action.payload["notes"]
    assert "CR2032" in notes
    assert "not reporting for 12 days" in notes
    assert "%" not in notes  # no spurious "was at None%"


def test_not_reported_without_days_still_notes_not_reporting():
    action = L.plan_battery_low(
        [], device_id="d", device_name="x", config_entry_id=CFG,
        name_template=TMPL, reason="not_reported",
    )
    assert isinstance(action, L.CreateTask)
    assert action.payload["notes"] == "not reporting"


def test_not_reported_reuses_existing_task_no_duplicate():
    # A battery already low (armed) that then goes dark must not spawn a second task.
    tasks = [_task("dev1", next_due="2026-06-01T00:00:00-04:00")]
    assert L.plan_battery_low(
        tasks, device_id="dev1", device_name="x", config_entry_id=CFG,
        name_template=TMPL, reason="not_reported",
    ) is None
    # Dormant task -> just re-arm it (keeps history), still no new task.
    dormant = [_task("dev1", next_due=None)]
    assert L.plan_battery_low(
        dormant, device_id="dev1", device_name="x", config_entry_id=CFG,
        name_template=TMPL, reason="not_reported",
    ) == L.ArmTask("task_dev1", "dev1")


# ── reconcile ────────────────────────────────────────────────────────────────
def test_reconcile_creates_arms_and_clears_to_converge():
    tasks = [
        _task("low_known_dormant", next_due=None),       # low now -> arm
        _task("recovered_still_armed", next_due="2026-06-01T00:00:00-04:00"),  # off -> clear
        _task("low_already_armed", next_due="2026-06-01T00:00:00-04:00"),       # low + armed -> noop
    ]
    low = {
        "low_known_dormant": {"name": "A"},
        "low_already_armed": {"name": "B"},
        "brand_new_low": {"name": "C"},                  # no task -> create
    }
    recovered = {"recovered_still_armed"}
    actions = L.plan_reconcile(
        tasks, low, recovered, config_entry_id=CFG, name_template=TMPL
    )

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


def test_reconcile_does_not_clear_armed_task_for_silent_device():
    # A device that's neither low nor affirmatively recovered (e.g. dead/unknown, so
    # absent from both sets) must keep its armed task — clearing it would record a
    # phantom replacement and fight the not-reported path.
    tasks = [_task("gone_dark", next_due="2026-06-01T00:00:00-04:00")]
    actions = L.plan_reconcile(
        tasks, {}, set(), config_entry_id=CFG, name_template=TMPL
    )
    assert actions == []


def test_reconcile_deletes_rechargeable_task_regardless_of_state():
    # A rechargeable device's task must be retired even when it isn't currently low
    # (recovered/silent → absent from low_devices), which the low/recovered passes
    # alone would miss. Only the rechargeable pass catches it.
    tasks = [
        _task("phone_recovered", next_due=None),                       # dormant, recovered
        _task("phone_low", next_due="2026-06-01T00:00:00-04:00"),      # still armed + low
        _task("aaa_low", next_due="2026-06-01T00:00:00-04:00"),        # disposable, untouched
    ]
    low = {
        "phone_low": {"name": "Fold7", "battery_type": "Rechargeable"},
        "aaa_low": {"name": "Remote", "battery_type": "AAA"},
    }
    actions = L.plan_reconcile(
        tasks,
        low,
        set(),
        config_entry_id=CFG,
        name_template=TMPL,
        skip_rechargeable=True,
        rechargeable_devices=frozenset({"phone_recovered", "phone_low"}),
    )
    assert L.DeleteTask("task_phone_recovered", "phone_recovered") in actions
    assert L.DeleteTask("task_phone_low", "phone_low") in actions
    # The low rechargeable is deleted exactly once (not also re-armed/duplicated).
    assert sum(isinstance(a, L.DeleteTask) for a in actions) == 2
    assert not any(
        isinstance(a, (L.ArmTask, L.ClearTask)) and a.device_id.startswith("phone")
        for a in actions
    )
    # A disposable battery is unaffected by the rechargeable skip (no delete/arm/clear).
    assert not any(
        isinstance(a, (L.DeleteTask, L.ArmTask, L.ClearTask)) and a.device_id == "aaa_low"
        for a in actions
    )


def test_reconcile_ignores_foreign_tasks():
    tasks = [_task("dev1", extra_source={"pawsistant": {"x": 1}}, next_due="2026-01-01T00:00:00-04:00")]
    # No low devices, none recovered, and the only task isn't ours -> nothing to do.
    assert L.plan_reconcile(
        tasks, {}, set(), config_entry_id=CFG, name_template=TMPL
    ) == []


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


# ── build_battery_chip ───────────────────────────────────────────────────────

def test_build_battery_chip_with_quantity():
    chip = L.build_battery_chip("AAA", 2)
    assert chip == {"label": "2× AAA", "icon": "mdi:battery"}


def test_build_battery_chip_without_quantity():
    chip = L.build_battery_chip("CR2032", None)
    assert chip == {"label": "CR2032", "icon": "mdi:battery"}


def test_build_battery_chip_no_type():
    assert L.build_battery_chip(None, 2) is None
    assert L.build_battery_chip("", 2) is None


# ── task_chips in add_task payload ───────────────────────────────────────────

def test_build_add_task_payload_includes_chip():
    payload = L.build_add_task_payload(
        device_id="dev1",
        device_name="Front door",
        config_entry_id=CFG,
        name_template=TMPL,
        battery_type="AAA",
        battery_quantity=2,
        battery_level=8,
    )
    assert payload["task_chips"] == [{"label": "2× AAA", "icon": "mdi:battery"}]


def test_build_add_task_payload_no_chip_when_type_unknown():
    payload = L.build_add_task_payload(
        device_id="dev1",
        device_name="Front door",
        config_entry_id=CFG,
        name_template=TMPL,
    )
    assert payload["task_chips"] == []


def test_plan_battery_low_creates_task_with_chip():
    action = L.plan_battery_low(
        [],
        device_id="dev1",
        device_name="Garage",
        config_entry_id=CFG,
        name_template=TMPL,
        battery_type="CR2032",
        battery_quantity=1,
    )
    assert isinstance(action, L.CreateTask)
    assert action.payload["task_chips"] == [{"label": "1× CR2032", "icon": "mdi:battery"}]


# ── UpdateChips during reconcile ─────────────────────────────────────────────

def _task_with_chips(device_id, chips, *, next_due="2026-06-11T00:00:00-04:00"):
    t = _task(device_id, next_due=next_due)
    return {**t, "task_chips": chips}


def test_reconcile_emits_update_chips_when_type_becomes_known():
    # Task was created without a chip (battery_type was unknown at the time).
    task = _task("dev1", next_due="2026-06-01T00:00:00-04:00")  # no task_chips
    actions = L.plan_reconcile(
        [task],
        {"dev1": {"name": "Sensor", "battery_type": "AAA", "battery_quantity": 2}},
        set(),
        config_entry_id=CFG,
        name_template=TMPL,
    )
    chip_actions = [a for a in actions if isinstance(a, L.UpdateChips)]
    assert len(chip_actions) == 1
    assert chip_actions[0].task_id == "task_dev1"
    assert chip_actions[0].chips == [{"label": "2× AAA", "icon": "mdi:battery"}]


def test_reconcile_skips_update_chips_when_chips_already_set():
    task = _task_with_chips(
        "dev1", [{"label": "2× AAA", "icon": "mdi:battery"}],
        next_due="2026-06-01T00:00:00-04:00",
    )
    actions = L.plan_reconcile(
        [task],
        {"dev1": {"name": "Sensor", "battery_type": "AAA", "battery_quantity": 2}},
        set(),
        config_entry_id=CFG,
        name_template=TMPL,
    )
    assert not any(isinstance(a, L.UpdateChips) for a in actions)


def test_reconcile_skips_update_chips_when_type_still_unknown():
    task = _task("dev1", next_due="2026-06-01T00:00:00-04:00")
    actions = L.plan_reconcile(
        [task],
        {"dev1": {"name": "Sensor", "battery_type": None}},
        set(),
        config_entry_id=CFG,
        name_template=TMPL,
    )
    assert not any(isinstance(a, L.UpdateChips) for a in actions)
