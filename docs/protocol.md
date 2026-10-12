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
- Accepted line endings: CR+LF works (**seen**); LF alone is said to work (**dev**).
- On connect the module streams **display frames** (`<…>`) by default. **seen**
- A heartbeat `$id: leva!` once per second is described by the developer, but has not
  appeared in any of our captures so far. **dev, unverified**
- *From the Leva Companion's behaviour:* the module closes a connection after ~10 s
  without client input. **unverified**
- Waking the machine (`MCa`) switches the output back to display frames; `MCr` has to
  be sent again afterwards. **seen** (in a Leva Companion log)
- Whether connecting or sending a command wakes the machine from standby/eco is
  **unverified**. The developer stated in 2018 that "almost any command" does.
- Assume **one client at a time**. Two clients in parallel: **unverified**.

## Commands

The full list is in the firmware manual (appendix "Serial menu") and via `MCh`.
Only use a fixed whitelist – several commands change PID values or toggle reporting.

| Command | Effect | Status |
|---|---|---|
| `MC?` | Identify; replies `XML=leva!-XXXXXXXX` – probably the name of the matching XML definition file (firmware version), not a module ID | reply **seen**, meaning **unverified** |
| `MC@` | Report display | **seen** |
| `MCr` | Report sensor data continuously, 4 frames/s | **seen** |
| `MCR` | Report sensor data once | **dev** |
| `MCu` | Dump setup (read only) | **seen**, ~1100 lines in ~3 s |
| `MCh` | Help / command list | **dev** |
| `MCa` | Wake from energy saver | **seen** |
| `MCe` / `MCE` | Enter eco / standby | **seen** |
| `MCcREPORT SP1` | Beep setpoint 1: short + long when the setpoint equals the PID reset value | **seen** |
| `MC<space>` | Toggle reporting – avoid | **dev** |
| `MC+` `MC-` `MCp/P` `MCi/I` `MCd/D` `MCw/W` | Change setpoint / PID values – never send | **dev** |

## Display frames

```
<CCC…text…>
```

`CCC` is a three-digit code followed by the display text.

| Code seen | Screen |
|---|---|
| `002` | Standby |
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

### Decoded fields (0-based character positions)

| Pos. | Example | Meaning | Status |
|---|---|---|---|
| 2 | `-` / `*` | `*` = pump / brew program active. **Not** the same as "shot": it also stays set while the pump runs dry or water is drawn through the steam wand. | **seen** |
| 4–6 | `PID` | Control mode of PID 1 (`PID`, `ECO`, `OFF`, `CLD`, `ATN` per developer). Only `PID` seen; standby showed `???` | partly **seen** |
| 7–12 | `090.15` | Boiler temperature °C | **seen** |
| 13–18 | `090.00` | Setpoint °C | **seen** |
| 19–20 | `17` | Heater output % (= P + I + D, clamped) | **seen** |
| 21–26 / 27–32 / 33–38 | `0001.1` `0007.1` `0008.5` | PID terms P, I, D | **seen** |
| 45–52 | `-00.0625` | Rate of temperature change, used by the D term | **seen** |
| 53–57 / 58–62 / 63–67 | `009.4` `058.0` `014.5` | PID constants Kc, Ti, Td | **seen** |
| 152–154 | `098` | Flow meter volume, ml | **seen** |
| 155–159 | `00207` | Flow meter **pulses** (≈ 2.07 per ml). Not a shot timer. | **seen** |
| 160–162 | `238` | Flow meter rate ×0.01 – matches the volume at low flow, but reads ~40 % lower than the volume counter at high flow | **unverified** |
| 192–196 | `08.25` | Target pressure of the active profile, bar | **seen** |
| 197–201 / 202–206 / 207–211 | `08.20` `08.10` `08.45` | Brew pressure, bar – probably current, min, max of the last interval | **unverified** |
| 226–230 | `042.1` | Scale weight, g | **seen** |
| 231–235 | `03.12` | Flow by weight, g/s | **seen** |
| near end | `GEN.LEVER` | Active profile name | **seen** |
| last 2 before `}` | `A9` | Probably a checksum – not a plain byte sum or XOR | **unverified** |

Not decoded yet: 39–44, 73–77 (changes during heat-up and shots), 212–225 (only during
a profile), the `OFF` marker at 78–80.

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
