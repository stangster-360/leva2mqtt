# Capturing raw data

The parser can only be as good as the captures it was built from. This page contains
the tool, the capture playbook we use to map the protocol, and what each scenario is
meant to answer. Results go into [protocol.md](protocol.md).

## Tool

[`tools/capture.py`](../tools/capture.py) – Python 3.8+, no dependencies.

```sh
python3 tools/capture.py <ito-ip> <name> [--mode mcr|display|none] [--no-keepalive] [--no-resend] [--minutes N]
```

- Writes `captures/<date>_<time>_<name>.txt`, every line prefixed with a Unix timestamp.
  Lines starting with `#` are events (connect, disconnect, commands sent, notes).
- `--mode`: what is sent on connect – `mcr` (sensor frames), `display` (display frames),
  or `none` (nothing). `MCr` and `MC@` replace each other: only one kind of frame
  is streamed at a time. To see both in one capture, switch by hand with `MC@` / `MCr`.
- By default a bare newline is sent every 5 s to keep the connection open.
  `--no-keepalive` disables this: the tool then sends **nothing** on its own.
- While no frame has arrived for 5 s, the start command is sent again. A command sent
  in the first seconds after power-on is lost; without this the module only sends
  `$id: leva! (3.0)` heartbeats. `--no-resend` disables it.
- Reconnects automatically when the machine is switched off and logs when it is back.

While recording:

| Input | Effect |
|---|---|
| command + Enter | sent to the module (e.g. `MCa`) |
| `# text` + Enter | note in the log only |
| `!cr MCR`, `!lf MCR`, `!crlf MCR` | send once with that line ending |
| `q` + Enter | stop |

Close every other client (e.g. other apps talking to the module) first.

### Live view

[`tools/monitor.py`](../tools/monitor.py) shows the decoded stream live, once per second,
including the bridge's ready decision – handy next to a capture (e.g. a heat-up, to
compare the *Ready* section with the ready beep). It writes no file. See
[monitor.md](monitor.md).

## Playbook

### Preparation

1. On the machine: *Setup → Wifi → Permissions → Serial menu* enabled. Otherwise every
   command is silently ignored and the results are wrong.
2. No other client connected to the module.
3. Water tank full – check before every scenario.
4. Scale on and paired.

**Rule:** every scenario that starts in standby runs with `--mode none --no-keepalive`
(connecting, the keepalive, `MC@` and `MCr` did not wake the machine in our captures,
but `MCcREPORT SP1` does – see scenario 0). That way the capture itself cannot influence
the result.

`MCr` and `MC@` replace each other. Pick the stream that answers the question:
`mcr` for sensor data, `display` where the display text matters (`READY`, alerts).

Note everything you see and hear as `#` notes, especially: *did it beep, did it wake up,
what is on the display*.

### 0. Standby probes – `probe-*`, `report-switch*` – done

Question: does connecting, or a single command, wake the machine from standby?

| Capture | What was sent | Result |
|---|---|---|
| `report-switch` | connect, keepalive, `MC@`, `MCr`, `MC@` in standby | no wake-up, display stays `STANDBY`; `MCr` and `MC@` replace each other |
| `report-switch-awake` | `MCr` in standby, then `MCa`, `MC@`, `MCr`, `MC@`, `MCE` | `MCr` keeps streaming through the wake-up |
| `probe-report` | `MCcREPORT SP1` in standby | **wakes the machine** (display `STANDBY` to main screen in 0.14 s); only the report beep was heard |

Still open (optional): `MCR` in standby (`probe-mcR`), and `MCcREPORT SP1` from eco
(`MCe` instead of `MCE`; `probe-report-eco`). Run them like this:

1. Put the machine in standby: `capture.py <ip> to-standby --mode none`, send `MCE`, wait 5 s, `q`.
2. Wait 30 s with no connection.
3. `capture.py <ip> <run> --mode none --no-keepalive`, listen 30 s.
4. Send the command, observe 60 s, add notes (*beep? display?*), `q`.

