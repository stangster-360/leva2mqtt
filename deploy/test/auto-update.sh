#!/usr/bin/env bash
# Pull main and rebuild the test stack if something changed. Run by
# leva2mqtt-update.timer inside the test LXC. Only services that are already
# running are restarted, so a stopped bridge stays stopped.
set -euo pipefail

REPO_DIR=${REPO_DIR:-/opt/leva2mqtt}
cd "$REPO_DIR"

old=$(git rev-parse HEAD)
git pull --ff-only -q
new=$(git rev-parse HEAD)
[ "$old" = "$new" ] && exit 0
echo "updated $old -> $new"

cd deploy/test
running=$(docker compose --profile fake ps --services --status running)
grep -qx leva2mqtt <<<"$running" || { echo "bridge not running, no restart"; exit 0; }

args=()
grep -qx fake-ito <<<"$running" && args=(--profile fake)
docker compose "${args[@]}" up -d --build
docker image prune -f >/dev/null
