# The bridge

> **Status: first version, not yet run against a real machine.** It has been checked
> with replays of our own captures (`tools/replay.py`) and end to end against a fake
> module (`tools/fake_ito.py`) and Mosquitto. The Visualizer upload and the Telegram
> message have not been tested against the real services yet.

The bridge keeps one telnet connection to the ito module, reads the `MCr` stream and
feeds three independent outputs:

| Output | Enabled when | What it does |
|---|---|---|
| Shot files | `shots.directory` is set (default `/data/shots`) | queue for the deliveries below, optional archive (see "Shot delivery") |
| Visualizer.coffee | `visualizer.username` and `password` are set | uploads every shot |
| Telegram | `telegram.token` and `chat_id` are set | sends a message after every shot |
| MQTT / Home Assistant | `mqtt.host` is set | live values, state, buttons, settings, last shot |

There is no web interface. Configuration is `config.yaml` and environment variables
(see [config.example.yaml](../config.example.yaml)); settings that change while the
bridge runs (dose, bean, grinder setting, upload / message switches) are Home
Assistant entities.

## Running

```sh
docker build -t leva2mqtt .
docker run -d --name leva2mqtt -v $PWD/data:/data \
  -e MACHINE_HOST=<ito-ip> -e MACHINE_NAME=Silvia -e MQTT_HOST=<broker> leva2mqtt
```

or without Docker (Python 3.10+):

```sh
pip install -r requirements.txt
MACHINE_HOST=<ito-ip> MQTT_HOST=<broker> SHOTS_DIRECTORY=./shots python3 -m leva2mqtt
```

The container runs as uid 1000; `/data` must be writable for it. The health check
reports healthy while the bridge is connected to the MQTT broker (or MQTT is not used).
A machine that is switched off is not unhealthy.

## Configuration

Every key `<section>.<key>` can be set as environment variable `<SECTION>_<KEY>`;
`<VAR>_FILE` reads the value from a file. Environment variables win over the file.

| Key | Default | |
|---|---|---|
| `machine.host` | – | ito module, required |
| `machine.port` | 23 | |
| `machine.name` | Espresso machine | device name in Home Assistant |
| `machine.id` | from name | used in topics and unique ids |
| `mqtt.host` | – | empty = no MQTT |
| `mqtt.port`, `username`, `password`, `tls` | 1883, –, –, false | |
| `mqtt.topic_prefix` | leva2mqtt | |
| `mqtt.discovery_prefix` | homeassistant | |
| `mqtt.publish_interval` | 1.0 | s between live updates while idle; every frame (4/s) while brewing |
| `visualizer.username`, `password` | – | Visualizer account (Basic Auth) |
| `visualizer.upload` | true | start value of the switch entity |
| `telegram.token`, `chat_id` | – | |
| `telegram.notify` | true | start value of the switch entity |
| `shots.directory` | /data/shots | queue (`pending/`) and archive; empty = queue in memory only, lost on restart |
| `shots.keep` | false | keep the shot file after a successful Visualizer upload |
| `shots.dose` | 18.0 | start value of the dose entity, g |
| `shots.tail` | 5.0 | s after the pump stops that still count for the yield |
| `bridge.report_min_offline` | 10 | see "Setpoint beep" |
| `bridge.ready_band`, `ready_hold` | 0.25 K, 240 s | see "Ready" |
| `bridge.steam_threshold` | 100 °C | |
| `log_level` | INFO | |

## Connection

Rules from [detection-rules.md](detection-rules.md), section 1:

- `MCr` after every connect.
- Watchdog: nothing at all received for 3 s → close, reconnect every 2 s.
- Keepalive newline every 5 s.
- No `MCr` frames for 5 s (heartbeat only, display frames) → re-send `MCr`.
- The bridge only ever sends `MCr`, `MCa`, `MCe`, `MCE` and `MCcREPORT SP1`.
  Everything else is refused in code.

## Setpoint beep (`MCcREPORT SP1`)

Fixed requirement: when the machine becomes reachable or wakes from standby / eco,
the bridge sends `MCcREPORT SP1` and then `MCr`. A restart of the bridge does not beep.

- `MCcREPORT SP1` wakes the machine from standby and eco (**seen**), so it is only sent
  while the machine is awake (`PID` in the frame). A pending report waits for that.
- Wake from standby / eco: detected in the frame (`???`/`ECO` → `PID`), works for the
  button, `MCa` and the leva! timers alike.
- Reachable again: only when the bridge had seen the machine offline for at least
  `bridge.report_min_offline` seconds. Every reconnect takes at least 5 s (watchdog +
  retry); a power cycle takes longer (module up 4–20 s after power-on, first `MCr`
  often lost). The 10 s default is **unverified**.
- First connection after a bridge start: no beep, whatever state the machine is in.
- The "Beep setpoint" button sends the same sequence. If it wakes the machine, no
  second, automatic report follows.

Whether the module accepts `MCcREPORT SP1` in the first seconds after power-on, as
soon as frames arrive, is **unverified**.

## States

`sensor.<name>_state`: `offline`, `standby`, `eco`, `heating`, `idle`, `ready`,
`brewing`, `hot_water`, `steam` (priority in this order from `brewing` down).

| State | Rule | Status |
|---|---|---|
| standby / eco | frame pos 4–6 `???` / `ECO` | **seen** |
| brewing | `*` flag (pump or brew program, not only shots) | **seen** |
| steam | boiler ≥ 100 °C while the setpoint is below; ends below 99 °C | threshold **unverified** |
| hot_water | flow meter counter rises ≥ 5 ml without `*`, ends after 3 s without change | **seen** (one capture) |
| ready | boiler within ±0.25 K of the setpoint for 240 s, lost beyond ±1 K or on pump / steam / hot water | **unverified** – the module has no ready flag; tuned to one ready beep |
| heating | more than 1.5 K below the setpoint | **seen** |
| idle | awake, none of the above | |