### 1. Cold start and heat-up – `heatup` – ~30 min – repeat open

Done once at 35 °C start (not fully cold, ready beep did not come, see protocol.md).
**Repeat with a really cold machine** (next morning, off all night), changed settings:
Eco 60 min, Standby 90 min (so the auto-eco cannot cut the detection window), ready
beep sequence louder and longer (e.g. 150 ms on/off, 5×, check with the buzzer menu's
*Test*). Do not touch the machine. Run with `--mode mcr` (fine temperature and the
per-minute heater average at 73–77 are what the bridge's ready logic uses; `READY`
itself was already captured in `heatup3`) and type `# ready beep` when it beeps. Keep it
running for 30 min or until the heater average stops falling.

Machine switched off for at least an hour.
1. `capture.py <ip> heatup --mode display` (reports "offline" – expected). The display
   stream is used because `READY` and alerts only appear there.
2. `# power on`, switch on. Let it heat up, no shots.
3. On the ready beep (if there is one): `# READY beep`.
4. After 2 more minutes: `MCu`, 10 s, `MCh`, 10 s, then `MCr` for 60 s (sensor data of the
   warm machine), `q`.

### 2. Flow meter reference – `flow-ref` – ~3 min – done

1. `capture.py <ip> flow-ref --mode mcr`
2. **Empty** portafilter, cup on the tared scale.
3. `# empty start`, run ~10 s, stop, `# end, scale XX g`, wait 20 s, `q`.

### 3. Two shots with a flush in between – `shots` – ~5 min – done

1. `capture.py <ip> shots --mode mcr`
2. `# shot 1, dose 18.0 g, grind X`, pull a shot. 30 s, `# cup off`, `# flush`, flush.
3. `# shot 2`, second shot without waiting. 30 s, `q`.

### 4. Eco / standby / wake by command – `standby` – ~6 min – done (log ends at 138 s)

1. `capture.py <ip> standby --mode mcr`
2. 30 s apart: `MCa`, `MCe`, `MCa`, `MCE`, `MCa`, `MCe`, `MCcREPORT SP1`. Then `q`.
   (If the machine is in eco at the start, the first `MCa` wakes it. The last two
   commands answer the open question whether `MCcREPORT SP1` also wakes from eco.)
3. Note after each command: *beep? display?* Expected from the captures so far: `MCr`
   keeps streaming through every state change.

### 5. Wake with the machine's button, passive – `wake-button` – ~3 min – done

1. Standby as in 0 (steps 1–2).
2. `capture.py <ip> wake-button --mode none --no-keepalive`
3. Listen 30 s, `# button`, wake the machine, 60 s, `q`.

### 6. Hot water and steam – `water-steam` – ~5 min – done

1. `capture.py <ip> water-steam --mode mcr`
2. `# hot water`, draw some. `# steam on`, switch, purge briefly, `# steam off`.
   Wait until the temperature drops, `q`.

### 7. Backflush – `backflush` – only when due

1. `capture.py <ip> backflush --mode mcr`
2. `# backflush start`, run the program, 30 s after it ends.
3. Type `MC@` and record 30 s of display frames (the `BACKFL` alert is only visible there), `q`.

### 8. Line endings – `eol` – ~2 min, machine awake – done (LF, CR accepted)

1. `capture.py <ip> eol --mode none --no-keepalive`
2. 10 s apart: `!lf MCR`, `!crlf MCR`, `!cr MCR`. 10 s, `q`.

The module keeps the report mode of the previous client, so a capture may start with `{…}` frames. Then look for an **extra** frame about 0.05 s after the command instead of for any `{…}` line.
Without `--no-keepalive` the tool appends a bare `\n` every 5 s, which would complete a
`MCR\r` and make `CR` look accepted when it is not.

### 9. Power cycle at the plug – `power-cycle` – ~4 min – done, report mode after power cycle still open

