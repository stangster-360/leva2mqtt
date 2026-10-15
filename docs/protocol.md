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
  Seen again in the two-client capture (see below): after a power-on, client A received
  only heartbeats for 7 s although it had sent `MCr` on connect and a keepalive newline;
  `{…}` frames started the moment client B connected and sent `MCr`. Possible cause:
  an `MCr` sent too soon after boot is ignored, or a new connection ends the state.
  Not separable yet. **unverified**.
  `power-cycle3`: heartbeat-only from the first second (13:45, three minutes after a
  normal two-client capture), through a power cycle and for 342 s after it, although
  `MCr` was sent on both connects. So the state is not limited to the seconds after a
  boot, and a single `MCr` after connect does not end it. Whether repeated `MCr` does:
  test with `capture.py --resend`. **seen**
  `hb-resend` (with `--resend`): frames for 7.6 s, then the machine was switched off at
  the plug (M.): 28 s of silence, then the TCP reset after the reboot (the known
  power-loss pattern). After the reconnect the `MCr` sent at once was ignored and
  heartbeats came; the `MCr` re-sent 9.7 s later brought frames within 0.36 s. So
  **re-sending `MCr` ends the heartbeat state**, and an `MCr` sent in the first seconds
  after power-on is lost. **seen** (once)
  `heatup2ready`: power-on, `MC@` sent at the first connect 12 s later → only heartbeats
  for the whole 6.5 min, no display frame. Third time the pattern "command right after
  power-on → heartbeat state" was seen. A ready beep was heard at about 14:19:30–14:20:00,
  roughly 6–6.5 min after power-on with a warm boiler (the earlier beep came ~6 min after
  power-on too). No data from that run. **seen**
  Working explanation: the heartbeat is the module's own sign of life while the leva!
  side is not reporting, e.g. because the report command came before leva! was ready.
  **unverified** Bridge rule: while only heartbeats arrive, re-send
  `MCr` about every 5 s until frames come.
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
- Two clients in parallel work: both received the same `{…}` frames on the same 0.25 s
  grid (identical timestamps), B joining and leaving did not disturb A (150 and 132
  frames, no gap). **seen** (one capture, 1 min).
- The report mode is **global for the module**, not per connection: client A had sent
  `MCr`, client B connected and sent `MC@`, and from then on **both** received display
  frames. Two connections cannot carry `MCr` and `MC@` at the same time. **seen**
- The number of TCP connections is limited: **four connections to port 23, each with a
  5-minute inactivity timeout** (ito Hardware Reference Manual, chapter WLAN – **dev**;
  the developer said "four, I think, from memory" in the forum).
  Connections that are not closed cleanly (new connections opened while old ones are
  left behind) occupy the slots until a timeout; until then new connections may fail.
  leva!'s menu item "Restart Wifi" frees them. **dev** (forum reply, 14.10.2026)
  For clients: always close the old socket before reconnecting, never keep spare
  connections open, and treat repeated connect failures while the module answers ping
  as "slots full" rather than "offline" (**unverified**).
- After a WLAN factory reset the module's access point "ito module" uses its default
  password again. The AP is only open if its password was deleted explicitly. Operating
  systems cache the AP password; after a reset the stored network has to be forgotten
  on the client. **dev**

## Module and WLAN (ito Hardware Reference Manual, **dev**)

- The ito carries two chips: an ESP8266 (WLAN, firmware based on esp-link) and the
  ATMega1284 running leva!. The ESP bridges port 23 to the ATMega's serial port at
  115200 baud, "like a cable". Port 2323 is the flashing port: the first character sent
  there resets the ATMega into its bootloader – never connect to it.
- WLAN modes: AP (factory: SSID `ito Module`, default password in the manual, web
  interface at 192.168.4.1), STA (in the home network, IP from DHCP), STA+AP (temporary,
  slow and unreliable while it scans). After power-on the ito falls back to STA+AP if it
  cannot reach the configured router; it does **not** fall back when the router vanishes
  during operation (power-cycle needed).
- WLAN configuration reset: power off, hold the encoder, power on, keep holding through
  slow and fast beeping until the beeping stops, release – the ESP restarts in AP mode
  with factory settings. Clients cache the AP password and must "forget" the network.
