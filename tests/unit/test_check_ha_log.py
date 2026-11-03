"""Unit tests for scripts/check_ha_log.py.

Run with: python -m pytest tests/unit/test_check_ha_log.py -v
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "check_ha_log.py"
_spec = importlib.util.spec_from_file_location("check_ha_log", SCRIPT)
assert _spec is not None and _spec.loader is not None
check_ha_log = importlib.util.module_from_spec(_spec)
sys.modules["check_ha_log"] = check_ha_log
_spec.loader.exec_module(check_ha_log)

TS = "2026-11-02 10:00:00.123"

CLEAN_LOG = f"""\
{TS} INFO (MainThread) [homeassistant.core] Starting Home Assistant
{TS} WARNING (SyncWorker_0) [homeassistant.loader] We found a custom integration home_keeper_battery_notes which has not been tested by Home Assistant. This component might cause stability problems, be sure to disable it if you experience issues with Home Assistant
{TS} DEBUG (MainThread) [custom_components.home_keeper_battery_notes.coordinator] debug line is fine
{TS} INFO (MainThread) [custom_components.home_keeper_battery_notes] Setup done
{TS} WARNING (MainThread) [homeassistant.components.foo] Other integration warning
"""

BLOCKING_LOG = f"""\
{TS} INFO (MainThread) [homeassistant.core] Starting Home Assistant
{TS} WARNING (MainThread) [homeassistant.util.loop] Detected blocking call to read_text with args (PosixPath('/config/custom_components/home_keeper_battery_notes/manifest.json'),) inside the event loop by custom integration 'home_keeper_battery_notes' at custom_components/home_keeper_battery_notes/wiring.py, line 33: manifest_data = json.loads(manifest_path.read_text(encoding="utf-8")) (offender: /usr/local/lib/python3.13/pathlib/_local.py, line 546: return f.read()), please create a bug report at https://github.com/prestomation/ha-home-keeper-battery-notes/issues
Traceback (most recent call last):
  File "/config/custom_components/home_keeper_battery_notes/wiring.py", line 33, in async_get_config_entry_diagnostics
