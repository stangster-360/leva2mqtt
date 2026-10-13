#!/usr/bin/env bash
# Create an unprivileged Debian LXC with Docker for the leva2mqtt test stack.
# Run as root on the Proxmox host. All settings are environment variables:
#
#   VLAN=30 IP=dhcp ./create-test-lxc.sh
#   VLAN=30 IP=192.168.30.60/24 GW=192.168.30.1 ./create-test-lxc.sh
#
# Afterwards: pct enter <CTID>, then follow deploy/test/README.md.
set -euo pipefail

CTID=${CTID:-$(pvesh get /cluster/nextid)}
CT_NAME=${CT_NAME:-leva2mqtt-test}
BRIDGE=${BRIDGE:-vmbr0}            # must be VLAN aware (or use a bridge already on VLAN 30 and VLAN=)
VLAN=${VLAN-30}                    # empty: no tag (bridge already on the VLAN)
IP=${IP:-dhcp}                     # dhcp or CIDR, e.g. 192.168.30.60/24
GW=${GW:-}                         # required with a static IP
STORAGE=${STORAGE:-local-lvm}      # root disk
TEMPLATE_STORAGE=${TEMPLATE_STORAGE:-local}
DISK=${DISK:-8}                    # GB
MEMORY=${MEMORY:-1024}             # MB
CORES=${CORES:-2}
SSH_KEYS=${SSH_KEYS:-}             # optional file with public keys for root
REPO=${REPO:-https://github.com/stangster-360/leva2mqtt.git}
BRANCH=${BRANCH:-main}

die() { echo "error: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root on the Proxmox host"
command -v pct >/dev/null || die "pct not found - not a Proxmox host?"
pct status "$CTID" >/dev/null 2>&1 && die "CT $CTID already exists"
[ "$IP" = dhcp ] || [ -n "$GW" ] || die "static IP needs GW"

echo "== template"
pveam update >/dev/null
case "$(uname -m)" in
  x86_64) ARCH=amd64 ;;
  aarch64) ARCH=arm64 ;;
  *) die "unsupported host architecture $(uname -m)" ;;
esac
TEMPLATE=$(pveam available --section system | awk -v a="_$ARCH." '/debian-[0-9]+-standard/ && index($2, a) {print $2}' | sort -V | tail -1)
[ -n "$TEMPLATE" ] || die "no Debian standard template available"
pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE" || pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"

NET="name=eth0,bridge=$BRIDGE,ip=$IP"
[ -n "$VLAN" ] && NET="$NET,tag=$VLAN"
[ -n "$GW" ] && NET="$NET,gw=$GW"

EXTRA=()
[ -n "$SSH_KEYS" ] && EXTRA+=(--ssh-public-keys "$SSH_KEYS")

echo "== create CT $CTID ($CT_NAME, $TEMPLATE, net0 $NET)"
pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
  --hostname "$CT_NAME" \
  --unprivileged 1 \
  --features nesting=1,keyctl=1 \
  --cores "$CORES" --memory "$MEMORY" --swap 512 \
  --rootfs "$STORAGE:$DISK" \
  --net0 "$NET" \
  --onboot 1 \
  --description "leva2mqtt test environment" \
  "${EXTRA[@]}"

pct start "$CTID"

echo "== waiting for network"
for _ in $(seq 30); do
  pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 && break
  sleep 2
done
pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 \
  || die "CT $CTID has no DNS/internet - check VLAN $VLAN (DHCP, gateway, firewall)"

echo "== docker and repository"
pct exec "$CTID" -- env BRANCH="$BRANCH" REPO="$REPO" bash -euc '
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl git >/dev/null
  curl -fsSL https://get.docker.com | sh >/dev/null
  git clone --branch "$BRANCH" "$REPO" /opt/leva2mqtt
  cp /opt/leva2mqtt/deploy/test/.env.example /opt/leva2mqtt/deploy/test/.env
  mkdir -p /opt/leva2mqtt/deploy/test/captures
  ln -s /opt/leva2mqtt/deploy/test/leva2mqtt-update.service \
        /opt/leva2mqtt/deploy/test/leva2mqtt-update.timer /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now leva2mqtt-update.timer
'

ADDR=$(pct exec "$CTID" -- hostname -I | awk '{print $1}')
cat <<EOF

CT $CTID ($CT_NAME) is running at $ADDR.
Next:
  pct enter $CTID
  cd /opt/leva2mqtt/deploy/test
  # edit .env, copy a capture to captures/ if you use the fake module
  docker compose --profile fake up -d --build
EOF