- The red LED on the PCB shows the WLAN state (AP, STA+AP, STA without IP, STA with IP);
  a module that connects for a few seconds and drops again is in a reset loop – the
  manual suggests power-cycling the router, then a WLAN configuration reset.
- 14.10.2026: the module seemed to drop out of the network (no ARP answer from the Mac,
  display and machine fine). Cause found on 15.10.: the Mac, not the ito – macOS had
  "Local Network" access switched off for the terminal app, which blocks every LAN host
  except the gateway. If a client gets "No route to host" for the ito while the gateway
  answers, check that permission first.
- Status Monitor conventions the leva! output follows: `?` → reply `XML=<name>`; `MCr`
  starts the `{…}` report at the cycle time of the XML file; the third character `*`
  marks an event; `MCR` resends the last packet; values without a reading are sent as
  `?…?`.

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

`CCC` is a three-digit code followed by the display text (4 lines × 16 characters).
The format is documented in the ito *Virtual Display Protocol* (`Virtual LCD\protocol.pdf`
on the ito DVD, 2018; not copied here) – **dev**. It matches our captures (**seen**):

| Digit | Document | Seen in captures |
|---|---|---|
| 1st | backlight: `0` off, `1` on | display off 180 s after the last input (`Auto-off 180s`), on again with input or `READY` |
| 2nd | pressed button: `0` none, `1` up, `2` down, `3` OK, `4` cancel, `5`/`6` user buttons | `1`/`2` encoder turned, `3` click; also `9` at pump start / stop (brew switch) – not in the document |
| 3rd | layout: `0` text 4×16, `1` menu (first line underlined), `2` titled text (9-character title + 3 lines) | `2` main and shot screen (title = profile name, max. 9 characters), `1` menu |

Control characters per the document: `\x01`/`\x02`/`\x03` bar segments (1/3, 2/3, full
width), `\x04` right arrow (cursor), `\x05` checkmark, `\x06` ±, `\x07` alarm bell
(probably used with alerts, **unverified**). The degree sign is named as code 225 in the
document; our captures show `ß` (0xDF) – the parser accepts both.

The module bridges port 23 transparently to the ATmega's first UART (115200 8N1) – **dev**
(same document). This explains why the report mode is global: all TCP clients share one
serial line. Virtual Display buttons are sent as `MC` + a raw byte 0–6 (not the
characters `0`–`6`), repeated at least once per second while pressed – the bridge never
sends these.

So `002` is simply "main screen, display off", not a machine state – the earlier
readings (standby, "ready") are superseded by this.

| Code seen | Screen |
|---|---|
| `102` / `002` | Main or shot screen, display on / off. Text shows `ECO` instead of the setpoint in eco, `STANDBY` in standby, `READY` for 10 s when ready – use the text |
| `101` | Menu, display on |
| `111` `121` `131` | Menu with encoder turn / turn / click |
| `112` `122` `132` | Main screen with encoder turn / turn / click |
| `191` | Menu item being confirmed (one older capture) |

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
- `READY` replaces the **setpoint** field (`1: 89.9ß   READY 6% …`, code `102`) for
  exactly **10 s** (100 consecutive frames, not blinking), together with the ready beep.
  A parser has to watch every frame to catch it. **seen** (`heatup3`)

### Shot screen

Captured during a 7 s flush (`shotscreen`, 13.10., control characters shown as `\x04`):

```
<102 0s  0.0g       1: 89.8ß \x04 90.0ß  7%    0s R=0.0 1.5  9.0b  0.0g>
<102 0s 10.9g       1: 89.8ß \x04 90.0ß  7%    0s R=7.5 0.5   0ß  10.9g>
    |-- line 1 ----||-- line 2 ----||-- line 3 ----||-- line 4 ----|
```

- Line 1: shot time and weight; line 2 as on the main screen; line 3 heater output, a
  second time field and `R=` flow by weight (g/s); line 4 measured pressure (no unit),
  a target (`9.0b` in the first frame, then `0ß`) and the weight. **seen**
- Both time fields stayed `0s` during this flush, as M. also saw on the machine: the
  firmware does not time a flush. **seen** Whether and from when they count during a
  real shot is **unverified**. The main screen's "last shot time" changed from `17s` (the shot
  before) to `0s` after the flush. The shot before ran 19.9 s by the `*` flag, so the
  firmware's shot time is measured differently (from the pressure rise or first drop?
  **unverified**).
