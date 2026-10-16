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
| `visualizer.username`, `password` | – | Visualizer account (Basic Auth), see [visualizer.md](visualizer.md) |
| `visualizer.upload` | true | start value of the switch entity |
| `visualizer.coffee_bags_refresh` | 1800 | s between reloads of the coffee bag list; 0 = no coffee bag entity |
| `telegram.token`, `chat_id` | – | |
| `telegram.notify` | true | start value of the switch entity |
| `telegram.language` | en | message language, `en` or `de` |
| `shots.directory` | /data/shots | queue (`pending/`) and archive; empty = queue in memory only, lost on restart |
| `shots.keep` | false | keep the shot file after a successful Visualizer upload |
| `shots.dose` | 18.0 | start value of the dose entity, g |
| `shots.tail` | 5.0 | s after the pump stops that still count for the yield |
| `shots.grinders` | – | grinder names for the **Grinder** select; env: comma-separated (`SHOTS_GRINDERS=Niche Zero,DF64`); one name = preselected |
| `bridge.report_min_offline` | 10 | see "Setpoint beep" |
| `bridge.ready_band`, `ready_spread`, `ready_grace` | 0.5 K, 1.0 %-points, 120 s | see "Ready" |
| `bridge.steam_threshold` | 100 °C | |
| `bridge.alert_interval` | 300 s | see "Maintenance alerts" |
| `bridge.other_client_quiet` | 180 s | see "Other client" |
| `capture.enabled` | false | raw capture of the telnet stream, see "Raw capture" |
| `capture.directory` | /data/captures | one file per switch-on |
| `capture.keep_days` | 7 | files older than this are deleted |
| `log_level` | INFO | |

## Connection

Rules from [detection-rules.md](detection-rules.md), section 1:

