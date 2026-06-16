"""Pytest configuration for the Battery Notes glue.

``logic.py`` is pure (it imports only ``const``), so we load it in isolation under a
synthetic ``bn`` package — exactly like Home Keeper does for its recurrence engine.
This lets the high-value decision logic run without the full HA test harness. Tests
that need a real HA runtime (the wiring + the Home Keeper fake) live under
``tests/integration`` and need ``pytest-homeassistant-custom-component``.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_COMPONENT_DIR = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "home_keeper_battery_notes"
)


def _load_pure_modules() -> None:
    if "bn" in sys.modules:
        return
    pkg = types.ModuleType("bn")
    pkg.__path__ = [str(_COMPONENT_DIR)]  # type: ignore[attr-defined]
    sys.modules["bn"] = pkg
    for name in ("const", "logic"):
        spec = importlib.util.spec_from_file_location(
            f"bn.{name}", str(_COMPONENT_DIR / f"{name}.py")
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"bn.{name}"] = module
        spec.loader.exec_module(module)
    sys.modules["bn_logic"] = sys.modules["bn.logic"]
    sys.modules["bn_const"] = sys.modules["bn.const"]


_load_pure_modules()
