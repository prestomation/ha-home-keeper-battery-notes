#!/usr/bin/env bash
# Build the HACS release asset: a zip of the integration directory contents.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

cd custom_components/home_keeper_battery_notes
zip -r ../../home_keeper_battery_notes.zip . \
  -x "*/__pycache__/*" \
  -x "*/__pycache__" \
  -x "__pycache__/*" \
  -x "__pycache__" \
  -x "*.pyc"
