# leva2mqtt

MQTT bridge for espresso machines with the **ito** module running the **leva!** firmware.
It exposes live machine data to Home Assistant via MQTT Discovery, records every shot
and can upload it to [Visualizer.coffee](https://visualizer.coffee).

> **Status: early development.** Nothing here is ready to install yet. This repository
> currently documents the machine protocol as it is being reverse-engineered.
> The bridge itself will follow.

## Why

The ito / leva! controller exposes a telnet interface (port 23) with a serial menu,
but the data formats it streams are not documented. leva2mqtt turns that stream into
something a smart home can use:

- live boiler temperature, setpoint, heater output, brew pressure, weight and flow
- machine state (standby, eco, ready, shot running) and maintenance alerts (backflush, descale, filter)
- buttons for wake / eco / standby and an audible setpoint report
- one file per shot, in a format Visualizer.coffee accepts, with optional automatic upload

## How it will work

```
ito / leva!  ──telnet:23──▶  leva2mqtt  ──MQTT──▶  broker  ◀──▶  Home Assistant
                                 │
                                 └── shots/*.json  ──▶  Visualizer.coffee (optional)
```

- A single persistent connection to the ito. The bridge is meant to be the only client.
- Entities are created automatically in Home Assistant through MQTT Discovery.
- When the machine comes back (powered on, or woken from standby), the bridge sends
  `MCcREPORT SP1` so the machine beeps its setpoint as an audible confirmation.

## Planned deployment

- Docker image for amd64, arm64 and armv7 (Raspberry Pi included), configured with
  environment variables
- Home Assistant add-on built from the same image, with the add-on options form as UI
- Plain Python for anything else (one dependency: `paho-mqtt`)

## Requirements

- An espresso machine with an ito module and leva! firmware, reachable over Wi-Fi
- In leva!: *Setup → Wifi → Permissions* → **Serial menu** enabled
- An MQTT broker (e.g. Mosquitto) and, for the integration, Home Assistant
- Network access from the bridge to the ito on TCP 23 and to the broker

Optional: a Bluetooth scale paired to leva! (weight and flow by weight), a brew
pressure sensor, a flow meter.

## Documentation

- [docs/capturing.md](docs/capturing.md) – capture tool and playbook for recording raw data
- [docs/protocol.md](docs/protocol.md) – commands, display layouts and connection
  behaviour, as observed on real hardware

## Contributing

Machines differ: firmware versions, displays, scales, flow meters and pressure sensors
all change what the ito streams. If your setup behaves differently, a short capture of
the raw output helps more than anything else:

```sh
python3 tools/capture.py <ito-ip> my-machine --mode mcr
```

Please replace your module ID and IP addresses before posting.

## Disclaimer

Not affiliated with the makers of the ito module, the leva! firmware or Visualizer.coffee.
Sending commands to your machine is at your own risk.

## License

[MIT](LICENSE)
