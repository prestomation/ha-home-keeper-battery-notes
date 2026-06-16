#!/usr/bin/env bash
# Pure-logic unit tests (no Home Assistant needed).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
pip install -q pytest
python -m pytest tests/unit -v
