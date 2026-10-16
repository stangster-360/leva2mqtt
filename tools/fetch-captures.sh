#!/usr/bin/env bash
# Copy the bridge's raw captures and archived shot files from the test LXC to this Mac.
#
#   tools/fetch-captures.sh
#
# Runs on the Mac: ssh to the Proxmox host, `pct exec` into the test LXC, tar a folder of
# the bridge's data volume and unpack it here. Works while the bridge is stopped too.
#   captures/  -> $CAPTURES_DEST  one file per switch-on (<date>_<time>_bridge.txt)
#   shots/     -> $SHOTS_DEST     archived shot files (*.json); only with SHOTS_KEEP=true,
#                                 otherwise a shot is deleted after its upload. pending/
#                                 (not yet delivered) is left out.
# Files are only added or overwritten here, never deleted: the Mac keeps what the LXC
# already pruned (captures after 7 days).
# Settings via environment:
#   PROXMOX_HOST (root@10.10.10.30), CTID (103), VOLUME (leva2mqtt-test_data),
#   CAPTURES_DEST (~/Silvia/captures/bridge), SHOTS_DEST (~/Silvia/shots)
set -euo pipefail

HOST=${PROXMOX_HOST:-root@10.10.10.30}
CTID=${CTID:-103}
VOLUME=${VOLUME:-leva2mqtt-test_data}
CAPTURES_DEST=${CAPTURES_DEST:-$HOME/Silvia/captures/bridge}
SHOTS_DEST=${SHOTS_DEST:-$HOME/Silvia/shots}

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

# fetch <folder in the volume> <destination>
fetch() {
  local remote="d=\$(docker volume inspect -f '{{.Mountpoint}}' $VOLUME)/$1"
  remote+=" && if [ -d \"\$d\" ]; then tar czf - -C \"\$d\" --exclude=./pending .; fi"
  ssh "$HOST" "pct exec $CTID -- sh -c $(printf %q "$remote")" >"$tmp"
  if [ ! -s "$tmp" ]; then
    echo "$1: nothing on the LXC yet"
    return
  fi
  mkdir -p "$2"
  tar xzf "$tmp" -C "$2"
  echo "$1 -> $2: $(find "$2" -type f | wc -l | tr -d ' ') files"
}

fetch captures "$CAPTURES_DEST"
fetch shots "$SHOTS_DEST"
