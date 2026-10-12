# ito / leva! telnet protocol – field notes

Observations from a Rancilio Silvia with an ito module, leva! firmware, Bluetooth scale,
flow meter and brew pressure sensor. Every statement is marked:

- **seen** – confirmed in our own captures
- **dev** – stated by the firmware developer in public forums, not yet confirmed here
- **unverified** – our assumption, needs a capture

Formats may differ with other firmware versions and hardware – captures from other
setups are welcome (see [capturing.md](capturing.md)).

Placeholder: `<ito-ip>` for the module's address.

## Connection

- TCP port **23**, plain TCP, latin-1. No telnet negotiation needed. **seen**
- Port 2323 is the bootloader used for flashing. Do not use it. **dev**
- The serial menu must be allowed in leva!: *Setup → Wifi → Permissions → Serial menu*,
  otherwise commands have no effect. **dev**
- Commands can be sent while the `MCr` report is streaming. **seen**
- `MCr` and `MC@` **replace each other**: after `MC@` only display frames arrive, after
  `MCr` only `{…}` frames, never both. The switch takes effect within 0.1–0.5 s. **seen**
- Display frames arrive at 10 per second (about 0.1 s apart), `MCr` frames at 4 per
  second. After `MCr` the first frame came 0.46 s later, and it was sent twice. **seen**
- Accepted line endings: CR+LF, LF alone and CR alone all work (**seen**: each `MCR` sent with LF (2×) or CR (1×) was answered with an extra frame 0.05 s later; CR+LF is what the other commands use).
- On connect the module streams **display frames** (`<…>`) by default. **seen** But the report mode seems to survive a disconnect: a connection that sent nothing started with `{…}` frames when the previous client had left `MCr` running, and with display frames when the previous one had ended in `MC@` (3 captures). A client therefore has to send its wanted mode (`MCr`) explicitly after every connect. **seen**, rule **unverified**
- A heartbeat `$id: leva!` once per second is described by the developer. It appeared
  as `$id: leva! (3.0)`, exactly once per second (118 s without a gap), in one capture in
  which the module sent **no** `{…}` frames although `MCr` was sent (twice, also after a
  reconnect). It never appeared in any capture in which the machine delivered report or
  display frames. The state of the machine during that capture is unknown, so what the
  heartbeat means (module alive, machine side silent?) is **unverified**. **seen**
  The state started about 2.4 min after a power-on and survived a second power cycle
  (at least 48 s after switching on); a plain connection 5 min later delivered `{…}`
  frames within 0.3 s. After the first cold start of the day (machine off for hours) the
  module was reachable within 17–20 s of switching on and delivered display frames at
  once, without any heartbeat. Why the state occurred and how long it lasts: **unknown**.
- *From the Leva Companion's behaviour:* the module closes a connection after ~10 s
  without client input. **Not confirmed:** a client that sent nothing for 40 s while
  `MCr` was streaming (no keepalive) stayed connected. Not tested with display frames
  only. **seen**
- Waking the machine with `MCa`: in a Leva Companion log the output switched back to
  display frames. In our own capture `MCr` kept streaming through the wake-up, no new
  `MCr` was needed. **seen** The same holds when the machine is woken with its button:
  the `?1???…` standby frames turned into `PID` frames without a gap and without any
  command from the client, and M. heard no beep. **seen** (one capture)
- When the module loses power it does **not** close the TCP connection: a client that
  sent nothing (no keepalive) saw the frames stop and then nothing at all – no FIN, no
  reset, no error – for 60 s after the power came back. A client has to notice the missing
  frames itself (watchdog, e.g. no frame for 2–3 s while `MCr` is streaming) and
  reconnect. **seen** (one capture)
- With the 5 s keepalive newline on, the first newline after the module was back up was
  answered with a TCP reset (`Connection reset by peer`), the reconnect then worked
  immediately. The module was reachable again 4–9 s after switching on (the keepalive
  period and the moment of the switch limit the precision). **seen** (one capture)
- The report mode after a power cycle could not be determined (see the heartbeat
  remark above). **unverified**
- From standby, connecting, the keepalive newline, `MC@` and `MCr` do **not** wake the
  machine (display stays `STANDBY`). **seen** (two captures)
- `MCcREPORT SP1` **does** wake it from standby: the display changed from `STANDBY` to
  the main screen 0.14 s after the command and the machine started heating. **seen**
  The developer stated in 2018 that "almost any command" wakes it. Other commands
  (`MCR`, from eco): **unverified**.
- `MCcREPORT SP1` also wakes the machine from **eco**: the setpoint field changed from
  `069.90` to `090.00` 0.6 s after the command. **seen**
