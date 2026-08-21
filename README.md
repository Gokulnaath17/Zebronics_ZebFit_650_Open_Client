# Zebronics Zebfit 650 Open Client

If you own a **Zebronics Zebfit 650**, you already know.

**No app. No way to do advanced stuff like setting time (on a watch).**

So I gave up looking for one.

Because obviously, when the app gets abandoned, the perfectly normal human response is to reverse engineer the fucking thing.

After little BLE reverse engineering, a lot of packet decoding, the watch has an independent Linux client.

Apparently, setting the time required reverse-engineering the watch.

## Demo

![Demo](temp/dont%20open/Dont%20OPENN%20this/go%20away/seriously%20last%20chance/demo.png)

## What works

* BLE discovery and connection
* Time synchronization
* Device information
* Activity data
* Notifications
* Live heart-rate data
* Vibration configuration
* History synchronization
* Raw + decoded BLE frame inspection
* Switch 24 and 12 hour format

## What's inside

| Path                  | Description                                                                            |
| --------------------- | -------------------------------------------------------------------------------------- |
| `BLE_PROTOCOL.md`     | GATT services, characteristics, commands, responses, packet formats and checksum rules |
| `bletest/tui.py`      | Terminal UI for connecting to and controlling the watch                                |
| `bletest/protocol.py` | Command builders and response decoder                                                  |
| `bletest/main.py`     | CLI interface for scripting and testing                                                |
| `bletest/test_tui.py` | Headless TUI smoke test — no watch required                                            |
                                               
## Requirements

* Linux
* Bluetooth adapter with BlueZ
* Python 3.10+
* Zebronics Zeb 650 / `ZEB-FIT650`

## Quickstart

```bash
python -m venv .venv
.venv/bin/pip install -e ./

# Test without a watch
.venv/bin/python bletest/test_tui.py

# Start the TUI
.venv/bin/python bletest/tui.py
```

Make sure the watch is disconnected from other devices and advertising.

## Examples

### Set the time

```text
gettime
```

```text
time
```

```text
format [12|24]
```
---

### Heart rate

```text
heart
```

Starts live heart-rate measurements.

```text
heart off
```

Stops them.

Congratulations, your abandoned smartwaT dtch is now reporting its heartbeat to a Linux terminal.

---

### Notifications

```text
notify 7 "Dinner is ready"
```

```text
notify 0 "Incoming call"
```

---

### Activity data

```text
sync
```

```text
hist
offline
running
```

---

### Other commands

```text
vibmode 2
info
clear
help
```

The TUI also shows raw BLE frames alongside their decoded meaning, so you can see exactly what is being sent to and received from the watch.

## But why ???

This is a cheap watch, and I didn't have to go this far.

Yeah, I know.

**That's not the fucking point.**

I bought it. I own it. I'll decide when I don't need it anymore.

They don't get to abandon the software and render perfectly fine hardware useless.

**I'll decide when I'm done with my watch. Not you, Zebronics.**


## Legal

Unofficial software for interoperability with compatible hardware.

No proprietary app binaries or decompiled application code are distributed.

Zebronics and Zeb 650 are referenced only to identify compatible hardware.

**License: TBD**

![Gus](temp/dont%20open/Dont%20OPENN%20this/go%20away/seriously%20last%20chance/gus.png)
