#!/usr/bin/env bash
# Assemble the custom_components the Docker end-to-end tier mounts: this glue plus
# its two real upstreams (Home Keeper and Battery Notes). Needs network access to
# clone the upstreams; pin refs via HK_REF / BN_REF for reproducibility.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../" && pwd)"
STAGE="$ROOT/tests/docker/custom_components"
HK_REPO="${HK_REPO:-https://github.com/prestomation/ha-home-keeper}"
# Pinned to the commit of ha-home-keeper#349 (managed appliances), the same ref
# requirements-test.txt names. Move both to the v0.24.0b9 tag once Home Keeper
# releases it.
HK_REF="${HK_REF:-039b9f7e02aaa1f1f059ad49efa95e5d3f33ab4e}"
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
  # A branch or a tag clones directly; a commit SHA cannot, so fetch it by name
  # into an empty repository instead. Without the second path a SHA pin fell back
  # to the default branch and the tier tested an upstream nobody asked for.
  git clone --depth 1 --branch "$ref" "$repo" "$tmp" 2>/dev/null || {
    git init -q "$tmp"
    git -C "$tmp" remote add origin "$repo"
    git -C "$tmp" fetch -q --depth 1 origin "$ref"
    git -C "$tmp" checkout -q FETCH_HEAD
  }
  cp -r "$tmp/custom_components/$name" "$STAGE/"
  rm -rf "$tmp"
}

fetch "$HK_REPO" "$HK_REF" "home_keeper"
fetch "$BN_REPO" "$BN_REF" "battery_notes"

# Home Keeper ships its panel JS as a build artifact (gitignored), so a fresh clone
# has only the TypeScript source. Build it here so the panel actually renders in the
# container — required for the browser/screenshot tier. Skipped gracefully if there's
# no frontend (e.g. a future Home Keeper layout) so the REST-only tier still works.
HK_FRONTEND="$STAGE/home_keeper/frontend"
if [ -f "$HK_FRONTEND/package.json" ] && [ "${SKIP_PANEL_BUILD:-0}" != "1" ]; then
  echo "[fetch-upstreams] building Home Keeper panel..."
  (cd "$HK_FRONTEND" && (npm ci --no-audit --no-fund || npm install --no-audit --no-fund) && npm run build)
fi

echo "[fetch-upstreams] staged:"
ls -1 "$STAGE"