- Beeps (heard, not in the capture): after `MCcREPORT SP1` in standby only the report
  beep (short + long) was heard, no separate wake-up beep. After `MCcREPORT SP1` in eco M.
  heard a long beep pattern instead. The manual says the report is relative to the reset
  setpoint (one short beep per °C below it, one long beep per °C above it, short + long
  when equal), so in eco (69.9 °C against the reset value of 90.0 °C) about 20 short beeps
  are expected. The number was not counted. **unverified**
- Mode changes by command, MCr running (one capture, no gaps in the 4 frames/s stream):
  `MCa` from eco: `ECO` → `PID` in 0.3 s; `MCe` from awake: `PID` → `ECO` in 0.1 s;
  `MCE`: frame turns to `?1???…` in 0.3 s; `MCa` from standby: `PID` in 0.3 s. **seen**
- After the `MCcREPORT SP1` from eco the machine showed the **PROTECT** alert at some
  point (M.; the capture ends at 138 s because the Mac's battery ran out, 19 s after the
  command). The frames up to then showed nothing unusual (heater output 8–11 %). The
  manual says PROTECT is raised when an on-time limit of the heater (PID 1, high power
  for 10 min), pump (2 min, 1:1 balance), paddle or the PCB temperature limit is breached;
  heat, pump and valve are then off until the machine is switched off, and the cause
  is shown in the info menu (line "Protect"). Cause here: **unknown**.
- Assume **one client at a time**. Two clients in parallel: **unverified**.

## Commands

The full list is in the firmware manual (appendix "Serial menu") and via `MCh`.
Only use a fixed whitelist – several commands change PID values or toggle reporting.

| Command | Effect | Status |
|---|---|---|
| `MC?` | Identify; replies `XML=leva!-XXXXXXXX` – probably the name of the matching XML definition file (firmware version), not a module ID | reply **seen**, meaning **unverified** |
| `MC@` | Report display | **seen** |
| `MCr` | Report sensor data continuously, 4 frames/s | **seen** |
| `MCR` | Report sensor data once: inside a running `MCr` stream it adds one extra frame about 0.05 s after the command | **seen** |
| `MCu` | Dump setup (read only) | **seen**, ~1100 lines in ~3 s |
| `MCh` | Help / command list | **dev** |
| `MCa` | Wake from energy saver; `MCr` keeps streaming | **seen** |
| `MCe` / `MCE` | Enter eco / standby | **seen** |
| `MCcREPORT SP1` | Beep setpoint 1: short + long when the setpoint equals the PID reset value. **Also wakes the machine from standby.** | **seen** |
| `MC<space>` | Toggle reporting – avoid | **dev** |
| `MC+` `MC-` `MCp/P` `MCi/I` `MCd/D` `MCw/W` | Change setpoint / PID values – never send | **dev** |

## Display frames

```
<CCC…text…>
```

`CCC` is a three-digit code followed by the display text.

| Code seen | Screen |
|---|---|
| `002` | Standby: `<002   …   STANDBY   …>`. The same code was also seen once with main-screen text (`GEN.LEVER … 90.3ß … 17%`) – meaning of that case **unverified** |
| `102` | Main screen and shot screen |
| `101` | Menu |
| `191` | Menu item being confirmed |

The text uses `ß` (0xDF) as degree sign and control characters `\x01`–`\x04` as
bar-graph and arrow glyphs. Strip them before parsing.

### Main screen

```
<102GEN.LEVER       1: 90.1ß  90.0ß     7% 1.8b-  0s   0.0g>
     profile           actual  setpoint heater pressure  last shot time  weight
```

- Pressure is the brew circuit. At idle it reads 0.2–2.0 bar and rises with boiler
  temperature (thermal expansion in the closed circuit). **seen**
- Last field: weight in `g` with a scale, otherwise volume in `ml`. **seen**
- Alerts appear between setpoint and heater output and may blink: `BACKFL` (**seen**),
  `DSCALE`, `FILTER`, `REFILL`, `PROTECT`, `VALVE`, `ERR:…` (manual).
- `READY` is shown only **briefly**, together with the ready beep. A parser has to
  watch every frame to catch it. **seen** (observed on the machine, not yet in a capture)

### Shot screen

```
<102 12s 24.7g 1: 88.8ß 90.0ß R=3.4 7.3 7.3b 24.7g>
     time weight   actual setpoint flow pressure target weight
```

- `time` – shot timer in seconds; `R=` – flow by weight in g/s; then measured
  pressure and target pressure of the profile in bar. **seen**
- With a scale connected, the flow meter value is not shown here. **seen**

## Rich frames (`MCr`)

```
{ -1PID090.15090.0010-001.20011.20000.00000.0000.0000009.4058.0014.5020.0008.7OFF???…???~ A9}
```