- The screen switches within 0.3 s after the pump starts and back 0.3 s after it stops.
  The frames at start and stop carry the input digit `9` (`192`): brew switch. **seen**
- An older, whitespace-collapsed note of a shot screen: `<102 12s 24.7g 1: 88.8ß 90.0ß
  R=3.4 7.3 7.3b 24.7g>` (time 12 s, flow 3.4 g/s, pressure 7.3, target 7.3 b).

## Rich frames (`MCr`)

```
{ -1PID090.15090.0010-001.20011.20000.00000.0000.0000009.4058.0014.5020.0008.7OFF???…???~ A9}
```

- Fixed width, 289 characters, `{ … }`, 4 frames per second at a steady 0.25 s. **seen**
- Undefined values (sensor not installed, not active) are `?`. Skip them. **seen**, **dev**
- The developer describes field positions, lengths and types in an XML file that is part
  of the licensed firmware download. It is not included here. **dev**
  Official documentation (developer, 14.10.2026): the XML file shipped with leva! (read
  with the chapter "XML Syntax" of the ito manual), `Virtual LCD\protocol.pdf` on the
  ito DVD (display frames), the leva! manual's "Appendix: Serial menu", and the ito
  manual's chapter "Creating Status Monitor-compatible firmware". Positions in this file
  come from our captures and should be checked against these sources; none of them is
  copied into the repository.
- In standby the frame is almost entirely `?`. **seen**
  Set characters in all 63 standby frames of one capture: position 3 = `1`, positions
  163–167 = `001.0` (the setpoint increment), and the tail `~ 59` (checksum). Position 2 (`*`/`-`) and
  positions 4–6 (`PID`) are `?`. All frames were identical. **seen**
- Candidate rule for the state "awake": position 2 is `-` or `*`, or positions 4–6 read
  `PID` (the other way round for standby). Seen in two captures with one transition each,
  **unverified** as a rule (eco not captured yet).
- After `MCa` the first rich frame came 0.29 s later with real values (boiler 61.5 °C,
  heater 100 %). **seen**

### Decoded fields (0-based character positions)

The module names its Status Monitor definition in the `MC?` reply: `XML=leva!-64EA1111`
for our leva! 3.0. That file (not copied here) gives every field's position, length,
type and unit for exactly our 289-character frame, with a report cycle of 250 ms. All
positions below agree with it and with our captures. The older `leva!-1.4.xml` from the
leva! download has the same field order but a layout with separators. Meanings marked
**dev** come from the 3.0 file alone. Gaps of one character (86, 126, 173, 179, 185,
191) belong to no field.

