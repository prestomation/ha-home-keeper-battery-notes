#!/usr/bin/env bash
# Browser end-to-end tier: real Home Keeper (panel built) + Battery Notes + this
# glue in a HA container, driven by Playwright. Fires Battery Notes events and
# asserts/screenshots the real Home Keeper panel.
#   KEEP_UP=1 bash ci/e2e-up.sh        # leave the container up afterwards
#   SHOT_DIR=docs/images CAPTURE=1 bash ci/e2e-up.sh   # also capture screenshots
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

cleanup() {
  if [ "${KEEP_UP:-0}" != "1" ]; then
    (cd tests/docker && docker compose down -v) || true
  fi
}
trap cleanup EXIT

# fetch-upstreams builds the Home Keeper panel so it actually renders in the container.
bash ci/fetch-upstreams.sh
(cd tests/docker && docker compose up -d)

echo "[e2e-up] waiting for Home Assistant..."
for _ in $(seq 1 90); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8123/api/ 2>/dev/null || true)
  [ "$code" = "200" ] || [ "$code" = "401" ] && break
  sleep 2
done

cd tests/e2e
npm ci 2>/dev/null || npm install --no-audit --no-fund
npx playwright install --with-deps chromium

if [ "${CAPTURE:-0}" = "1" ]; then
  SHOT_DIR="${SHOT_DIR:-$ROOT/docs/images}" npx playwright test screenshots.capture.ts --config=screenshots.config.ts
else
  npx playwright test
fi