"""


def run(log: str, domain: str = "home_keeper_battery_notes", allowlist=None):
    records = check_ha_log.parse_records(log.splitlines())
    return check_ha_log.check_records(records, domain, allowlist or [])


def test_clean_log_has_no_findings():
    assert run(CLEAN_LOG) == []


def test_generic_custom_integration_notice_is_ignored():
    log = (
        f"{TS} WARNING (SyncWorker_0) [homeassistant.loader] We found a custom "
        "integration home_keeper_battery_notes which has not been tested by Home Assistant. "
        "This component might cause stability problems\n"
    )
    assert run(log) == []


@pytest.mark.parametrize("level", ["WARNING", "ERROR", "CRITICAL"])
def test_integration_logger_at_warning_or_higher_fails(level):
    log = f"{TS} {level} (MainThread) [custom_components.home_keeper_battery_notes.engine] boom\n"
    findings = run(log)
    assert len(findings) == 1
    assert "custom_components.home_keeper_battery_notes.engine" in findings[0].rule


def test_integration_root_logger_fails():
    log = f"{TS} ERROR (MainThread) [custom_components.home_keeper_battery_notes] boom\n"
    assert len(run(log)) == 1


def test_logger_of_other_domain_is_not_ours():
    log = f"{TS} ERROR (MainThread) [custom_components.home_keeper_battery_notes_extra] boom\n"
    assert run(log) == []
    log = f"{TS} ERROR (MainThread) [custom_components.other] boom\n"
    assert run(log) == []


def test_blocking_call_for_our_domain_fails():
    findings = run(BLOCKING_LOG)
    assert len(findings) == 1
    assert "blocking call" in findings[0].rule
    assert "wiring.py, line 33" in findings[0].text


def test_blocking_call_for_other_domain_is_ignored():
    log = (
        f"{TS} WARNING (MainThread) [homeassistant.util.loop] Detected blocking "
        "call to open inside the event loop by custom integration 'other' at "
        "custom_components/other/x.py, line 1\n"
    )
    assert run(log) == []


def test_docker_compose_prefix_is_accepted():
    log = "homeassistant-1  | " + BLOCKING_LOG.splitlines()[1] + "\n"
    assert len(run(log)) == 1


@pytest.mark.parametrize(
    "message",
    [
        (
            "/config/custom_components/home_keeper_battery_notes/engine.py:10: RuntimeWarning: "
            "coroutine 'foo' was never awaited"
        ),
        (
            "Task exception was never retrieved\nTraceback (most recent call last):\n"
            '  File "/config/custom_components/home_keeper_battery_notes/coordinator.py", line 5'
        ),
        (
            "Executing <Task pending name='Task-9' coro=<run() running at "
            "/config/custom_components/home_keeper_battery_notes/coordinator.py:55>> "
            "took 0.350 seconds"
        ),
        (
            "Unclosed client session client_session: <aiohttp.ClientSession> "
            "created at /config/custom_components/home_keeper_battery_notes/x.py:3"
        ),
        (
            "The integration 'home_keeper_battery_notes' uses a deprecated call "
            "(custom_components/home_keeper_battery_notes/__init__.py)"
        ),
    ],
)
def test_known_bad_patterns_for_our_domain_fail(message):
    log = f"{TS} WARNING (MainThread) [asyncio] {message}\n"
    assert len(run(log)) == 1


def test_known_bad_patterns_for_other_code_are_ignored():
    log = (
        f"{TS} WARNING (MainThread) [asyncio] Executing <Task pending name='T' "
        "coro=<run() running at /usr/src/homeassistant/foo.py:5>> took 0.4 seconds\n"
        f"{TS} WARNING (MainThread) [py.warnings] /config/custom_components/other/a.py:1: "
        "RuntimeWarning: coroutine 'x' was never awaited\n"
    )
    assert run(log) == []


def test_traceback_lines_belong_to_the_record_before_them():
    log = (
        f"{TS} ERROR (MainThread) [asyncio] Task exception was never retrieved\n"
        "future: <Task finished>\n"
        "Traceback (most recent call last):\n"
        '  File "/config/custom_components/home_keeper_battery_notes/engine.py", line 1, in f\n'
        f"{TS} INFO (MainThread) [homeassistant.core] later line\n"
    )
    findings = run(log)
    assert len(findings) == 1
    assert findings[0].line_no == 1


def test_allowlist_entry_hides_a_finding(tmp_path):
    path = tmp_path / "allow.txt"
    path.write_text(
        "# The test setup has no real light, so this warning is expected.\n"
        "light\\.missing is unavailable\n"
    )
    allow = check_ha_log.load_allowlist(path)
    log = (
        f"{TS} WARNING (MainThread) [custom_components.home_keeper_battery_notes.engine] "
        "light.missing is unavailable\n"
        f"{TS} WARNING (MainThread) [custom_components.home_keeper_battery_notes.engine] other\n"
    )
    findings = run(log, allowlist=allow)
    assert len(findings) == 1
    assert "other" in findings[0].text


def test_allowlist_entry_without_reason_is_rejected(tmp_path):
    path = tmp_path / "allow.txt"
    path.write_text("light\\.missing is unavailable\n")
    with pytest.raises(ValueError, match="no reason"):
        check_ha_log.load_allowlist(path)


def test_allowlist_reason_does_not_carry_over_a_blank_line(tmp_path):
    path = tmp_path / "allow.txt"
    path.write_text("# Reason one.\nfirst\n\nsecond\n")
    with pytest.raises(ValueError, match="no reason"):
        check_ha_log.load_allowlist(path)


def test_shipped_allowlist_is_valid():
    check_ha_log.load_allowlist(SCRIPT.with_name("ha_log_allowlist.txt"))


def _cli(tmp_path, log_text, *extra):
    log = tmp_path / "ha.log"
    log.write_text(log_text)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--domain", "home_keeper_battery_notes", *extra, str(log)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_exit_codes(tmp_path):
    assert _cli(tmp_path, CLEAN_LOG).returncode == 0
    failed = _cli(tmp_path, BLOCKING_LOG)
    assert failed.returncode == 1
    assert "wiring.py" in failed.stdout
    # An empty log must not pass.
    assert _cli(tmp_path, "").returncode == 2
    assert _cli(tmp_path, "no ha records here\n").returncode == 2


def test_cli_unreadable_allowlist_exits_2(tmp_path):
    # A directory cannot be read as a file, on any user (also as root in CI).
    result = _cli(tmp_path, CLEAN_LOG, "--allowlist", str(tmp_path))
    assert result.returncode == 2
    assert "error:" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_reads_stdin():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--domain", "home_keeper_battery_notes"],
        input=BLOCKING_LOG,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1


def test_cli_bad_allowlist_exits_2(tmp_path):
    allow = tmp_path / "allow.txt"
    allow.write_text("no reason here\n")
    assert _cli(tmp_path, CLEAN_LOG, "--allowlist", str(allow)).returncode == 2


# ---------------------------------------------------------------------------
# Realistic log text: compose prefix, colour codes, tracebacks
# ---------------------------------------------------------------------------

PREFIX = "homeassistant-1  | "

REAL_BLOCKING_LINE = (
    "2026-11-02 10:00:05.200 WARNING (MainThread) [homeassistant.util.loop] "
    "Detected blocking call to open with args "
    "(PosixPath('/config/custom_components/home_keeper_battery_notes/manifest.json'),) "
    "inside the event loop by custom integration 'home_keeper_battery_notes' at "
    "custom_components/home_keeper_battery_notes/wiring.py, line 34: "
    "manifest_data = json.loads(manifest_path.read_text(encoding=\"utf-8\")) "
    "(offender: /usr/local/lib/python3.13/pathlib/_local.py, line 546: "
    "return f.read()), please create a bug report at "
    "https://github.com/prestomation/ha-home-keeper-battery-notes/issues"
)

REAL_COMPOSE_LOG = "\n".join(
    PREFIX + line
    for line in [
        "s6-rc: info: service s6rc-oneshot-runner: starting",
        "2026-11-02 10:00:00.100 INFO (MainThread) [homeassistant.bootstrap] Starting Home Assistant",
        "2026-11-02 10:00:01.000 WARNING (SyncWorker_3) [homeassistant.loader] We found a custom integration home_keeper_battery_notes which has not been tested by Home Assistant.",
        REAL_BLOCKING_LINE,
        "Traceback (most recent call last):",
        '  File "/config/custom_components/home_keeper_battery_notes/wiring.py", line 34, in async_get_config_entry_diagnostics',
        "    manifest_data = json.loads(manifest_path.read_text(encoding=\"utf-8\"))",
        "2026-11-02 10:00:06.000 INFO (MainThread) [homeassistant.components.http] Request done",
    ]
)


def test_real_compose_log_is_parsed():
    records = check_ha_log.parse_records(REAL_COMPOSE_LOG.splitlines())
    levels = [r.level for r in records]
    # The s6 line has no level. The other four are records.
    assert levels == [None, "INFO", "WARNING", "WARNING", "INFO"]
    assert records[3].logger == "homeassistant.util.loop"
    # The traceback lines belong to the blocking-call record.
    assert "manifest_path.read_text" in records[3].text
    assert "Traceback" in records[3].text


def test_real_compose_log_finds_the_blocking_call():
    findings = run(REAL_COMPOSE_LOG)
    assert len(findings) == 1
    assert "blocking call" in findings[0].rule
    assert "wiring.py, line 34" in findings[0].text


def test_colour_codes_are_removed():
    log = f"{PREFIX}\x1b[33m{REAL_BLOCKING_LINE}\x1b[0m\n"
    assert len(run(log)) == 1


def test_compose_prefix_with_colour_codes_is_removed():
    log = f"\x1b[36mhomeassistant-1  |\x1b[0m {REAL_BLOCKING_LINE}\n"
    assert len(run(log)) == 1


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-11-02T10:00:05.200",
        "2026-11-02T10:00:05.200Z",
        "2026-11-02 10:00:05.200+00:00",
        "2026-11-02 10:00:05",
    ],
)
def test_other_timestamp_forms(stamp):
    log = f"{stamp} ERROR (MainThread) [custom_components.home_keeper_battery_notes] boom\n"
    assert len(run(log)) == 1


def test_prefix_forms():
    line = "2026-11-02 10:00:05.200 ERROR (MainThread) [custom_components.home_keeper_battery_notes] boom"
    for prefix in ("ha-1 | ", "ha_test.1  |", "homeassistant  | "):
        assert len(run(prefix + line + "\n")) == 1


def test_cli_prints_log_sample_when_no_records(tmp_path):
    lines = [f"unknown format line {i} " + "x" * 300 for i in range(40)]
    result = _cli(tmp_path, "\n".join(lines) + "\n")
    assert result.returncode == 2
    assert "no Home Assistant log records found" in result.stderr
    assert "unknown format line 0 " in result.stderr
    assert "unknown format line 14 " in result.stderr
    assert "unknown format line 15 " not in result.stderr
    assert "unknown format line 24 " not in result.stderr
    assert "unknown format line 39 " in result.stderr
    # Every shown line is cut to 200 characters (plus the "  | " lead).
    shown = [ln for ln in result.stderr.splitlines() if ln.startswith("  | ")]
    assert len(shown) == 30
    assert all(len(ln) <= 5 + 200 for ln in shown)


def test_cli_empty_log_exits_2_without_sample_lines(tmp_path):
    result = _cli(tmp_path, "")
    assert result.returncode == 2
    assert "no Home Assistant log records found" in result.stderr


