# ZEB-FIT650 BLE Protocol — the friendly version

How the **Zebfit** watch actually talks over Bluetooth, written for humans.
Everything below was derived from the (now-dead) Zebfit Android app v1.2.7
and — wherever marked **✓ verified** — confirmed live against a real
ZEB-FIT650 watch.

If you just want to connect your own code to the watch, skip straight to
[§7. A complete recipe](#7-a-complete-recipe).

---

## 1. The one-paragraph summary

The watch exposes a single GATT service (`fff0`) with two interesting
characteristics: **`fff1`** — the watch talks to you here (notifications),
and **`fff2`** — you talk to the watch here (writes). All messages are tiny
byte frames: one command byte, one length byte, some payload, and an XOR
checksum. Send a command on `fff2`, and the watch answers (mostly) with a
frame whose first byte is the same command with the top bit flipped
(`C6` → `26`, `89` → `29`, `C2` → `22`, ...). That's the whole trick.

---

## 2. How frames work

### You → watch (commands)

```
[ command ] [ length ] [ payload... ] [ checksum ]
```

- **command** — one byte, e.g. `0xC6` = "sync your data".
- **length** — how many payload bytes follow.
- **payload** — the arguments.
- **checksum** — the XOR of every payload byte (also written "chk"). The
  watch ignores anything that fails the checksum, so don't skip it.

Commands are always sent as **20-byte frames** — pad with `0x00` if your
command is shorter. (The app does this; the watch also tolerates unpadded
frames in practice.)

Example — "set time to 2026-08-19 12:00:00 (Wednesday)":

```
C2 07 26 08 19 0C 00 00 03 38
└┘ └┘ └──── payload: yy MM dd hh mm ss week ────┘ └┘
cmd len                                     chk = XOR of the 7 payload bytes
```

Week numbering: **Monday = 1 … Sunday = 7** (ISO). Year is last two digits.

### Watch → you (replies & events)

Replies follow the same shape but are **not** padded — you get a
variable-length frame (e.g. `26 09` + 9 data bytes + 1 chk byte = 11 bytes).

Big replies (history, offline records) arrive as **several 20-byte packets**
that you must reassemble: the length byte(s) at offset 1 tell you how many
packets to expect — `(len + 3) / 20` packets, or `(len16 + 4) / 20` for the
`B1` reply which uses a 16-bit length.

---

## 3. The GATT map ✓ verified

| UUID (suffix) | Name | Role | Notes |
|---|---|---|---|
| `0000fff0-…` | HJT ISSC | service | the whole protocol lives here |
| `0000fff1-…` | data in | **watch → phone** | subscribe to notifications (CCCD `0x2902` = `0x0001`) |
| `0000fff2-…` | command out | **phone → watch** | write commands here (write-with-response) |
| `00002a19-…` | battery | read + notify | standard battery service |
| `0000fff3-…` | ? | notify | exposed but **never used by the app** — purpose unknown |
| `0000fff4-…` | ? | write | same — unknown |

The full UUIDs use the standard Bluetooth base
`-0000-1000-8000-00805f9b34fb`, e.g. `0000fff0-0000-1000-8000-00805f9b34fb`.

**Minimal connect sequence** (verified end-to-end):

1. Scan for a device advertising the name **`ZEB-FIT650`**.
2. Connect + discover services.
3. Enable notifications on `fff1`.
4. Write `C6 01 08 08` (sync current data) → expect `26` back.
5. Optionally subscribe to battery `2a19` too.

That's it — no handshake, no pairing, no magic. (The app also sends device
params `9B` and set-time `C2` right after the first sync; the watch works
fine without them, but your own app will probably want to set the time so
the watch's timestamps are sane.)

---

## 4. Commands you can send

`chk` = XOR of the payload bytes unless stated otherwise. "✓" = verified
live against the real watch.

| Cmd | Len | Payload | What it does | Reply |
|---|---|---|---|---|
| `C6` | `01` | `08 08` | **Sync current data** (steps/distance/kcal) ✓ | `26` / `06` |
| `C2` | `07` | `yy MM dd hh mm ss week` | **Set time** (week: Mon=1…Sun=7) ✓ | `22` / `02` |
| `89` | `00` | `00` | **Get device time** ✓ | `29` / `09` |
| `FB` | `00` | `00` | **Read firmware info** ✓ | `FB` + 12 ASCII chars |
| `C4` | `03` | `week hour hours` | Start a history/curve sync for that day | `24` stream / `04` |
| `88` | `00` | `00` | **Erase all watch data** (no undo!) | `28` / `08` |
| `83` | `2F` | 50-byte profile block | Personal info + alarms + goals (split into 3 writes) | `23` / `03` |
| `86` | `2F` | `enable begin_h end_h minutes weekbits` | Idle/sedentary reminder | `32` / `0d` |
| `FF` | `11` | 17 bytes | Device params (auto heart-rate detection) | — |
| `92` | `11` | `flag 0 0 0 0 0 on off hi lo …` | Heart/BP thresholds (`80`=heart, `40`=BP) | `42` / `12` |
| `9B` | `0F` | `24h unit screentime 0 unit+1 vibmode wrist lang …` | Device params — **byte 7 = vibration mode** (0 off, 1 on+anti-lost, 2 on) | `2B` (presumed) |
| `B1` | `11` | `unix_ts (4 bytes BE)` | Offline history since that timestamp | `B1` stream / `61` |
| `A2` | `00` | `yy MM dd` | Running data for a date | `A2` |
| `F1` | `01`/`len` | flag (or flag + ASCII) | Legacy call/SMS alert — **does not vibrate this watch**, see `A4` | — |
| `A4` | `00/01/02/03` | see §6 | **Notification push — this one vibrates** ✓ | none observed |
| `F4` | `01` | `mode mode` | Band mode (bit7 sleeping, bit6 vibrate, bit5 ring) ✓ | — |
| `93` | `11` | `mode 0 0 0 on on 0…0` | **Start/stop heart (`80`) or BP (`40`) measurement** ✓ | `33`, then `99` stream |
| `94` | `11` | `mode 0 0 0 on 0…0` | Sport mode switch | `33` / `13` |
| `F2` | `03` | `00 sign± temp` | Set temperature (whole degrees, bit7 = positive) ✓ | — |
| `2F` | `00` | `00` | Ack after answering/ending a call (`9F`) | — |
| `FC` | `00` | `00` | **Enter DFU mode. Don't send this unless you mean it** — the watch leaves normal mode and needs recovery. | — |

The `C6` sync triggers an **unsolicited `93` frame** every time (the watch
reports its sensor state) — that's normal, don't treat it as an error.

---

## 5. What the watch sends you

| First byte | Meaning |
|---|---|
| `26` + 9 bytes + chk | Current data: steps(3) dist(3) cal(3), each **24-bit big-endian**. Trailing byte = XOR of the 9 data bytes ✓ |
| `06` | …sync failed |
| `29` + `yy MM dd hh mm ss` | Device time (reply to `89`) ✓ |
| `09` | …time read failed |
| `22` | Set-time success (full reply: `22 04 00 00 00 00`) ✓ |
| `02` | …set-time failed |
| `28` / `08` | Clear-data success / failed |
| `23` / `03` | Personal-info set success / failed |
| `32` / `0d` | Idle-alarm set success / failed |
| `42` / `12` | Threshold set success / failed |
| `FB` + 12 bytes | Firmware string, e.g. `ZEB_CMABV002` ✓ |
| `24` + `len yy MM dd hh` + slots + chk | History curve data (reply to `C4`). Each 10-minute slot is 6 bytes: high 2 bits of byte 0 = type (0=daily, 1=sleep, 2=running), then steps/cal/dist as 16-bit fields. Checksum byte at the end ✓ |
| `04` | …curve failed |
| `B1` + len(2 B) + records | Offline history (reply to `B1`). Each record is 7 bytes: `type ts(4 BE) v1 v2` — type 1 = heart (bpm = v2), type 2 = blood pressure (v1 = sys, v2 = dia). Records land on 30-minute slots ✓ |
| `61` | …no offline data |
| `A2` + details | Running data for a date (mode 4 = running) ✓ |
| `2B` | Params ack (seen after `9B`, meaning unconfirmed — the app never handles it) |
| `93` | Sensor state (also sent unsolicited after `C6`) |
| `99` + `len 80 00 00 00 bpm(2 BE)` | **Live heart rate** — e.g. `99 06 80 00 00 00 00 2C` = 44 bpm ✓ |
| `33` / `13` | Measurement switch success / failed |
| `F3` | Alarm triggered (app plays a tone) |
| `F5` | Take picture (camera remote) |
| `F6` `01..06` | Music control: toggle / prev / next / stop / vol+ / vol− |
| `9F` | Incoming call: answer or end (reply `2F`) |
| `A4` | Notification ack (documented in the app; **none observed live** — don't wait for it) |
| `E3` | Weather refresh request (send `F2` weather) |
| `F7` / `F8` | Call/SMS no-ops — ignore |

---

## 6. Notifications: the fun part (and the icon table) ✓ verified

This watch is NOT a "Wordstock" firmware device, so the app's legacy `F1`
alert frames do nothing here. The real path is the **A4 ANCS push** — a
little 4-frame conversation:

```
A4 00 <appIdx> <flag>          metadata: which app icon, which type
A4 01 <title, max 17 bytes>    title
A4 02 <content, max 17 bytes>  body text
A4 03                          end
```

Every frame is 20 bytes, padded with zeros; checksum at byte 19 = XOR of
bytes 2–18. Long texts just repeat the `A4 01`/`A4 02` frames with 17-byte
chunks. **The watch vibrates on receipt and shows the text** — no extra
"enable vibration" param needed.

The watch's own app-icon set (mapped live, one push per index):

| Index | Icon shown | App's flag for it |
|---|---|---|
| 0 | Call | `0x80` |
| 1 | Mail | `0x40` |
| 2 | Discord | `0x80` |
| 3 | Snapchat | `0x40` |
| 4 | Facebook | `0x04` |
| 5 | Skype | `0x20` |
| 6 | Skype-like (duplicate?) | `0x02` |
| 7 | **WhatsApp** (the classic test) | `0x08` |
| 8 | LINE | `0x00` |
| 9 | *(nothing — dead index)* | any |
| 10 | Instagram | `0x00` |
| 11 | LinkedIn | `0x00` |
| 12+ | *(nothing — dead)* | any |

The icon is driven by `appIdx`. Flag values: `80`=call/WeChat, `40`=SMS/QQ,
`20`=email/Skype, `08`=WhatsApp, `04`=Facebook, `02`=Twitter,
`00`=Instagram/LinkedIn.

**Gotcha:** the watch drops pushes sent back-to-back. If you sweep through
indices with ~4 s gaps, some get swallowed (still showing the previous
push). Send one push at a time with **≥6 seconds** between them.

---

## 7. A complete recipe

A minimal Python client (bleak) that syncs the watch and reads live heart
rate:

```python
import asyncio
from bleak import BleakClient, BleakScanner

SVC = "0000fff0-0000-1000-8000-00805f9b34fb"
RX  = "0000fff1-0000-1000-8000-00805f9b34fb"   # watch -> you (notify)
TX  = "0000fff2-0000-1000-8000-00805f9b34fb"   # you -> watch (write)
BAT = "00002a19-0000-1000-8000-00805f9b34fb"

def xor_chk(b: bytes) -> int:
    c = 0
    for x in b:
        c ^= x
    return c

def set_time():
    import datetime
    now = datetime.datetime.now()
    payload = bytes([now.year % 100, now.month, now.day,
                     now.hour, now.minute, now.second, now.isoweekday()])
    return bytes([0xC2, 0x07]) + payload + bytes([xor_chk(payload)])

async def main():
    device = await BleakScanner.find_device_by_name("ZEB-FIT650", timeout=10)
    async with BleakClient(device) as c:
        await c.start_notify(RX, lambda _h, data: print("<<", data.hex(" ")))
        await c.write_gatt_char(TX, bytes.fromhex("C6 01 08 08"))  # sync
        await asyncio.sleep(1)
        await c.write_gatt_char(TX, set_time())                    # set time
        await asyncio.sleep(1)
        # start heart rate measurement:
        # 93 11 80 00 00 00 01 01 + zeros + chk  (chk = XOR of bytes 2..18)
        start = bytes([0x93, 0x11, 0x80, 0, 0, 0, 1, 1] + [0] * 11)
        start = start[:19] + bytes([xor_chk(start[2:19])])
        await c.write_gatt_char(TX, start)
        await asyncio.sleep(15)   # watch streams 99 frames with bpm

asyncio.run(main())
```

Ground rules, learned the hard way:

- **Subscribe to `fff1` before writing anything** — frames sent before the
  subscription are lost.
- **Write with response** (the default in bleak). The app queues commands
  and drains them 125 ms apart; keep at least that gap between writes.
- **20 bytes per write, zero-padded.** Longer payloads (the `83` profile)
  go in sequential ≤20-byte writes.
- **Always compute the XOR checksum** — the watch drops bad frames silently.
- **Session commands wait for a reply** (~6 s timeout, then resend). Big
  syncs (`C4`, `B1`) can produce many packets — reassemble by the length
  byte, don't treat each packet as a separate reply.
- The watch must be **advertising** — it stops when a phone app is bound to
  it. Disconnect from any phone before testing.
- On bluez/DBus the same thing is: `StartNotify` on `fff1` (this writes
  `0x0001` to the `2902` descriptor), `WriteValue` on `fff2`.

---

## 8. Known unknowns (honest caveats)

- `fff3`/`fff4` exist but the app never touches them — possibly a second
  command channel, maybe nothing.
- `2B` arrives after `9B` params but the app has no handler for it; treat it
  as "params accepted" at your own risk.
- Index 9 (and 12+) in the icon table display nothing; whether a different
  flag makes them work is untested.
- The `83` profile block layout (alarms/goals/personal info) is documented
  in the app's source but **not verified live** — send it with care.
- This protocol was derived from one app version (1.2.7) and one watch
  (firmware `ZEB_CMABV002`). Other firmware revisions may differ — if your
  watch replies with `06`/`09`/`13` where you expect success, verify the
  frame against §4 first.

---

## 9. How this was derived

From the decompiled Zebfit app (jadx) — command builders in
`BluetoothLeService.java`, UI call sites in `PremierMainFragment.java` —
then verified byte-by-byte against a real watch over a host bluez stack.
The verification log lives in `HANDOFF.md`. No app code is included in this
repo; this document is a description of observable behavior, written to be
reimplemented cleanly.