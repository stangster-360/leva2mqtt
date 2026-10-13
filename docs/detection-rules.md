# Detection rules for the bridge

Derived from the captures in [protocol.md](protocol.md). Data source is the `MCr` frame
(`f[a:b]` = 0-based character positions, end exclusive). Every rule is marked like in
protocol.md; **unverified** rules rest on one capture or on an assumption and must be
checked with more captures before they are relied on. Fields that read `?` are skipped,
a missing sensor never raises an error.

## 1. Link

| Situation | Rule | Status |
|---|---|---|
| Connected | send `MCr` after every connect (the report mode of a previous client may linger) | **seen** |
| Machine reachable | frames arrive; reconnect every 2 s while the connect fails | **seen** |
| Machine off / power loss | the TCP connection does not close. No frame for 2–3 s → treat as lost, close, reconnect. Send a bare newline every 5 s as keepalive (the first one after a reboot is answered with a TCP reset, then a reconnect works) | **seen** |
| Heartbeat only (`$id: leva! (3.0)` once per second, no frames) | re-send `MCr` every 5 s until frames come. An `MCr` sent right after a module start is lost; a re-sent one brought frames within 0.4 s | **seen** (once) |
| Complete silence (no frames, no heartbeat) | machine off: the watchdog above, reconnect after 2–3 s | **seen** |
| Reachable again after power-on | module answers 4–20 s after power-on | **seen** |
| Wrong kind of frames (display frames while the bridge wants `MCr`, outside its own alert poll) | another client switched the global mode. Seen with the Leva Companion app: an `XML=…` line and a setup dump appear in the stream, then display frames. Re-sending `MCr` at once would break the app's display, so: expose "other client active" in HA, keep publishing what the display frames carry (temperature, setpoint, heater, pressure, weight), and switch back to `MCr` only after no `XML=`/dump lines and no display input events for a few minutes. Interval **unverified** | **seen** (cause), handling **proposal** |
| Second client | works, both get the same frames. The report mode is global: `MC@` from one client switches all of them. The bridge should be the only client | **seen** |

## 2. Machine state (from `f[4:7]` and `f[2]`)

| State | Rule | Status |
|---|---|---|
| standby | `f[4:7] == "???"` (frame almost only `?`, tail `~ 59`) | **seen** |
| eco | `f[4:7] == "ECO"`, setpoint field shows the eco setpoint | **seen** |
| awake | `f[4:7] == "PID"` | **seen** |
| other modes | `OFF`, `CLD`, `ATN`: treat as awake, expose the raw text | **dev** |

Transitions (the display text is not needed):

- standby or eco → awake: **wake event**. Send `MCcREPORT SP1`, then `MCr` again
  (REPORT replaces the report mode?). The beep confirms to the user that the machine is
  up. **seen** that `MCcREPORT SP1` wakes the machine and beeps; that a following `MCr`
  is needed after it is **unverified** (REPORT and `MCr` did not interfere in the
  captures).
- Bridge start with the machine already awake: no command that beeps. **requirement**
- Machine unreachable → reachable and awake: same as wake event. **requirement**
- Never send `MCcREPORT SP1` while the machine is in standby or eco unless a wake-up is
  wanted: it wakes the machine. **seen**
- Automatic eco and standby come from the setup (here 30 min / 45 min after power-on or
  last activity). **seen**

## 3. Heating and ready

**Decision (M., 13.10.):** the bridge provides its own ready logic and its own Home
Assistant entity for it. It does not depend on the firmware's `READY`
(shown for only 10 s, only in the display stream). The firmware's own decision could be
reproduced from `MCr` in two captures, so the bridge uses the same condition:

| State | Rule | Status |
|---|---|---|
| heating | awake and temperature `f[7:13]` more than 1.5 K below the setpoint `f[13:18]` | **seen** |
| boiler at temperature | temperature within ±0.5 K of the setpoint | **seen** |
| ready | boiler at temperature **and** the last three values of the per-minute heater average `f[73:78]` (it updates once a minute) lie within less than 1.0 percentage point. Evaluate at each update of the field. Reproduced the firmware's ready beep to within seconds twice (cold start ~8.3 min, warm power cycle ~5.7 min) | **unverified** (2 hits) |