| Pos. | Example | Meaning | Status |
|---|---|---|---|
| 2 | `-` / `*` | `*` = pump / brew program active. **Not** the same as "shot": it also stays set while the pump runs dry or water is drawn through the steam wand. | **seen** |
| 3 | `1` | Not in the 3.0 file. Always `1` here, also in standby. Perhaps the PID selected with `MC1`/`MC2` (the Status Monitor help lists "1/2 Choose PID") | **unverified** |
| 4–6 | `PID` | Control mode of PID 1 (`PID`, `ECO`, `OFF`, `CLD`, `ATN` per developer). `PID` when awake, `ECO` in eco (with the eco setpoint in the next field and no heater output), `???` in standby. `OFF`, `CLD`, `ATN` not seen | partly **seen** |
| 7–12 | `090.15` | Boiler temperature °C | **seen** |
| 13–17 | `090.0` | Setpoint °C, one decimal (shows the eco setpoint `069.9` while in eco) | **seen** |
| 18–20 | `017` | Heater output %, three digits, `100` at full power (= P + I + D, clamped). Earlier notes read 13–18 / 19–20, which cut off the hundreds digit; corrected with the cold-start capture (609 frames at `100`) | **seen** |
| 21–26 / 27–32 / 33–38 | `0001.1` `0007.1` `0008.5` | PID terms P, I, D | **seen** |
| 39–44 | `0000.0` | PID 1 feed-forward term, % | **dev**, always 0 here |
| 45–52 | `-00.0625` | Rate of temperature change (dPV/dt), used by the D term | **seen** |
| 53–57 / 58–62 / 63–67 | `009.4` `058.0` `014.5` | PID constants Kc, Ti, Td | **seen** |
| 68–72 | `020.0` | PID 1 wind-up guard, % | **dev** |
| 73–77 | `019.5` | Average heater output of PID 1 in % over the previous minute, updated about once per minute. Mean of the heater field over the window: 19.5 % → `019.5`, 0.0 % → `000.0` (steam capture). Matches the manual's "average output per minute". | **seen** |
| 78–80 / 81–85 / 87–91 / 92–94 | `OFF` `?????` | PID 2: control type, temperature °C, setpoint °C, output % | **dev**, type `OFF` **seen** |
| 95–100 / 101–106 / 107–112 / 113–118 | `??????` | PID 2 terms P, I, D, feed-forward | **dev**, `?` here (PID 2 off) |
| 119–125 / 127–131 / 132–136 / 137–141 / 142–146 / 147–151 | `?` | PID 2 dPV/dt (7 characters), Kc, Ti, Td, wind-up guard, average output | **dev**, `?` here |
| 152–154 | `098` | Flow meter volume, ml. Counts from 0 when the pump starts. Reference run on a primed group, empty portafilter: counter 96 ml, scale 102.3 g (counter ~6 % low; in flight ~3–4 % low, and ~3 g more arrives after the pump stops). The first run (group not primed) showed the opposite sign, so check again with more runs before correcting by a factor. | **seen**, calibration **unverified** |
| 155–159 | `00241` | Flow meter **pulses** (2.07–2.10 per ml: 241 pulses for 115 ml, 201 for 96 ml). Not a shot timer. | **seen** |
| 160–162 | `500` | Flow meter rate in **ml/min** (the 3.0 file converts with factor 0.01666 = 1/60 to ml/s). In an empty-portafilter run the volume rose 111 ml in 13.4 s = 497 ml/min while the field averaged ~500, so counter and rate agree. The field stays within ±5 % of 500 during the run (filtered / interpolated), then decays (`335`, `066`, `000`) after the pump stops. The earlier "~40 % too low" reading came from assuming ×0.01 ml/s. | **seen** (two captures; second run: 90 ml in 11.1 s = 487 ml/min, field average ~490) |
| 163–167 | `001.0` | Setpoint increment for `MC+`/`MC-` (`MC#`: 0.1, 1.0 or 10) – the `001.0` that stays set even in standby | **dev** |
| 168–172 / 174–178 / 180–184 / 186–190 | `?????` | PCB sensor and AUX 1–3 temperature sensors, °C (not installed here) | **dev** |
| 192–196 | `08.25` | Target pressure of the active profile, bar. In the second flow run it read `09.00` from the pump start and fell to `07.64` over the last 5 s (the lever decay). In the first flow run the same field showed 0.2–0.6 and no profile curve – why is **unverified** (different way of starting the pump?). | **seen** |
| 197–201 | `08.22` | Brew pressure, bar | **seen**, **dev** |
| 202–206 / 207–211 | `07.92` `08.60` | "Pressure band" min / max (XML name). Behaves like the min/max of the last reporting interval around the current pressure (1.63–1.75 bar at idle, 7.92–8.60 bar in a shot) | **dev**, behaviour **seen** |
| 212–214 / 215–217 / 218–221 / 222–225 | `020` `052` `-008` `-022` | Pump phase angle (pressure control output) with its bias, P and I terms; `?` when no profile controls the pump | **dev** |
| 226–230 | `042.1` | Scale weight, g. Matches the scale display (final 102.3 g in the frame, 102.3 g read off the scale). Reads `000.0` after the cup is removed. | **seen** |
| 231–235 | `03.12` | Flow by weight, g/s | **seen** |
| 236–244 / 245–253 / 254–262 / 263–271 | `     9BAR` `GEN.LEVER` | Profile slots 1–4, 9 characters each (right-aligned in slot 1, empty slots are blanks) | **dev**, contents **seen** |
| 272–280 | `GEN.LEVER` | Selected profile (same 9-character name as the display title) | **dev**, **seen** |
| 281–283 | `???` | Flow tracking target in ml/min (factor 0.01666 to ml/s, leva! 3.0 feature, configured per pressure profile); `?` when not used | **dev** |
| last 2 before `}` | `A9` | Checksum: two's complement of the 8-bit sum of all characters before `~` (sum + checksum ≡ 0 mod 256). The ito manual calls it the "binary complement" of the sum; the two's complement is what matches: 21 172 of 21 173 captured frames, the one miss was a 215-character line with lost characters. The parser drops frames with a wrong checksum. | **seen**, **dev** |

