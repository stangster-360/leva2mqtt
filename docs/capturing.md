# Capturing raw data

The parser can only be as good as the captures it was built from. This page contains
the tool, the capture playbook we use to map the protocol, and what each scenario is
meant to answer. Results go into [protocol.md](protocol.md).

## Tool

[`tools/capture.py`](../tools/capture.py) – Python 3.8+, no dependencies.

```sh
python3 tools/capture.py <ito-ip> <name> [--mode mcr|display|both|none] [--no-keepalive] [--minutes N]
```

- Writes `captures/<date>_<time>_<name>.txt`, every line prefixed with a Unix timestamp.
  Lines starting with `#` are events (connect, disconnect, commands sent, notes).
- `--mode`: what is sent on connect – `mcr` (sensor frames), `display` (display frames),
  `both`, or `none` (nothing).
- By default a bare newline is sent every 5 s to keep the connection open.
  `--no-keepalive` disables this: the tool then sends **nothing** on its own.
- Reconnects automatically when the machine is switched off and logs when it is back.

While recording:

| Input | Effect |
|---|---|
| command + Enter | sent to the module (e.g. `MCa`) |
| `# text` + Enter | note in the log only |
| `!cr MCR`, `!lf MCR`, `!crlf MCR` | send once with that line ending |
| `q` + Enter | stop |

Close every other client (e.g. other apps talking to the module) first.

## Playbook

### Preparation

1. On the machine: *Setup → Wifi → Permissions → Serial menu* enabled. Otherwise every
   command is silently ignored and the results are wrong.
2. No other client connected to the module.
3. Water tank full – check before every scenario.
4. Scale on and paired.

**Rule:** every scenario that starts in standby runs with `--mode none --no-keepalive`.
Otherwise the tool itself might wake the machine.

Note everything you see and hear as `#` notes, especially: *did it beep, did it wake up,
what is on the display*.

### 0. Passive standby series – `probe-*` – run first

Question: does connecting, or a single command, wake the machine from standby?

For every run:
1. Put the machine in standby: `capture.py <ip> to-standby --mode none`, send `MCE`, wait 5 s, `q`.
2. Wait 30 s with no connection.
3. `capture.py <ip> <run> --mode none --no-keepalive`
4. Listen for 30 s.
5. Send the command below, observe for 60 s, add notes, `q`.

| Run | Name | Command after 30 s |
|---|---|---|
| 0a | `probe-idle` | none (listen 90 s) |
| 0b | `probe-mcR` | `MCR` |
| 0c | `probe-mcr` | `MCr` |
| 0d | `probe-report` | `MCcREPORT SP1` |

Optional: repeat 0c and 0d from eco (`MCe` in step 1) as `probe-mcr-eco`, `probe-report-eco`.

### 1. Cold start and heat-up – `heatup` – ~30 min

Machine switched off for at least an hour.
1. `capture.py <ip> heatup --mode both` (reports "offline" – expected).
2. `# power on`, switch on. Let it heat up, no shots.
3. On the ready beep: `# READY beep`.
4. After 2 more minutes: `MCu`, 10 s, `MCh`, 10 s, `q`.

### 2. Flow meter reference – `flow-ref` – ~3 min

1. `capture.py <ip> flow-ref --mode mcr`
2. **Empty** portafilter, cup on the tared scale.
3. `# empty start`, run ~10 s, stop, `# end, scale XX g`, wait 20 s, `q`.

### 3. Two shots with a flush in between – `shots` – ~5 min

1. `capture.py <ip> shots --mode both`
2. `# shot 1, dose 18.0 g, grind X`, pull a shot. 30 s, `# cup off`, `# flush`, flush.
3. `# shot 2`, second shot without waiting. 30 s, `q`.

### 4. Eco / standby / wake by command – `standby` – ~5 min

1. `capture.py <ip> standby --mode both`
2. 30 s apart: `MCe`, `MCa`, `MCE`, `MCa`, `MCcREPORT SP1`. Then `q`.

### 5. Wake with the machine's button, passive – `wake-button` – ~3 min

1. Standby as in 0 (steps 1–2).
2. `capture.py <ip> wake-button --mode none --no-keepalive`
3. Listen 30 s, `# button`, wake the machine, 60 s, `q`.

### 6. Hot water and steam – `water-steam` – ~5 min

1. `capture.py <ip> water-steam --mode both`
2. `# hot water`, draw some. `# steam on`, switch, purge briefly, `# steam off`.
   Wait until the temperature drops, `q`.

### 7. Backflush – `backflush` – only when due

1. `capture.py <ip> backflush --mode both`
2. `# backflush start`, run the program, 30 s after it ends, `q`.

### 8. Line endings – `eol` – ~2 min, machine awake

1. `capture.py <ip> eol --mode none`
2. 10 s apart: `!cr MCR`, `!lf MCR`, `!crlf MCR`. 10 s, `q`.

A `{…}` line after a `MCR` means that line ending was accepted.

### 9. Power cycle at the plug – `power-cycle` – ~4 min

1. `capture.py <ip> power-cycle --mode none --no-keepalive`
2. 20 s, `# power off`, switch off. 60 s, `# power on`, switch on.
3. After "connected again", listen 60 s, `q`.

### 10. Optional, last: second client – `client-a` / `client-b`

1. Terminal 1: `capture.py <ip> client-a --mode mcr`
2. After 20 s, terminal 2: `capture.py <ip> client-b --mode mcr`
3. 60 s, `q` in both. Older firmware had trouble with fast reconnects – keep this last.

### What the playbook answers

| Question | Scenario |
|---|---|
| Does connecting / `MCR` / `MCr` / `MCcREPORT SP1` wake the machine from standby? | 0 |
| Is a silent client disconnected? Is there a heartbeat? | 0a |
| Heat-up time, when does READY appear, is the XML/firmware name announced on connect? | 1 |
| Command list of the current firmware (`MCh`) | 1 |
| Is the flow meter volume counter or the rate field correct? | 2 |
| How to tell shots from flushes, hot water, steam, backflush | 3, 6, 7 |
| Is `MCr` lost after waking up? | 4, 5 |
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