## Pump runs and shots

Every `*` period is a run, closed 5 s after `*` ends so the drip is part of the yield.

| Class | Rule | Status |
|---|---|---|
| shot | brew pressure above 3 bar while `*` | **seen** (3 runs) |
| flush | `*` without reaching 3 bar | **seen** (2 runs) |
| tank_empty | flush ≥ 5 s with 9 bar target, mean flow < 100 ml/min | **unverified** |

Shot values: duration (from `*`), yield (max. scale weight incl. tail), volume (flow
meter, reads ~6 % low), peak pressure, first drop (weight > 0.5 g, threshold
**unverified**), mean flow by weight after the first drop, boiler temperature at start
and minimum, profile name (position in the frame **unverified**), plus dose, ratio,
bean and grinder setting from the settings entities.

Replaying the `shots` capture gives 21.4 s / 42.3 g and 18.1 s / 42.9 g; M. read
42.3 g and 42.9 g off the scale.

Only shots are uploaded, saved and announced. All runs are published on `…/event`.

## Shot delivery

A finished shot goes through a small persistent queue, so a crash, a restart or an
outage of the internet or of Visualizer cannot lose it:

1. The summary goes to MQTT at once (`event`, `last_shot`).
2. The shot is written to `shots/pending/<date>_<time>_shot.json` **before** anything
   is sent (written to a temp file, then renamed).
3. Visualizer upload, then the Telegram message. The targets are fixed when the shot
   is taken (configured and switched on at that moment). The message does not wait
   for a failed upload; it then goes out without the link.
4. A failed delivery is retried after 1, 5, 15, 30 and then every 60 minutes, and at
   once when the bridge starts. Rejected files (HTTP 400/413/415/422 from Visualizer,
   400 from Telegram) are not retried. Wrong credentials (401/403) are retried, so the
   shot goes up after the configuration is fixed.
5. A Telegram message that could not be sent within 60 minutes is dropped: a late
   "shot finished" is of no use.
6. When nothing is pending any more, the file is
   - **deleted** if the Visualizer upload succeeded (default, `shots.keep: false`),
   - **moved to `shots/`** (archive) if `shots.keep` is on, if there was no Visualizer
     target (the file is the only copy of the curves) or if the upload was rejected.

After a successful upload `last_shot` gets `visualizer_url` (only if no newer shot has
replaced it meanwhile) and `event` gets a `shot_uploaded` message.

The file is a Decent JSON shot that Visualizer accepts as it is, so an archived shot can
also be uploaded by hand. The key `leva2mqtt` holds the summary and the delivery state
(state, attempts, last error, URL); it is removed before the upload.

Known gap: if the bridge dies after Visualizer accepted a shot but before the state
was saved, the shot is uploaded a second time on the next start.

## MQTT topics

`<prefix>/<id>/…`:

| Topic | |
|---|---|
| `bridge` | `online` / `offline`, retained, last will |
| `availability` | `online` while frames arrive, retained |
| `state` | live values as JSON; keys a machine does not have are `null` |
| `last_shot` | last shot as JSON, retained; gets `visualizer_url` after the upload |
| `event` | every finished run (`shot`, `flush`, `tank_empty`); `shot_uploaded` with the Visualizer URL |
| `pending` | number of shots waiting for delivery, retained |
| `command/<wake\|eco\|standby\|report>` | payload `PRESS` |
| `settings/<key>` / `settings/<key>/set` | retained runtime settings, restored after a restart |

## Home Assistant entities

Sensors appear through MQTT discovery **only after their value has been seen in a
frame**, so a machine without scale or flow meter gets no weight or flow entities.

- Sensors: state, boiler temperature, setpoint, heater output, heater output last
  minute, brew pressure, target pressure, weight, flow by weight, flow meter rate,
  flow meter volume, control mode, profile, PID terms (disabled by default),
  last shot (timestamp, attributes = shot record), last shot yield, last shot duration,
  shots waiting for delivery (diagnostic)
- Binary sensors: pump, heating up, ready, steam mode, hot water
- Buttons: wake (`MCa`), eco (`MCe`), standby (`MCE`), beep setpoint
- Settings: dose (number), bean, grinder setting (text), Visualizer upload and
  Telegram message (switches, only when configured)

## Not done yet

Differences to the current proposals in [detection-rules.md](detection-rules.md), to be
aligned once the cold-start captures are evaluated:

- **Ready:** the code uses a single band (±0.25 K for 240 s). The cold-start capture
  showed that the firmware's ready beep follows the per-minute heater average (pos
  73–77): ready when the boiler is within ±0.5 K and the last three per-minute averages
  lie within less than 1.0 percentage point, evaluated at each update of the field (two
  hits, see detection-rules.md). To be ported.
- **Other client:** the code re-sends `MCr` after 5 s without `MCr` frames. With the
  Leva Companion app connected this would break the app's display; detection-rules.md
  proposes to expose "other client active" and keep reading the display frames instead.

- Maintenance alerts (`BACKFL`, `DSCALE`, …) and `READY` only exist in display frames.
  Planned: switch to `MC@` for about a second every few minutes while idle. Decided
  after the warm-up test.
- Multi-arch images (amd64 / arm64 / armv7) and the Home Assistant add-on.
- Backflush detection (no capture yet).

## Development tools

- `tools/replay.py <capture>` – run a capture through the detection logic and print
  states, beeps and runs.
- `tools/fake_ito.py <capture> --port 2300` – serve a capture as a fake module; start
  the bridge with `MACHINE_HOST=127.0.0.1 MACHINE_PORT=2300`.
- `python -m pytest tests` – unit tests with synthetic frames.
