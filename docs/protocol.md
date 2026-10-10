# ito / leva! telnet protocol – field notes

Observations from a Rancilio Silvia with an ito module, leva! firmware, Bluetooth scale
and brew pressure sensor. Everything here was seen on real hardware unless marked
otherwise. Formats may differ with other firmware versions and hardware options –
captures from other setups are welcome.

Placeholders: `<ito-ip>` for the module's address, `XXXXXXXX` for the module ID.

## Connection

- TCP port **23**, plain text, latin-1. Lines are terminated with CR/LF.
- The serial menu must be allowed in leva!: *Setup → Wifi → Permissions → Serial menu*.
- On connect the ito starts streaming **display frames** (`<…>`) on its own, about
  4–10 per second.
- *Reported, not yet verified:* the firmware closes connections after roughly 10 s
  without client input. Sending a bare newline every few seconds keeps it open.
- Assume **one client at a time**. Running two tools against the same module leads
  to interleaved output and lost commands.

## Commands

Commands are sent as text followed by CR/LF. The full list is in the firmware manual
(appendix "Serial menu"); these are the ones relevant here.

| Command | Effect | Observed |
|---|---|---|
| `MC?` | Identify | Replies `XML=leva!-XXXXXXXX` |
| `MC@` | Report display | Display frames `<…>` |
| `MCr` | Report sensor data continuously ("rich" frames) | `{…}` frames, see below |
| `MCR` | Report sensor data once | not yet tested |
| `MC<space>` | Toggle reporting on/off | not yet tested |
| `MCu` | Dump setup | Full settings tree, ~1100 lines in ~3 s |
| `MCa` | Wake from energy saver | Works; also switches output back to display frames, so `MCr` has to be re-sent |
| `MCe` / `MCE` | Enter eco / standby | Works |
| `MCcREPORT SP1` | Beep setpoint 1 | Short + long beep when setpoint equals the PID reset value; other patterns encode deviations |

## Display frames

```
<CCC…text…>
```

`CCC` is a three-digit code followed by the display text. The last digit tells the
screen type:

| Code seen | Screen |
|---|---|
| `002` | Standby |
| `102` | Main screen and shot screen |
| `101` | Menu |
| `191` | Menu item being confirmed |

The text uses `ß` (0xDF) as degree sign and control characters `\x01`–`\x04` as
bar-graph and arrow glyphs. Strip them before parsing.

### Standby

```
<002                                    STANDBY                     >
```

### Main screen

```
<102GEN.LEVER       1: 90.1ß  90.0ß     7% 1.8b-  0s   0.0g>
     profile           actual  setpoint heater pressure  last shot time  weight
```

- Pressure is the brew circuit. At idle it reads 0.2–2.0 bar and rises with boiler
  temperature (thermal expansion in the closed circuit).
- The last field is weight in `g` when a scale is connected, otherwise volume in `ml`.
- Shot time and weight of the last shot stay on screen after the shot ends.
- Alerts appear between setpoint and heater output and may blink, so sample several
  frames: `BACKFL`, `DSCALE`, `FILTER`, `REFILL`, `PROTECT`, `VALVE`, `ERR:…` and
  others listed in the firmware manual ("Runtime alerts", "Setup errors").
- `READY` never appeared in our captures, even with the machine stable at setpoint.

### Shot screen

While a shot runs, the layout changes:

```
<102 12s 24.7g 1: 88.8ß 90.0ß R=3.4 7.3 7.3b 24.7g>
     time weight   actual setpoint flow pressure target weight
```

- `time` – shot timer in seconds (no space before it from 10 s on: `<10210s …`)
- `R=` – flow by weight in g/s, from the scale
- first pressure value – measured brew pressure in bar
- second value with `b` – target pressure of the active profile; before the
  profile starts this field shows a temperature-like value instead
- Values update several times per second

When the pump stops, the display returns to the main screen. Weight keeps rising
for a few seconds as the shot drips out.

With a scale connected, the flow meter value does not appear on this screen.

## Rich frames (`MCr`)

```
{ -1PID090.15090.0010-001.20011.20000.00000.0000.0000009.4058.0014.5020.0008.7OFF???…???~ A9}
```

- Fixed width, about 289 characters, enclosed in `{` `}`. Unused positions are `?`.
- The two characters before `}` change with the content – most likely a checksum.
- In standby the frame is almost entirely `?`.
- **Decoded so far:** boiler temperature with two decimals (`090.15`), setpoint
  (`090.00`), the PID constants Kc / Ti / Td (`009.4`, `058.0`, `014.5`), and the
  active profile name near the end.
- **Still open:** weight, flow meter rate and volume, pressure, shot timer and state.
  A capture during a shot is needed to map them.

## Setup dump (`MCu`)

The settings tree, one item per line, nesting shown with `|`:

```
PID 1...
| Control     PID
| Setpoint  90.0°
| Eco       69.9°
| Reset     90.0°
| Ramp&Soak...
| | Settled ±  1.0°
```

Useful for reading setpoints, doses and profiles without navigating the menu.