- Fixed width, 289 characters, `{ … }`, 4 frames per second at a steady 0.25 s. **seen**
- Undefined values (sensor not installed, not active) are `?`. Skip them. **seen**, **dev**
- The developer describes field positions, lengths and types in an XML file that is part
  of the licensed firmware download. It is not included here. **dev**
- In standby the frame is almost entirely `?`. **seen**
  Set characters in all 63 standby frames of one capture: position 3 = `1`, positions
  163–167 = `001.0` (not decoded), and the tail `~ 59`. Position 2 (`*`/`-`) and
  positions 4–6 (`PID`) are `?`. All frames were identical. **seen**
- Candidate rule for the state "awake": position 2 is `-` or `*`, or positions 4–6 read
  `PID` (the other way round for standby). Seen in two captures with one transition each,
  **unverified** as a rule (eco not captured yet).
- After `MCa` the first rich frame came 0.29 s later with real values (boiler 61.5 °C,
  heater 100 %). **seen**

### Decoded fields (0-based character positions)

| Pos. | Example | Meaning | Status |
|---|---|---|---|
| 2 | `-` / `*` | `*` = pump / brew program active. **Not** the same as "shot": it also stays set while the pump runs dry or water is drawn through the steam wand. | **seen** |
| 4–6 | `PID` | Control mode of PID 1 (`PID`, `ECO`, `OFF`, `CLD`, `ATN` per developer). `PID` when awake, `ECO` in eco (with the eco setpoint in the next field and no heater output), `???` in standby. `OFF`, `CLD`, `ATN` not seen | partly **seen** |
| 7–12 | `090.15` | Boiler temperature °C | **seen** |
| 13–18 | `090.00` | Setpoint °C (shows the eco setpoint `069.90` while in eco) | **seen** |
| 19–20 | `17` | Heater output % (= P + I + D, clamped) | **seen** |
| 21–26 / 27–32 / 33–38 | `0001.1` `0007.1` `0008.5` | PID terms P, I, D | **seen** |
| 45–52 | `-00.0625` | Rate of temperature change, used by the D term | **seen** |
| 53–57 / 58–62 / 63–67 | `009.4` `058.0` `014.5` | PID constants Kc, Ti, Td | **seen** |
| 73–77 | `019.5` | Average heater output of PID 1 in % over the previous minute, updated about once per minute. Mean of the heater field over the window: 19.5 % → `019.5`, 0.0 % → `000.0` (steam capture). Matches the manual's "average output per minute". | **seen** |
| 152–154 | `098` | Flow meter volume, ml. Counts from 0 when the pump starts. Reference run on a primed group, empty portafilter: counter 96 ml, scale 102.3 g (counter ~6 % low; in flight ~3–4 % low, and ~3 g more arrives after the pump stops). The first run (group not primed) showed the opposite sign, so check again with more runs before correcting by a factor. | **seen**, calibration **unverified** |
| 155–159 | `00241` | Flow meter **pulses** (2.07–2.10 per ml: 241 pulses for 115 ml, 201 for 96 ml). Not a shot timer. | **seen** |
| 160–162 | `500` | Flow meter rate in **ml/min** (not ×0.01 ml/s). In an empty-portafilter run the volume rose 111 ml in 13.4 s = 497 ml/min while the field averaged ~500, so counter and rate agree. The field stays within ±5 % of 500 during the run (filtered / interpolated), then decays (`335`, `066`, `000`) after the pump stops. The earlier "~40 % too low" reading came from assuming ×0.01 ml/s. | **seen** (two captures; second run: 90 ml in 11.1 s = 487 ml/min, field average ~490) |
| 192–196 | `08.25` | Target pressure of the active profile, bar. In the second flow run it read `09.00` from the pump start and fell to `07.64` over the last 5 s (the lever decay). In the first flow run the same field showed 0.2–0.6 and no profile curve – why is **unverified** (different way of starting the pump?). | **seen** |
| 197–201 / 202–206 / 207–211 | `08.20` `08.10` `08.45` | Brew pressure, bar – probably current, min, max of the last interval | **unverified** |
| 226–230 | `042.1` | Scale weight, g. Matches the scale display (final 102.3 g in the frame, 102.3 g read off the scale). Reads `000.0` after the cup is removed. | **seen** |
| 231–235 | `03.12` | Flow by weight, g/s | **seen** |
| near end | `GEN.LEVER` | Active profile name | **seen** |
| last 2 before `}` | `A9` | Probably a checksum – not a plain byte sum or XOR | **unverified** |

Not decoded yet: 39–44, 212–225 (only during a profile), the `OFF` marker at 78–80.

There is **no shot timer** in the rich frame. Shot duration has to be measured by the
client from the `*` flag and its own clock.

