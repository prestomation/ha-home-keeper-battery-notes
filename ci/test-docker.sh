#!/usr/bin/env bash
# Full end-to-end tier: real Home Keeper + Battery Notes + this glue in a HA
# container, driven over REST. Needs Docker + network (to fetch the upstreams).
#   KEEP_UP=1 bash ci/test-docker.sh   # leave the container running afterwards
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

cleanup() {
  # Save the Home Assistant log first: `down` removes the container and its log.
  # scripts/check_ha_log.py reads this file in CI.
  (cd "$ROOT/tests/docker" && docker compose logs --no-color homeassistant > "$ROOT/ha-docker.log") || true
  if [ "${KEEP_UP:-0}" != "1" ]; then
    (cd "$ROOT/tests/docker" && docker compose down -v) || true
  fi
}
trap cleanup EXIT

bash ci/fetch-upstreams.sh
(cd tests/docker && docker compose up -d)

echo "[test-docker] waiting for Home Assistant..."
for _ in $(seq 1 90); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8123/api/ 2>/dev/null || true)
  [ "$code" = "200" ] || [ "$code" = "401" ] && break
  sleep 2
done

pip install -q pytest requests
python -m pytest tests/docker -v
