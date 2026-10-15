# Client specification

How a client talks to the ito module (leva! firmware) over TCP. This is the normative
summary for anyone implementing a client in another language, e.g. a mobile app that
connects to the machine without the bridge. The bridge (`leva2mqtt/link.py`,
`leva2mqtt/frame.py`) follows it.

The evidence lives elsewhere and is not repeated here:

- [protocol.md](protocol.md) – field notes, captures, frame layout
- [detection-rules.md](detection-rules.md) – machine state, ready, shot classification
- [bridge.md](bridge.md) – the bridge's behaviour and configuration

Every rule carries the status marker from protocol.md: **seen** (own capture), **dev**
(firmware developer or ito manual), **unverified** (assumption), **requirement**
(owner's decision, not a protocol fact). MUST / SHOULD / MAY as in RFC 2119.

Shared test vectors: [tests/vectors/mcr_frames.json](../tests/vectors/mcr_frames.json).
A client's frame parser MUST decode every vector to the listed values.

## 1. Transport

| Item | Rule | Status |
|---|---|---|
| Address | The module does not announce itself (no mDNS / Bonjour). The user enters its IP address. | **dev** |
| Port | TCP 23, plain TCP, no telnet negotiation, latin-1 text. | **seen** |
| Port 2323 | Bootloader. A client MUST NOT connect to it: the first byte resets the controller into flashing mode. | **dev** |
| Permission | leva! *Setup → Wifi → Permissions → Serial menu* must allow commands, otherwise they have no effect. | **dev** |
| Line endings | Send commands with CR+LF. Incoming lines end with CR+LF; split on LF and strip CR. | **seen** |
| Connections | At most 4 TCP connections on port 23, each closed by the module after 5 min without client input. All connections share one serial line to the controller. | **dev** |

## 2. Commands

A client MUST only send commands from this whitelist. Every other `MC…` command
changes setpoints or PID values, or toggles reporting.

| Command | Effect | Status |
|---|---|---|
| `MCr` | Stream rich frames `{…}`, 4 per second | **seen** |
| `MC@` | Stream display frames `<…>`, 10 per second | **seen** |
| `MCa` | Wake from eco / standby | **seen** |
| `MCe` | Enter eco | **seen** |
| `MCE` | Enter standby | **seen** |
| `MCcREPORT SP1` | Setpoint beep; also wakes the machine from standby and eco | **seen** |
| (empty line) | Keepalive, no effect | **seen** |

`MCr` and `MC@` replace each other, and the report mode is **global for the module**:
when one client sends `MC@`, every connected client receives display frames from then
on. The switch takes effect within 0.1–0.5 s. **seen**

## 3. Frames

- Rich frame: one line `{ … ~ XX}`, 289 characters for leva! 3.0 (`MC?` replies
  `XML=leva!-64EA1111`). Field positions: protocol.md, "Decoded fields". **seen**
- Checksum: `XX` (hex) is the two's complement of the 8-bit sum of all bytes before `~`,
  i.e. `(sum + XX) mod 256 == 0`. Frames with a wrong checksum MUST be dropped. **seen**
- A field containing `?` has no value (sensor missing, machine in standby). It MUST
  decode to "no value", never to an error or to zero. **seen**
- Other frame lengths (other firmware): parse what looks numeric, drop the rest, warn
  once. **unverified** (no other firmware captured)
- Other lines a client MUST tolerate and ignore for data purposes: display frames
  `<…>`, the heartbeat `$id: leva! (3.0)`, `XML=…` replies, setup-dump lines (`| …`,
  `Name...`). They are signals, see section 5. **seen**

## 4. Connection lifecycle

Timings as used by the bridge. They are tested against the real machine only as far as
the status column says.

| Step | Rule | Status |
|---|---|---|
| Connect | Timeout 3 s. On failure retry every 2 s. | **seen** |
| After connect | Send `MCr` at once. The report mode of a previous client may linger across connections, so a client cannot rely on the default. | **seen** |
| Keepalive | Send an empty line every 5 s. This also keeps the 5-min inactivity timeout from closing the connection. | **seen** |
| Watchdog | Nothing at all received for 3 s → close the socket, reconnect. Needed because the module does not close the connection when it loses power (no FIN, no reset). | **seen** |
| After a power cycle | The first keepalive to the rebooted module is answered with a TCP reset; reconnect at once. The module is reachable 4–20 s after power-on. | **seen** |
| Heartbeat only | `$id: leva! (3.0)` once per second and no frames: the `MCr` was lost (sent too early after power-on). Re-send `MCr` every 5 s until frames arrive. | **seen** (once) |
| Close | Always close the old socket before opening a new one. Never keep spare connections. | **dev** |
| Slots full | Repeated connections that end without a single byte while the module is otherwise reachable: treat as "all 4 slots taken", not as "offline". leva! *Restart Wifi* frees them. How the module turns away a fifth connection is not captured. | **unverified** |

## 5. Other clients

Up to four clients can be connected (bridge, apps, the Leva Companion). They share the
report mode and the machine.

| Situation | Rule | Status |
|---|---|---|
| Display frames arrive while `MCr` is wanted (more than 1.5 s after the own `MCr`), or `XML=` / setup-dump lines appear | Another client switched the global mode (seen with the Leva Companion). Do not switch back at once – that breaks the other client's display. Use the values the display frames carry; switch back to `MCr` only after a quiet period (bridge: 180 s). | cause **seen**, handling **unverified** |
| Two clients both want `MCr` | Works, both receive the same frames on the same 0.25 s grid. | **seen** |
| Wake beep with several clients | Every client that implements section 6 sends `MCcREPORT SP1` on the same wake event: the machine beeps more than once. Only **one** client per machine should own the wake beep. A client cannot reliably detect that a bridge is present (an `MCr` stream on connect may be left over from a client that has gone), so a second client needs a user setting, e.g. "another client handles the wake beep". | double beep **unverified**, setting **requirement** proposal |

## 6. Wake sequence (beep)

**requirement** (owner): when the machine becomes reachable or wakes from standby / eco,
the client sends `MCcREPORT SP1`, then `MCr`. Starting the client does not beep.

| Rule | Status |
|---|---|
| Wake event = state change standby or eco → awake in the frame (pos 4–6 `???` / `ECO` → `PID`). Covers the machine button, `MCa` and the leva! timers. | **seen** |
| `MCcREPORT SP1` MUST only be sent while the machine is awake: it wakes the machine from standby and eco. | **seen** |
| "Reachable again" counts only after the client saw the machine offline for a minimum time (bridge: 10 s), so short reconnects do not beep. | value **unverified** |
| The first connection after the client starts never beeps, whatever state the machine is in. | **requirement** |
| Whether `MCr` is really needed after `MCcREPORT SP1` | **unverified** (they did not interfere in captures) |

## 7. Machine state

From the rich frame; details and thresholds in detection-rules.md.

| State | Rule | Status |
|---|---|---|
| standby | pos 4–6 `???` | **seen** |
| eco | pos 4–6 `ECO` | **seen** |
| awake | pos 4–6 `PID` (`OFF`, `CLD`, `ATN` per developer: treat as awake) | **seen** / **dev** |
| pump running | pos 2 `*` – any pump or brew program, not only shots | **seen** |
| shot vs. flush | `*` and pressure has reached > 3 bar → shot | **seen** (3 runs) |
| ready | no flag in `MCr`; derive it (detection-rules.md, section 3) | **unverified** (2 hits) |
| alerts (`BACKFL`, `REFILL`, …) | only in display frames | **seen** |

The frame has **no shot timer**. Shot duration comes from the `*` flag and the
client's own clock. **seen**

## 8. Mobile clients

Additional rules for a client that is not running all the time (e.g. an iOS app).
They follow from sections 1, 4 and 5; none is tested with a mobile client yet.

- Close the connection actively when the app leaves the foreground. A process that is
  suspended or killed without a clean close leaves the slot taken until the 5-min
  timeout. **unverified**
- Reconnect and send `MCr` when the app returns to the foreground. **seen** (section 4)
- Do not send the wake beep on the first connection after the app (re)starts or returns
  to the foreground (section 6). **requirement**
- Shot recording only works while the app is in the foreground; a shot that started
  before the connection is incomplete and should be marked so. **unverified**
- Never switch the global mode to `MC@` while a bridge may be connected unless the user
  asked for it: the bridge then loses its `MCr` data. **seen** (mode is global)