Not decoded yet: position 3 (see above). 284–288 hold the checksum `~ XX` and `}`.

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
| Espresso shots 13.10. (18.0 g in; 42.8 g / 42.0 g on the scale) | `*` 16.1 s / 19.9 s. Pressure starts rising ~2.5 s after pump start, first weight on the scale at +5.5 s (0.3 / 0.6 g), M. saw the first drop ~0.3 s earlier (note typed at +5.2 / +5.1 s): the scale lags the cup by well under a second. ~43 ml pumped at the first drop (68 ml in the 12.10. shots with a longer pre-infusion). Peak 8.0 / 8.8 bar. Yield from the frame 42.8 / 42.1 g. |
| Boiler after a shot | Shot ends at 89 °C, minimum 84.95 °C ~30 s later (heater 50–66 %), back above 88.5 °C ~55 s after the end, then **overshoot to 91.65 °C** with heater 0 % for ~1.5 min, undershoot 89.4 °C, inside ±0.5 K again ~4.5 min after the shot. Per-minute heater averages after the shot: 46.1, 1.5, 0.2, 6.9, 7.9 – the firmware's ready condition is not met again within 5 min. |
| Target pressure curve | `09.00` from pump start, falls after ~5.5 s by ~0.23 bar/s; 7.64 bar at 10.8 s in both shots. Depends on time since pump start, not on the puck. |
| Idle pressure after a shot | 5.9 bar 10 s after the pump stopped, 4.2 bar after about a minute, 0.5–0.8 bar after a flush. Not usable as a "pump is running" signal. |

## Heat-up and automatic eco / standby (`heatup`, display frames, **seen**, one capture)

Cold start (first frame 35.6 °C, display stream, no interaction for 55 min):

| Time | Event |
|---|---|
| 0 s | first frame, 35.6 °C, heater 100 %, code `102`, pressure 0.0 bar |
| 60 s | 48 °C, pressure 5.6 bar (thermal expansion, falls again later) |
| 120 s | 74.6 °C, heater drops (62 %) |
| 176 s | 89.6 °C, heater 16 %: code changes `102` → `002` = display switches off 180 s after power-on (`Auto-off 180s`). **seen** |
| 4–30 min | temperature oscillates 89.6–90.6 °C, heater 2–12 %, pressure falls 2.6 → 0.7 bar |
| 1807 s | auto-**eco** (setup: `Eco 30m`): setpoint field shows `ECO`, heater 0 %, boiler cools to 76.6 °C |
| 2714 s | auto-**standby** (setup: `Standby 45m`): `STANDBY` text |

- No `READY` text appeared in 32 665 display frames, and no ready beep was heard
  (the setup has a `Ready beep` item, its setting is not decoded). A parser cannot rely
  on `READY`. The firmware decides "ready" by steady-state detection on the temperature
  of the last three minutes (manual, PID menu: *Ready-beep*), and the beep only comes
  sporadically on this machine, also before the bridge existed. Until a capture
  shows `READY` or the beep, the bridge has to derive "ready" itself (temperature
  within a band of the setpoint for some minutes). **seen**, rule **unverified**
