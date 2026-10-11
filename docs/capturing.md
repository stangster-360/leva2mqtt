# Capturing raw data

The parser can only be as good as the captures it was built from. If your machine
behaves differently from what [protocol.md](protocol.md) describes, or you want to
help map the remaining fields, a capture is the most useful contribution.

## Tool

[`tools/capture.py`](../tools/capture.py) – Python 3.8+, no dependencies.

```sh
python3 tools/capture.py <ito-ip> <name> --mode both
```

- Writes `captures/<date>_<time>_<name>.txt`, every line prefixed with a Unix timestamp.
- `--mode`: `mcr` (sensor frames), `display` (display frames), `both`, or `none`.
- `--minutes N` stops automatically; otherwise type `q` + Enter.
- Keeps the connection alive and reconnects when the machine is switched off,
  logging when it disappears and comes back.
- While recording:
  - a command + Enter is sent to the module (e.g. `MCa`, `MCE`, `MCu`)
  - `# some text` + Enter only adds a note to the log (e.g. `# flush starts`)

Close every other client (e.g. other apps talking to the module) first. The module
should only have one connection.

## Useful scenarios

| Name | What to do | What it shows |
|---|---|---|
| `heatup` | Start with the machine off, switch it on, wait for READY | boot time, heat-up curve, READY |
| `shots` | Two shots with a flush in between, cup on the scale | shot detection, flow meter vs. scale |
| `standby` | Send `MCe`, `MCa`, `MCE`, `MCa`, `MCcREPORT SP1` with ~30 s pauses | energy saver states, report mode after wake |
| `wake-button` | Wake the machine from standby with its own button | wake without a command |
| `water-steam` | Draw hot water, switch to steam and back | events that are not shots |
| `backflush` | Run the backflush program | programs from the Run menu |
| `power-cycle` | Switch the machine off at the plug and on again | disconnect and reconnect behaviour |

Add notes generously – they make the capture much easier to read.

## Before sharing

Captures contain your module ID (`XML=leva!-…`) and possibly IP addresses. Replace
them, for example:

```sh
sed -i.bak -E 's/leva!-[0-9A-F]+/leva!-XXXXXXXX/' captures/*.txt
```

Then attach the files to a GitHub issue together with your firmware version and
hardware (scale, flow meter, pressure sensor, display).