- Leaving "ready": temperature more than 1.5 K below the setpoint outside a shot, eco,
  standby or steam mode. Whether the firmware re-arms its beep after a shot: unknown.
- The firmware `READY` replaces the setpoint field for 10 s in display frames (**seen**);
  the bridge may log it when it happens to be in display mode, nothing depends on it.
- Heater output `f[18:21]` in % (`100` at full power), per-minute average `f[73:78]`.
  **seen**

## 4. Brewing (needs `f[2] == "*"`)

`*` means a pump or brew program is running, not necessarily a shot. Classify while it
runs and fix the class at the end:

| Class | Rule | Status |
|---|---|---|
| shot | `*` and measured pressure (`f[197:202]`) has reached > 3 bar | **seen** (3 runs) |
| flush / free flow | `*`, pressure stays < 1.5 bar for the whole run, rate (`f[160:163]`) ~ 480 ml/min, run ≤ 10 s | **seen** (2 runs) |
| tank empty | `*`, target (`f[192:197]`) 9 bar, pressure stays < 3 bar, rate ~ 0 | **unverified** (one old capture) |
| water through the steam wand | `*`, high flow, low pressure, almost no weight | **unverified** |

A shot starts like a flush (pre-infusion at ~480 ml/min below 1.5 bar for about 9 s).
The class is therefore `flush` at first and becomes `shot` when the pressure crosses
3 bar. A flush that is stopped before that never becomes a shot.

Shot record (published when `*` has been `-` for 5 s, to catch the drip):

- start/end time from the `*` flag and the bridge clock, duration
- yield: maximum of `f[226:231]` (scale) until 5 s after the end; without a scale the
  flow meter volume `f[152:155]`, which reads ~6 % low
- peak pressure, average flow (`f[231:236]` or from volume), start/end temperature
- first drop time: first sample where the weight rises above 0.5 g (**seen** as the time
  the weight starts to rise; threshold **unverified**)
- dose is not in the frame; take it from an HA entity set by the user (the setup dump
  lists dose presets)

The shot ends on its own at the target weight when a scale is connected (pump stops
at ~39 g for a 40 g target). **seen**

## 5. Hot water and steam

| Situation | Rule | Status |
|---|---|---|
| Hot water | `f[2] == "-"` and the flow meter volume `f[152:155]` increases (the counter is only reset when the next `*` starts). One run is ~16 s, ~107 ml, with pressure spikes ~10 bar at start and end | **seen**, 1 capture |
| Steam mode | boiler temperature ≥ 100 °C while the setpoint is ~90 °C; heater output 0 and a strongly negative P term (`f[21:27]`). The machine's steam switch heats outside the PID | **seen** (confirmed by M.), threshold **unverified** |
| Steam drawn | steam mode and temperature falling with pressure (`f[197:202]`) dropping to ~1 bar | **seen**, 1 capture |
| Steam mode left | temperature back below 100 °C | **unverified** |

## 6. Not detectable with `MCr` (yet)

- Alerts (`BACKFL`, `DSCALE`, `FILTER`, `REFILL`, `PROTECT`, `VALVE`, `ERR:…`) and
  `READY` appear only in display frames (`MC@`), which replace `MCr`. A second
  connection does not help (the mode is global, **seen**); switching modes costs
  0.1–0.6 s of data.
  **Decision (M., 13.10.):** `MCr` stays the data source. The bridge derives
  "ready" itself and, only while idle (no `*`, no steam), switches to `MC@` for about a
  second every few minutes to read maintenance alerts, then back to `MCr`. If a shot
  starts in that second (display shows the shot screen), it switches back at once; at
  most ~0.6 s of pre-infusion is lost, about 1 in 300 shots. Interval: every 5 min for
  about 1 s.
- Backflush: not captured (scenario 7).
- Idle pressure is thermal expansion and no pump signal.
