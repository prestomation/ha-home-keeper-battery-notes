#!/usr/bin/env bash
# HA-runtime integration tests against Home Keeper's real test fake.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
pip install -q -r requirements-test.txt
python -m pytest tests/integration -v
