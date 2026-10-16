#!/usr/bin/env bash
# Copy the bridge's raw captures (capture.enabled) from the test LXC to this machine.
#
#   tools/fetch-captures.sh [destination]      default: ~/Silvia/captures/bridge
#
# Runs on the Mac: ssh to the Proxmox host, `pct exec` into the test LXC, tar the
# captures/ folder of the bridge's data volume and unpack it here. Works while the
# bridge is stopped as well. Settings via environment:
#   PROXMOX_HOST (root@10.10.10.30), CTID (103), VOLUME (leva2mqtt-test_data)
# One file per switch-on (<date>_<time>_bridge.txt), plain text, ready for replay.py.
set -euo pipefail

HOST=${PROXMOX_HOST:-root@10.10.10.30}
CTID=${CTID:-103}
VOLUME=${VOLUME:-leva2mqtt-test_data}
DEST=${1:-$HOME/Silvia/captures/bridge}

remote="d=\$(docker volume inspect -f '{{.Mountpoint}}' $VOLUME)/captures && tar czf - -C \"\$d\" ."
mkdir -p "$DEST"
ssh "$HOST" "pct exec $CTID -- sh -c $(printf %q "$remote")" | tar xzf - -C "$DEST"
ls -lh "$DEST"