- **First ready beep heard** (M.) at about 14:07:27 in `hb-resend` (`MCr` stream; time
  from M.'s message, ±10 s), about 6 min after a power cycle with the boiler still warm.
  No character of the `MCr` frame changed at that moment apart from the always-moving
  fields (pressure, checksum, the per-minute average at 73–77), so there is **no ready
  flag in `MCr`**. **seen**
  Temperature before the beep: 89.90–90.15 °C (setpoint 90.0) from 14:03:24 on, i.e.
  3–4 min inside a ±0.15 K band, rate field 45–52 constantly `000.0000`, heater 3–6 %.
  In the heat-up capture without a beep the temperature swung 89.6–90.6 °C in slow
  cycles for 25 min. Candidate condition: temperature within about ±0.15–0.25 K of the
  setpoint for 3 min. **unverified** (one beep)
- **`READY` captured** in `heatup3` (display stream, warm boiler): power cycle at about
  14:24:04, frames again from 14:24:10 (after a re-sent `MC@`), `READY` from 14:27:01.9
  to 14:27:11.9, beep heard. Time from power-on: **2 min 57 s**. Displayed temperature
  89.6 °C for the first ~1.5 min, then 89.9–90.1 °C for 1 min 20 s before `READY`;
  heater 1–8 %. **seen**
  Power-on to beep in the three warm runs: ~3.0 min, ~5.7 min, ~6–6.5 min. A simple
  "inside ±x K for 3 min" rule does not fit all three; the firmware's detection is more
  than a band check. Matches the manual's 3-minute window only as a lower bound.
  **unverified**
- **Cold start in the evening** (`heatup-cold`, first frame 25.2 °C): setpoint reached
  after about 3.7 min, `READY` at 20:09:52–20:10:02 (10 s), **about 12 min after
  power-on** (the info menu's "On" counter gives leva!'s start as 19:57:50). Between
  20:02 and 20:06 M. changed Eco/Standby in the menu. Displayed temperature 89.9–90.1 °C
  from 20:06 on, heater 9 % → 6 %. The morning cold start (35.6 °C, temperature swinging
  89.6–90.6 °C) gave no `READY` within 30 min. **seen**
- **Another client switched the global mode during this capture**: at 20:00:15 the Leva
  Companion app (confirmed by M.) connected; its effect on the stream: `MC?` reply
  `XML=…`, a setup dump as after `MCu`, then display frames; from then on only display
  frames came, so the `MCr` data (and the per-minute heater average) ends at 20:00:21.
  `capture.py` now re-sends its own mode when the wrong kind of frame arrives. **seen**
- **Cold start with `MCr`** (`heatup-cold2`, 13.10., 20.8 °C, no app, no interaction):
  power-on ~10:28:25, heater `100` until 10:31:07, setpoint reached 10:32:20 (~4 min),
  overshoot to 90.65 °C at 10:33:40, 90.05–90.25 °C from 10:35 on. Ready beep heard at
  about 10:36:41 (~8.3 min). **seen**
- **The ready beep follows the per-minute heater average (73–77)**. The field updates
  once a minute; the beep came right after an update both times it was captured in
  `MCr`:

  | Capture | Last per-minute averages before the beep | Update → beep |
  |---|---|---|
  | `heatup-cold2` | 22.4, 16.0, **10.4, 10.7, 10.1** | 10:36:35 → ~10:36:41 |
  | `hb-resend` (warm) | 3.1, 6.2, 3.7, **4.7, 4.1, 4.7** | 14:07:40 → before 14:07:50 (note `ready beep`) |

  The beep came at the first update at which the **last three averages lay within less
  than 1 percentage point** (range 0.6 both times). One update earlier the range was 5.6
  and 1.0 – no beep. The field updated every 60.4 s in both captures (raw timestamps;
  the earlier "14:07:26" for `hb-resend` came from the chat, the capture shows 14:07:40).
  `tools/replay.py` with the bridge's tracker marks "ready" at exactly these two updates
  (10:36:35, 14:07:40). This matches the manual ("steady state … last three minutes") and
  explains why the beep is sporadic: a temperature that swings slowly keeps the heater
  average moving. Rule **unverified** (two hits, threshold could also be relative).
- The buzzer sequence for the ready beep is very short (10 × 30 ms on, 90 ms off in the
  setup dump) and the display switches off after 180 s (`Auto-off 180s`), so beep and
  `READY` are easy to miss. **seen** (setup dump)
- The eco and standby timers count from power-on / last activity (30 min / 45 min
  here), not from reaching the setpoint. Whether any serial command or a shot restarts
  them: **unverified**.
- A display frame `Auto save` appears while `MCu` walks through the setup menu
  (end of the dump). It is an artefact of the dump, not a machine state. **seen**

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