According to the developer, the flow meter delivers only 1–2 pulses per second during
an espresso and the rate is interpolated in between – weight is the more reliable
measure. **dev**

### Observed signatures (single captures, **unverified** as rules)

| Situation | Pattern |
|---|---|
| Normal shot | `*`, pressure follows the target, weight rises |
| Tank empty | `*`, target 9 bar but pressure stays ~2–2.6 bar, flow meter almost zero |
| Water through the steam wand | `*`, high flow meter volume (~6 ml/s), low pressure, almost no weight |
| Cold start | 21.5 → 82 °C in 2.5 min, 89 °C after 3.6 min (boiler only, group still cold) |
| Espresso shot, lever profile (18 g in; 42.3 g / 42.9 g out) | `*` for 21.1 s / 17.9 s. Pre-infusion at ~480 ml/min with measured pressure below 1.5 bar until ~68–69 ml are pumped (both shots), first drop 9.3 s / 8.8 s after pump start. Pressure then builds to 6.9–7.6 bar and follows the falling target; flow meter rate falls to 200–290 ml/min, weight rate 3.0–4.3 g/s. Boiler drops 4.5 K, heater output 90 %+. The pump stopped at 39.1 g / 39.3 g in the cup, another 3 g dripped in afterwards. The firmware ended both shots by weight (target of 40 g reported by M.; the setup dump lists a "Dbl Espresso" dose preset with Out 40.0 g and a scale setting "Stop early 0.5g" – which preset was active is **unverified**), so the end of `*` is the end of the shot. |
| Flush / free flow | `*` for 4.5 s and 8 s, rate constant ~480 ml/min, measured pressure stays below 1.5 bar, weight rate 10–15 g/s. The target field shows `09.00` like in a shot. A cup stood on the scale (M.), and these flushes did not stop at the target weight. Weight (45.6 g, 91.5 g) was higher than the flow meter volume (39 ml, 66 ml; 8 s at 480 ml/min would be 64 ml) – why is **unverified**. |
| Hot water | `*` stays `-`. The flow meter keeps counting from its last value (114 → 221 ml, 239 → 463 pulses; the counter is only reset when the next `*` starts). Sequence: pump against the closed valve for ~3 s (pressure up to 10.3 bar, rate ~240 ml/min), valve opens: pressure 0.2–0.4 bar, rate ~500 ml/min for ~10 s, valve closes: pressure back to ~9.9 bar for ~2.5 s, pump stops. About 16 s and 107 ml in total; boiler 90.75 → 83.7 °C, heater 90 %+ for recovery. One capture. |
| Steam | Machine steam switch flipped at ~59 s (M.; the "steam on" note was typed shortly before). From then the boiler rose to 118.45 °C although the setpoint field stayed `090.00`, heater output was `00` and the PID P term strongly negative (−120 … −262): the heating happens outside the PID, through the steam switch of the machine, which bypasses the PID (confirmed by M.; the frame data fit it). The brew pressure sensor follows the boiler temperature (5.0 bar at 84 °C, 6.2 bar at 110 °C). Steam valve opened at ~175 s: temperature fell 118 → 106 °C and pressure fell to ~0.9 bar while steam was drawn, then valve closed; the switch stayed on, temperature rose again. Positions 73–77 stepped `013.3` → `036.7` → `019.5` → `000.0` during the steam phase (not decoded). One capture. |
| Target pressure curve | `09.00` from pump start, falls after ~5.5 s by ~0.23 bar/s; 7.64 bar at 10.8 s in both shots. Depends on time since pump start, not on the puck. |
| Idle pressure after a shot | 5.9 bar 10 s after the pump stopped, 4.2 bar after about a minute, 0.5–0.8 bar after a flush. Not usable as a "pump is running" signal. |

## Setup dump (`MCu`)

The settings tree, one item per line, nesting shown with `|`:

```
PID 1...
| Control     PID
| Setpoint  90.0°
| Eco       69.9°
| Reset     90.0°
```

Useful for reading setpoints, doses and profiles without navigating the menu.

Dose presets (sample from one machine, **seen**; the active preset was not identified):

```
Dose...
| Use           -
| Espresso...
| | Method   WEIGHT
| | Out       30.6g
| | In        18.0g
| | Ratio 1 :   1.7
| | Focus     RATIO
| Dbl Espresso...
| | Method   WEIGHT
| | Out       40.0g
| | In        20.0g
| | Ratio 1 :   2.0
| | Focus       OUT
```

A preset has `Method` (`WEIGHT` or `TIME`, then `Duration`), `Out`, `In`, `Ratio` and
`Focus`. The scale menu also holds `Stop early 0.5g`. How the bridge can learn which
preset is active is **unverified**.
