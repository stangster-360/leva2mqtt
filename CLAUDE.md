# CLAUDE.md

Working rules for Claude in this repository. They apply until new findings change them.

## Project

leva2mqtt is a bridge for a Rancilio Silvia with an ito module (leva! firmware). It reads
the module's telnet stream and offers three independent functions:

- shot upload to Visualizer.coffee (the main function),
- MQTT with Home Assistant discovery (optional),
- a Telegram message after each shot, sent by the bridge itself (not by HA), so the
  image is useful without Home Assistant.

It runs as a Docker container (amd64, arm64, armv7), later also as an HA add-on.

## Language

- Replies to the owner: German, direct and short.
- Everything in the repository is English: code, comments, commit messages, docs.

## Hard rules

- **No code from the Leva Companion.** It is copyrighted. The bridge is built only on our
  own captures (`tools/capture.py`) and public statements of the firmware developer.
- **Wake sequence:** when the machine becomes reachable or wakes from standby/eco, the
  bridge sends `MCcREPORT SP1`, then `MCr`. A restart of the bridge must not make the
  machine beep.
- **Data source is `MCr`.** Sensors missing from a frame are left out, never an error.
- **No own web UI.** Configuration via environment variables and `config.yaml`
  (env wins, `<VAR>_FILE` for secrets); runtime settings are Home Assistant entities.
- **Mark assumptions.** Anything not confirmed by a capture is marked unverified until a
  capture confirms it. Docs use the markers **seen**, **dev** and **unverified**
  (see `docs/protocol.md`). Do not promote a statement to **seen** without a capture.

## Decisions

- Shots go to a file queue first; Visualizer upload and Telegram retry from there. After a
  successful upload the file is deleted unless `shots.keep` is set.
- The bridge has its own "ready" logic and entity; it does not rely on the firmware's READY.
- Maintenance alerts: only while idle, every 5 min switch to `MC@` for about 1 s, then
  back to `MCr`.
- MQTT is optional (empty `mqtt.host` disables it). The name "leva2mqtt" may change.

## Git

- Commit and push to `main` when it makes sense (owner's permission, 13.10.2026). Keep
  commits focused; run the tests first.
- Never commit captures, `config.yaml`, `.env`, shot data or secrets (see `.gitignore`).

## Layout

- `leva2mqtt/` – the bridge (`python -m leva2mqtt`)
- `tools/` – `capture.py`, `monitor.py`, `replay.py`, `fake_ito.py` (standard library only)
- `tests/` – `python -m pytest tests`, synthetic frames from `tests/framegen.py`
- `docs/` – protocol field notes, bridge behaviour, capture playbook, detection rules
- `deploy/test/` – test stack (own Mosquitto, replayed capture);
  `deploy/proxmox/create-test-lxc.sh` creates the test LXC

## Test environment

Proxmox host `root@10.10.10.30`, test container in VLAN 30, created with
`VLAN=30 IP=dhcp deploy/proxmox/create-test-lxc.sh`. See `deploy/test/README.md`.
The fake module only replays captures and ignores commands; wake sequence, `MC@` polls and
writes from HA need the real machine.
