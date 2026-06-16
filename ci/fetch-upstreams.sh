#!/usr/bin/env bash
# Assemble the custom_components the Docker end-to-end tier mounts: this glue plus
# its two real upstreams (Home Keeper and Battery Notes). Needs network access to
# clone the upstreams; pin refs via HK_REF / BN_REF for reproducibility.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="$ROOT/tests/docker/custom_components"
HK_REPO="${HK_REPO:-https://github.com/prestomation/ha-home-keeper}"
# TODO: move back to "main" once the `triggered` type (ha-home-keeper#21) is merged.
HK_REF="${HK_REF:-claude/optimistic-sagan-dgrt64}"
# Battery Notes — the integration this glue bridges to.
BN_REPO="${BN_REPO:-https://github.com/andrew-codechimp/HA-Battery-Notes}"
BN_REF="${BN_REF:-main}"

rm -rf "$STAGE"
mkdir -p "$STAGE"

echo "[fetch-upstreams] staging this integration..."
cp -r "$ROOT/custom_components/home_keeper_battery_notes" "$STAGE/"

fetch() {
  local repo="$1" ref="$2" name="$3"
  local tmp
  tmp="$(mktemp -d)"
  echo "[fetch-upstreams] cloning $name ($repo@$ref)..."
  git clone --depth 1 --branch "$ref" "$repo" "$tmp" 2>/dev/null \
    || git clone --depth 1 "$repo" "$tmp"
  cp -r "$tmp/custom_components/$name" "$STAGE/"
  rm -rf "$tmp"
}

fetch "$HK_REPO" "$HK_REF" "home_keeper"
fetch "$BN_REPO" "$BN_REF" "battery_notes"

echo "[fetch-upstreams] staged:"
ls -1 "$STAGE"