1. `capture.py <ip> power-cycle --mode mcr` (**with** keepalive). A first run with
   `--no-keepalive` showed that the module does not close the connection when it loses
   power; without outgoing data the tool never noticed and never reconnected.
2. 20 s, `# power off`, switch off. 60 s, `# power on`, switch on.
3. After "connected again", listen 60 s, `q`. Note the time from `# power on` to
   "connected again" and which kind of frames arrive first (`<…>` or `{…}`).

### 10. Second client – `client-a` / `client-b` – done

1. Terminal 1: `capture.py <ip> client-a --mode mcr`
2. After 20 s, terminal 2: `capture.py <ip> client-b --mode mcr`
3. 60 s, `q` in both. Older firmware had trouble with fast reconnects – keep this last.

### 11. `MCcREPORT SP1` right after power-on – `report-poweron` – ~3 min

Does the module accept the report command as soon as frames arrive after power-on
(the bridge's "reachable again" beep)?
1. Machine off at the plug. `capture.py <ip> report-poweron --mode mcr --first-frame "MCcREPORT SP1"`
2. Switch on. Note `# beep` if the report beep comes. 60 s after the first frames, `q`.

### 12. Shot in display mode – `shot-display` – ~2 min

Does the firmware's shot timer count during a shot, and from when (pump start,
pressure rise, first drop)? A flush is not timed (seen).
1. `capture.py <ip> shot-display --mode display`, pull a normal shot, note
   `# first drop`, read the shot time off the display afterwards: `# display 17s`, `q`.

### 13. Fifth connection – `slots-full` – ~3 min, bridge stopped

How does the module turn away a fifth connection (refused, closed at once, or open
but silent)? The bridge's "Connection slots full" sensor guesses from that.
1. Open four connections that stay idle, each in its own terminal:
   `nc <ip> 23` (4×).
2. `capture.py <ip> slots-full --mode mcr --no-resend`, watch the first 30 s, note what
   happens (`# refused`, `# closed`, `# silent`), `q`.
3. Close one `nc`, start the capture again: frames should come at once.

### Answered by the documentation (no capture needed)

Field meanings 39–44, 68–72, 78–151, 163–167, 168–191, 202–225 (leva! Status Monitor XML),
the `~ XX` checksum, display code digits and control characters (Virtual Display
protocol), connection limit and idle timeout, why the report mode is global, `MCR`, the
command list and the WLAN modes (ito Hardware Reference Manual). See protocol.md.

Still open and only answerable by a capture: scenario 11 (report right after power-on,
once the WLAN is stable), 12 (shot timer in display mode), more ready beeps (absolute or
relative threshold), 7 (backflush alert position, blinking, `\x07` bell), and single-seen
rules (hot water, steam threshold, tank empty, flow meter calibration).

### What the playbook answers

| Question | Scenario |
|---|---|
| Does connecting / `MCR` / `MCr` / `MCcREPORT SP1` wake the machine from standby? | 0 (answered, `MCR` open) |
| Is a silent client disconnected? Is there a heartbeat? | any capture without keepalive |
| Heat-up time, when does READY appear, is the XML/firmware name announced on connect? | 1 |
| Command list of the current firmware (`MCh`) | 1 |
| Is the flow meter volume counter or the rate field correct? | 2 |
| How to tell shots from flushes, hot water, steam, backflush | 3, 6, 7 |
| Is `MCr` lost after waking up? | 0 (answered for `MCa`), 5 (button) |
| Accepted line endings | 8 |
| How does the connection end at power-off, time from power-on to first data | 9 |
| Two clients at once | 10 |
| Is `~ XX` a checksum? | analysis only |

## Before sharing a capture

Captures may contain IP addresses and identifiers. Check and replace them, for example:

```sh
sed -i.bak -E 's/leva!-[0-9A-F]+/leva!-XXXXXXXX/' captures/*.txt
```

Then attach the files to a GitHub issue together with your firmware version and
hardware (scale, flow meter, pressure sensor, display).
