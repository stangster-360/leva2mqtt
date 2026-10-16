# Test environment

A self-contained stack for trying the bridge without touching a production setup:
own Mosquitto, the bridge built from this checkout, and optionally `tools/fake_ito.py`
replaying a capture as if it were the ito module.

## Proxmox LXC

`deploy/proxmox/create-test-lxc.sh` creates an unprivileged Debian container with
Docker (nesting, keyctl), tagged into a VLAN, and clones this repository to
`/opt/leva2mqtt`. Run it as root on the Proxmox host:

```sh
VLAN=30 IP=dhcp ./create-test-lxc.sh
VLAN=30 IP=192.168.30.60/24 GW=192.168.30.1 STORAGE=local-lvm ./create-test-lxc.sh
BRIDGE=vmbr30 VLAN= IP=dhcp STORAGE=local ./create-test-lxc.sh   # bridge already on VLAN 30
```

The bridge (`BRIDGE`, default `vmbr0`) must be VLAN aware. The VLAN needs DHCP (or a
static address), DNS and internet access for apt and Docker, and – for real-machine
tests – a route to the ito module and the MQTT broker.

## Run

```sh
cd /opt/leva2mqtt/deploy/test
cp .env.example .env                                  # done by the LXC script
cp <your capture>.txt captures/replay.txt
docker compose --profile fake up -d --build
docker compose logs -f leva2mqtt fake-ito
```

Watch MQTT: `docker compose exec mosquitto mosquitto_sub -v -t 'leva2mqtt-test/#'`.

## Updates

The LXC script installs `leva2mqtt-update.timer`: every 5 minutes it runs
`auto-update.sh`, which pulls `main` and, if something changed and the bridge is
running, rebuilds and restarts the running services (with the fake module if it runs).
Log: `journalctl -u leva2mqtt-update`. Manual: `systemctl start leva2mqtt-update`.
Off: `systemctl disable --now leva2mqtt-update.timer`.

## Captures and shot files

The bridge records the telnet stream continuously in the test stack
(`CAPTURE_ENABLED=true`, 7 days, `docs/bridge.md` → "Raw capture"). Files live in the
`data` volume (`/data/captures` in the container). From the Mac:

```sh
~/Silvia/tools/fetch-captures.sh          # → ~/Silvia/captures/bridge/
```

Shot files are deleted after a successful upload; `SHOTS_KEEP=true` in `.env` keeps
them (`/data/shots` in the same volume). On the LXC directly:
`ls "$(docker volume inspect -f '{{.Mountpoint}}' leva2mqtt-test_data)"`.

## Limits

- `fake_ito.py` only replays; it does not react to commands. The wake sequence
  (MCcREPORT SP1, then MCr), MC@ alert polls and settings written from HA can only be
  tested against the real machine.
- Captures are not part of the repository (`captures/` is ignored).