- `MCr` after every connect.
- Watchdog: nothing at all received for 3 s → close, reconnect every 2 s.
- Keepalive newline every 5 s.
- No frames at all for 5 s (heartbeat only) → re-send `MCr`.
- Display frames while `MCr` is wanted (more than 1.5 s after the bridge's own `MCr`),
  an `XML=` line or setup-dump lines: another client is active, see "Other client".
- The bridge only ever sends `MCr`, `MC@`, `MCa`, `MCe`, `MCE` and `MCcREPORT SP1`.
  Everything else is refused in code.
- Frames with a wrong `~ XX` checksum are dropped and counted ("Dropped frames",
  diagnostic, `total_increasing`, starts at 0 after a bridge restart). One in 21 173
  captured frames was bad; a rising count points to a weak WLAN link.
- The ito accepts at most 4 connections on port 23 (ito manual; idle ones time out
  after 5 min). How it turns away a fifth one is not captured. Heuristic (**unverified**):
  three connections in a row that end without a single byte (refused, closed at once,
  or silent until the watchdog) set the diagnostic binary sensor "Connection slots
  full" and log a warning; the first frame clears it. The retry interval stays 2 s.

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
- While another client holds the module in display mode, only `MCcREPORT SP1` is sent;
  the `MCr` is left out so the client keeps working (deviation from the requirement,
  on purpose).

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
| ready | see "Ready" below | **unverified** (2 hits) |
| heating | more than 1.5 K below the setpoint | **seen** |
| idle | awake, none of the above | |

## Ready

The module has no ready flag in `MCr`, and the firmware's `READY` is shown for only
10 s in display frames. The bridge reproduces the firmware's decision instead
(docs/detection-rules.md, section 3):

- **Boiler at temperature** (own binary sensor): within ±0.5 K of the setpoint.
- **Ready**: at an update of the per-minute heater average (pos 73–77, once a minute),
  the boiler is at temperature **and** the last three averages lie within less than
  1.0 percentage point. An update that repeats the old value cannot be seen in the
  frame; an unchanged value is counted again after 65 s (**unverified**).
- Ready is kept during a shot or hot water and for 120 s afterwards (recovery time,
  decided by M. on 13.10.; `bridge.ready_grace`). It is lost when the boiler is still
  more than 1.5 K below the setpoint after that, and in eco, standby and steam mode.
  120 s is enough: after the 13.10. shots the boiler was back above setpoint − 1.5 K
  ~55 s after the end (it then overshoots by ~1.6 K and needs ~4.5 min to settle inside
  ±0.5 K, so "boiler at temperature" goes off and on meanwhile). **seen** (2 shots)
- After a bridge start or a lost connection the bridge needs three updates (about
  2–3 min) before it can report ready, also on a machine that is long warm.

Replay of the captures: cold start `heatup-cold2` ready at 494.8 s, M.'s note "beep
10 s ago" at 510 s; warm power cycle `hb-resend` ready at 391.8 s, beep noted at 401 s.
Two hits; whether the 1-point spread is absolute or relative is **unverified**.

## Maintenance alerts

Alerts (`BACKFL`, `DSCALE`, `FILTER`, `REFILL`, `PROTECT`, `VALVE`, `ERR:…`) and the
firmware's `READY` exist only in display frames. Decided 13.10.:

- While idle (awake or eco; no pump run incl. its 5 s tail, no steam, no hot water)
  the bridge switches to `MC@` every `bridge.alert_interval` seconds (default 300; the
  first poll 30 s after the connection), reads display frames for 1 s and switches
  back to `MCr`. No display frame within 3 s ends the poll as well.
- If the shot screen appears during a poll, the bridge switches back at once. At most
  ~0.6 s of pre-infusion is lost (about 1 in 300 shots).
- An alert counts when it appears in any main-screen frame of a poll; it is cleared
  after two polls without it, since alerts may blink (**unverified**). Their position
  on the screen is unknown (no alert captured yet), so the whole main screen is
  searched.
- The alarm bell glyph (display control character 7, Protocol.pdf) anywhere on the
  display counts as the alert `BELL` when no alert text is found, so an unknown alert
  still switches "Maintenance alert" on. Never seen in a capture (**unverified**).
- Entities: "Maintenance alerts" (`none` or e.g. `BACKFL`, unknown until the first
  poll) and the binary sensor "Maintenance alert" (problem).

## Other client

The report mode is global for the module. The Leva Companion app sends `MC?`, `MCu`
and `MC@` when it connects (seen), which switches every connection to display frames.
Switching straight back to `MCr` would break the app's display, so the bridge:

- recognises the app by the `XML=` reply, the setup dump or display frames it did not
  ask for – also when these arrive before the first frame of a new connection (seen
  16.10.2026: machine switched on, app and bridge connected at once, the dump came
  3.8 s before the first frame),
- stops its alert polls and does not send `MCr` on its own,
- reads what the display shows (temperature, setpoint, heater, pressure, weight,
  standby / eco, shot screen) and keeps publishing it; no shot logging and no
  "ready" changes meanwhile (no per-minute average in the display),
- reports "Other client connected" (diagnostic binary sensor),
- switches back to `MCr` after `bridge.other_client_quiet` seconds (default 180)
  without identify / dump lines and without input on the machine's display. If the
  app is still open it switches back to display frames again and the phase starts
  over. The interval is **unverified**.

## Pump runs and shots

Every `*` period is a run, closed 5 s after `*` ends so the drip is part of the yield.

**Duration and curve (decision M., 16.10.2026).** The shot duration is the pump time:
from the first frame with `*` to the first frame without it. Telegram, MQTT ("last shot
duration") and Visualizer show the same value. The curve in the shot file, and so the
duration Visualizer shows (the length of `elapsed`), ends at that pump-stop frame. The
5 s tail is only recorded for the yield: `drink_weight` is the maximum weight including
the drip. Reasons (**seen**, two shots 16.10.): the drip ends 1–2 s after the pump stop;
after the stop the pressure sensor reads the sealed-off boiler, not the brew pressure,
and the flow meter rate holds its last value for 1–3 s (docs/protocol.md). The
firmware's display timer is not reproduced; it starts later (see protocol.md, "Shot
timer").

| Class | Rule | Status |
|---|---|---|
| shot | brew pressure above 3 bar while `*` | **seen** (3 runs) |
| flush | `*` without reaching 3 bar | **seen** (2 runs) |
| tank_empty | flush ≥ 5 s with 9 bar target, mean flow < 100 ml/min | **unverified** |

Shot values: duration (from `*`), yield (max. scale weight incl. tail), volume (flow
meter, reads ~6 % low), peak pressure, first drop (first scale reading above
0.2 g; in the 13.10. shots the first readings were 0.3 / 0.6 g, about 0.3 s after M. saw
the drop, **seen**), mean flow by weight after the first drop, boiler temperature at start
and minimum, profile name (selected profile, frame positions 272–280), plus dose, ratio,
bean and grinder setting from the settings entities.

The shot file also keeps series Visualizer has no place for (pump phase angle, pressure
band min/max) under `leva2mqtt.series`; they are not uploaded. A flow tracking target
(frame 281–283, leva! 3.0, `???` on M.'s machine) goes to Visualizer as `flow.goal` in
ml/s; whether Visualizer shows it is **unverified**.

Replaying the `shots` capture gives 21.4 s / 42.3 g and 18.1 s / 42.9 g; M. read
42.3 g and 42.9 g off the scale.

Only shots are uploaded, saved and announced. All runs are published on `…/event`.

## Shot delivery

A finished shot goes through a small persistent queue, so a crash, a restart or an
outage of the internet or of Visualizer cannot lose it:

1. The summary goes to MQTT at once (`event`, `last_shot`).
2. The shot is written to `shots/pending/<date>_<time>_shot.json` **before** anything
   is sent (written to a temp file, then renamed).
3. Visualizer upload, then the coffee bag link (only if a bag was selected), then the
   Telegram message. The targets are fixed when the shot
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

## Coffee bags (Visualizer Premium)

Setup for users: [visualizer.md](visualizer.md).

With Premium and coffee management switched on in the Visualizer settings, the bridge
offers the active (not archived) coffee bags as the select entity **Coffee bag**. The
selection is stored with each shot when it is taken; after the upload the bridge links
the shot to the bag, and Visualizer fills roaster, coffee, roast date and roast level
from the bag.

**Bean or Coffee bag, never both.** As soon as Visualizer returns at least one coffee
bag, the free-text **Bean** entity is removed from Home Assistant and a leftover Bean
value is no longer sent with shots. When the list becomes empty again (bags archived,
coffee management or Premium off), **Coffee bag** disappears and **Bean** comes back.
Accounts without coffee management only ever see **Bean**.

- The list is loaded at start, every `visualizer.coffee_bags_refresh` seconds and with
  the button **Reload coffee bags**. On an error the last list stays.
- No bags: no select entity. A selected bag that is archived or deleted is cleared.
- Labels are `<coffee> – <roaster>`; when two bags look the same, the roast date
  (or a short id) is added.
- The link is its own queue step: it waits for the upload, is retried like the upload,
  and is dropped if the upload failed for good or Visualizer rejects it (404 / 422, or
  coffee management off). The upload itself is not repeated for a failed link.

API (`GET /api/roasters`, `GET /api/coffee_bags`, `PATCH /api/shots/<id>` with
`coffee_bag_id`) as read from Visualizer's public source on 13.10.2026; **unverified**
against the live service until the first linked shot.

## Grinders

Setup for users: [visualizer.md](visualizer.md#3-grinders).

Visualizer keeps no grinder objects: it only collects the `grinder_model` values of
past shots as dropdown suggestions, and the API does not offer them. So the grinders
come from `shots.grinders`. The chosen one goes into each shot as `grinder_model`
(Visualizer then adds it to its suggestions) and into the Telegram message. The grind
setting stays a text entity.

## Telegram message

Sent with `parse_mode=HTML`, link preview off. Lines without a value are left out
(no scale → volume instead of weight; no bag, bean or grinder → no coffee lines; upload
not done yet → no link). Names from Visualizer and the configuration are HTML-escaped. The headline is the coffee
(Visualizer coffee bag, else the free-text bean); without one it is a cup, the machine
name and "Shot finished".
Example (`telegram.language: de`):

```
🫘 <b>CUERPO – Südhang</b>
⏱ <b>21 s</b> · <b>42,3 g</b> aus 18,0 g (1:2,3)

📈 Spitze <b>7,7 bar</b>   💧 erster Tropfen nach 9 s
🌡 Kessel 90,8 °C beim Start
⚙️ DF64 · 2,6

📊 <a href="https://visualizer.coffee/shots/…">Auf Visualizer ansehen</a>
```

A message sent more than 2 min after the shot names its time in a second line
(`Shot von 16:05 (verspätet gesendet)` / `Shot from 16:05 (sent late)`).

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
| `slots_full` | `ON` when the module seems to refuse further connections, retained |
| `command/<wake\|eco\|standby\|report\|refresh_coffee_bags>` | payload `PRESS` |
| `settings/<key>` / `settings/<key>/set` | retained runtime settings, restored after a restart |

## Home Assistant entities

Sensors appear through MQTT discovery **only after their value has been seen in a
frame**, so a machine without scale or flow meter gets no weight or flow entities.

- Sensors: state, boiler temperature, setpoint, heater output, heater output last
  minute, brew pressure (outside a pump run this is the pressure of the sealed boiler,
  typically 4–5 bar after a shot, not a fault – see protocol.md), target pressure, weight, flow by weight, flow meter rate,
  flow meter volume, control mode, profile, pump time (running time of the `*` flag,
  else the last run; the bridge's own clock), heater average spread (diagnostic,
  attribute `heater_averages` = the last three per-minute averages; ready needs a spread
  below 1.0), PID terms and constants Kc / Ti / Td (diagnostic, disabled by default),
  last shot (timestamp, attributes = shot record), last shot yield, last shot duration,
  shots waiting for delivery (diagnostic), maintenance alerts, dropped frames
  (diagnostic). The profile sensor has the attribute `slots` (the profile names in the
  four slots).
- Diagnostic sensors from the frame: pump phase angle (on) and its bias / P / I terms,
  pressure band min / max, PID feed-forward, wind-up guard and temperature rate
  (disabled by default)
- Optional sensors, only announced when the machine reports them: PCB and AUX 1–3
  temperatures, flow tracking target, PID 2 (control mode, temperature, setpoint,
  output; terms and constants diagnostic and disabled) – PID 2 only while its control
  mode is not `OFF`
- Binary sensors: pump, heating up, boiler at temperature, ready, steam mode, hot water,
  maintenance alert (problem), other client connected (diagnostic), connection slots
  full (diagnostic, problem)
- Button **Mark capture** (diagnostic), only with `capture.enabled`
- Buttons: wake (`MCa`), eco (`MCe`), standby (`MCE`), beep setpoint
- Settings: dose (number); grinder setting (text); bean (text, only while Visualizer
  has no coffee bags) or coffee bag (select, only with bags on Visualizer) and a button
  to reload them; grinder (select, only with `shots.grinders`); Visualizer upload and
  Telegram message (switches, only when configured). Empty text entities show "None";
  entering "None" clears them.

## Raw capture

With `capture.enabled` the bridge records everything it exchanges with the module in
the format of `tools/capture.py` – so `replay.py`, `fake_ito.py` and the analysis of a
problem work on normal operation, without a separate capture session.

- **One file per switch-on:** a file starts when the connection to the module comes up
  and ends when it is lost – with the machine on a smart plug, one file from switching
  on to switching off. Name `<directory>/<YYYY-MM-DD>_<HHMMSS>_bridge.txt` (start, local
  time). A reconnect within 60 s (WLAN hiccup, watchdog) continues the same file; a
  connection longer than 24 h (module powered all the time) starts a new file.
- Plain text, no compression. Files older than `keep_days` are deleted when a new file
  starts. Nothing is written while the machine is unreachable.
- Content: every line received (`MCr` frames, display frames of the alert polls,
  heartbeats, replies), and `#` event lines: connected / lost, every command sent
  (`# >> MCr`), the bridge's decisions (`# bridge: state brewing`,
  `# bridge: power mode ECO -> PID`, `# bridge: run {…}` with the shot record), buttons.
- Button **Mark capture** (diagnostic, only with `capture.enabled`) writes
  `# mark (button in Home Assistant)` – for "the ready beep came now" or "this shot
  looked wrong". Outside a connection there is no file and the mark is dropped.
- Recording is passive: it sends nothing and does not change the bridge's behaviour.
  A write error (disk full, no permission) disables it with one warning in the log.
- Size: an `MCr` frame is ~300 bytes at 4/s, about 4 MB per hour the machine is on.
- Not covered: whatever needs another report mode or another client – a shot in
  display mode (playbook scenario 12) or a fifth connection. Whether the machine beeped
  is only in the capture if someone presses **Mark capture**.

Fetching from the test LXC to a Mac: `tools/fetch-captures.sh` copies the captures to
`~/Silvia/captures/bridge` and archived shot files (with `shots.keep`) to `~/Silvia/shots`
(see the script header; defaults: Proxmox host `root@10.10.10.30`, CT 103, volume
`leva2mqtt-test_data`).

## Not done yet

- A capture with a real alert (scenario 7, backflush) to confirm the alert position
  and blinking. (Shot screen in display mode: captured 13.10., parser adjusted.)
- Multi-arch images (amd64 / arm64 / armv7) and the Home Assistant add-on.
- Backflush detection (no capture yet).

## Development tools

- `tools/replay.py <capture>` – run a capture through the detection logic and print
  states, beeps and runs.
- `tools/monitor.py <ito-ip>` – live view of all decoded fields and the ready decision,
  see [monitor.md](monitor.md).
- `tools/fetch-captures.sh` – copy the bridge's raw captures and archived shots from the
  test LXC to the Mac.
- `tools/fake_ito.py <capture> --port 2300` – serve a capture as a fake module; start
  the bridge with `MACHINE_HOST=127.0.0.1 MACHINE_PORT=2300`.
- `python -m pytest tests` – unit tests with synthetic frames.
