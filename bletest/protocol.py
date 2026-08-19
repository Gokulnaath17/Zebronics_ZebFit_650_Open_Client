"""Zebfit650 BLE command builders + response decoder.

Mirrors the decompiled code (BluetoothLeService.java, PremierMainFragment.java).
Frames are the raw byte arrays the app sends; the watch accepts
variable-length frames but replies in 20-byte packets.
"""

from __future__ import annotations

import struct
from datetime import datetime

UUID_SERVICE = "0000fff0-0000-1000-8000-00805f9b34fb"
UUID_RX = "0000fff1-0000-1000-8000-00805f9b34fb"  # watch -> phone (notify)
UUID_TX = "0000fff2-0000-1000-8000-00805f9b34fb"  # phone -> watch (write)
UUID_BATTERY = "00002a19-0000-1000-8000-00805f9b34fb"
UUID_CCCD = "00002902-0000-1000-8000-00805f9b34fb"

MAX_CMD_LEN = 20

# App index -> ANCS flag (BluetoothLeService.java alertNotification map).
# The watch chooses the icon by app index; flag is the app's type selector.
ANCS_FLAGS = {0: 0x80, 1: 0x40, 2: 0x80, 3: 0x40, 4: 0x04, 5: 0x20,
              6: 0x02, 7: 0x08, 9: 0x20, 10: 0x00, 11: 0x00}

APP_NAMES = {
    0: "call", 1: "sms", 2: "wechat", 3: "qq", 4: "facebook", 5: "skype",
    6: "twitter", 7: "whatsapp", 8: "line", 9: "email", 10: "instagram",
    11: "linkedin",
}


def xor_chk(data: bytes) -> int:
    """XOR of all bytes."""
    c = 0
    for b in data:
        c ^= b
    return c


def pad20(data: bytes) -> bytes:
    """Zero-pad a frame to 20 bytes."""
    return data + b"\x00" * (MAX_CMD_LEN - len(data))


# ---------------------------------------------------------------- commands


def sync_current() -> bytes:
    """C6 01 08 08 - sync current data (steps/distance/cal). Resp: 26/06."""
    return bytes([0xC6, 0x01, 0x08, 0x08])


def set_time(dt: datetime | None = None) -> bytes:
    """C2 07 yy MM dd hh mm ss week xor(...). week: Monday=1..Sunday=7."""
    dt = dt or datetime.now()
    yy = dt.year % 100
    week = dt.isoweekday()
    payload = bytes([yy, dt.month, dt.day, dt.hour, dt.minute, dt.second, week])
    return bytes([0xC2, 0x07]) + payload + bytes([xor_chk(payload)])


def get_time() -> bytes:
    """89 00 00 - read device time. Resp: 29 + yy MM dd hh mm ss."""
    return bytes([0x89, 0x00, 0x00])


def read_device_info() -> bytes:
    """FB 00 00 - read firmware version. Resp: FB + version bytes."""
    return bytes([0xFB, 0x00, 0x00])


def sync_history(week: int, hour: int, hours: int) -> bytes:
    """C4 03 week hour hours xor(...). Begin curve/history sync."""
    payload = bytes([week, hour, hours])
    return bytes([0xC4, 0x03]) + payload + bytes([xor_chk(payload)])


def clear_data() -> bytes:
    """88 00 00 - clear all device data. Resp: 28 (ok) / 08 (err)."""
    return bytes([0x88, 0x00, 0x00])


def request_offline(since_ts: int | None = None) -> bytes:
    """B1 11 <unix ts BE> 0...0 xor(2..18). Offline history since ts."""
    ts = since_ts if since_ts is not None else int(datetime.now().timestamp())
    frame = bytearray(20)
    frame[0] = 0xB1
    frame[1] = 0x11
    frame[2:6] = struct.pack(">I", ts)
    frame[19] = xor_chk(frame[2:19])
    return bytes(frame)


def get_running(yy: int, month: int, day: int) -> bytes:
    """A2 00 yy MM dd 0...0 xor(2..18). Running data for a date. Resp: A2."""
    frame = bytearray(20)
    frame[0] = 0xA2
    frame[1] = 0x00
    frame[2:5] = bytes([yy, month, day])
    frame[19] = xor_chk(frame[2:19])
    return bytes(frame)


def band_mode(vibrate: bool = True, ring: bool = True, sleeping: bool = False) -> bytes:
    """F4 01 mode mode - bit7 sleeping, bit6 vibrate, bit5 ring."""
    m = 0
    if sleeping:
        m |= 0x80
    if vibrate:
        m |= 0x40
    if ring:
        m |= 0x20
    return bytes([0xF4, 0x01, m, m])


def heart_measure(start: bool = True) -> bytes:
    """93 11 80 00 00 00 01/00 01/00 0...0 chk - heart measurement switch.

    startExcercise layout (PremierMainFragment.java:2124): byte6/7 = 1,1 to
    start, 0,0 to stop. VERIFIED live 2026-08-19:
    `93 11 80 00 00 00 01 01 ...` -> 33 success + live 99 bpm stream.
    (setHeartMode's 02 00 01 layout at :2094 does NOT start on this watch.)"""
    frame = bytearray(20)
    frame[0] = 0x93
    frame[1] = 0x11
    frame[2] = 0x80
    if start:
        frame[6] = 1
        frame[7] = 1
    frame[19] = xor_chk(frame[2:19])
    return bytes(frame)


def bp_measure(start: bool = True) -> bytes:
    """93 11 40 00 00 00 1 1 chk - blood pressure measurement
    (PremierMainFragment.startExcercise, sensor 2 = 0x40)."""
    frame = bytearray(20)
    frame[0] = 0x93
    frame[1] = 0x11
    frame[2] = 0x40
    if start:
        frame[6] = 1
        frame[7] = 1
    frame[19] = xor_chk(frame[2:19])
    return bytes(frame)


def sport_mode(mode: int = 0x80, start: bool = True) -> bytes:
    """93 11 mode 00 00 00 on on chk - continuous sensor mode (same as
    bp_measure for 0x40; heart continuous = 0x80). Resp: 33 / 13."""
    frame = bytearray(20)
    frame[0] = 0x93
    frame[1] = 0x11
    frame[2] = mode
    frame[6] = 1 if start else 0
    frame[7] = 1 if start else 0
    frame[19] = xor_chk(frame[2:19])
    return bytes(frame)


def alert_notify(flag: int) -> bytes:
    """F1 01 flag - short alert, no text (BluetoothLeService.alertNotification
    short form). Flags: 0x80 call/WeChat, 0x40 SMS/QQ, 0x20 email/Skype,
    0x08 WhatsApp, 0x04 Facebook, 0x02 Twitter."""
    return bytes([0xF1, 0x01, flag])


def alert_notify_ex(flag: int, typ: int, extra: int) -> bytes:
    """F1 02 flag type extra chk - short notification alert (no text)."""
    return bytes([0xF1, 0x02, flag, typ, extra, flag ^ typ ^ extra])


def update_device_params(
    vibrate: int = 2, is_24h: bool = True, unit: int = 0,
    screen_time: int = 8, wrist: int = 0, lang: int = 0,
) -> bytes:
    """9B 0F ... - device params (BluetoothLeService.updateDeviceParams).
    byte7 vibration: 0=off, 2=vibrate, 1=vibrate+anti-lost."""
    frame = bytearray(18)
    frame[0] = 0x9B
    frame[1] = 0x0F
    frame[2] = 1 if is_24h else 0
    frame[3] = unit
    frame[4] = screen_time
    frame[5] = 0
    frame[6] = unit + 1
    frame[7] = vibrate
    frame[8] = wrist
    frame[9] = lang
    frame[17] = xor_chk(frame[2:17])
    return bytes(frame)


def ancs_frames(app_idx: int, flag: int, title: str = "", content: str = "") -> list[bytes]:
    """A4 ANCS push - mirrors alertNotification() at BluetoothLeService.java:4113.
    Sequence: A4 00 idx flag | A4 01 <title> | A4 02 <content> | A4 03 end.
    Every frame 20 bytes, chk = xor(bytes 2..18). The watch vibrates on
    receipt; icon follows app_idx (0 call, 1 mail, 2 discord, 3 snapchat,
    4 facebook, 5 skype, 6 twitter, 7 whatsapp, 8 line, 10 instagram,
    11 linkedin; 9 & 12+ show nothing)."""

    def chunk(sub: int, payload: bytes) -> bytes:
        f = bytearray(20)
        f[0] = 0xA4
        f[1] = sub
        n = min(len(payload), 17)
        f[2 : 2 + n] = payload[:n]
        f[19] = xor_chk(f[2:19])
        return bytes(f)

    frames = [chunk(0, bytes([app_idx, flag]))]
    if title:
        t = title.encode()
        for i in range(0, len(t), 17):
            frames.append(chunk(1, t[i : i + 17]))
    if content:
        c = content.encode()
        for i in range(0, len(c), 17):
            frames.append(chunk(2, c[i : i + 17]))
    else:
        frames.append(chunk(2, b""))
    frames.append(chunk(3, b""))
    return frames


def ack_call() -> bytes:
    """2F 00 00 - ack after answering/ending a call (resp to 9F)."""
    return bytes([0x2F, 0x00, 0x00])


def enter_ota() -> bytes:
    """FC 00 00 - enter DFU/OTA mode. WATCH LEAVES NORMAL MODE."""
    return bytes([0xFC, 0x00, 0x00])


# ---------------------------------------------------------------- decoding


def hexdump(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def describe_cmd(data: bytes) -> str:
    """Human meaning of a frame we send (mirrors describe_resp for rx)."""
    if not data:
        return "empty frame"
    op = data[0]
    if op == 0xC6:
        return "sync steps/dist/cal"
    if op == 0xC2 and len(data) >= 10:
        y, mo, d, h, mi, s, w = data[2:9]
        return (f"set time 20{y:02d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:"
                f"{s:02d} (week {w})")
    if op == 0x89:
        return "get time"
    if op == 0xFB:
        return "read firmware version"
    if op == 0xC4 and len(data) >= 6:
        return (f"history sync week={data[2]} hour={data[3]} "
                f"hours={data[4]}")
    if op == 0x88:
        return "clear data"
    if op == 0xB1 and len(data) >= 6:
        ts = int.from_bytes(data[2:6], "big")
        t = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
        return f"offline history since {t}"
    if op == 0xA2 and len(data) >= 5:
        return f"running data 20{data[2]:02d}-{data[3]:02d}-{data[4]:02d}"
    if op == 0xF4 and len(data) >= 3:
        m = data[2]
        flags = []
        if m & 0x80:
            flags.append("sleep")
        if m & 0x40:
            flags.append("vibrate")
        if m & 0x20:
            flags.append("ring")
        return f"band mode 0x{m:02X} ({','.join(flags) or 'none'})"
    if op == 0x9B and len(data) >= 8:
        return f"device params vib={data[7]} 24h={bool(data[2])}"
    if op == 0xA4 and len(data) >= 3:
        sub = data[1]
        if sub == 0:
            idx, flag = data[2], data[3] if len(data) > 3 else 0
            name = APP_NAMES.get(idx, f"app{idx}")
            return f"notify {name} (idx {idx}, flag 0x{flag:02X})"
        if sub == 1:
            return "notify title ..."
        if sub == 2:
            return "notify text ..."
        if sub == 3:
            return "notify end"
        return f"notify sub {sub}"
    if op == 0xF1:
        if len(data) >= 3 and data[1] == 1:
            return f"alert flag=0x{data[2]:02X}"
        if len(data) >= 5 and data[1] == 2:
            return f"alert ex flag=0x{data[2]:02X} type={data[3]}"
    if op == 0x93 and len(data) >= 3:
        kind = {0x80: "heart", 0x40: "blood pressure"}.get(
            data[2], f"mode 0x{data[2]:02X}")
        start = (data[8] == 1 if len(data) > 8 else False) or (
            len(data) > 8 and data[8] != 0xFF and (data[6] or data[7]))
        return f"{kind} {'start' if start else 'stop'}"
    if op == 0x2F:
        return "call ack"
    if op == 0xFC:
        return "ENTER DFU MODE"
    return f"raw 0x{op:02X}"


def decode_offline(data: bytes) -> list[tuple[int, int, int, int]]:
    """Decode a fully reassembled B1 offline-history frame.

    Returns [(type, unix_ts, v1, v2), ...]. type 1 = heart (bpm = v2),
    type 2 = blood pressure (v1 = sys, v2 = dia). Record stride 7 bytes."""
    out = []
    i = 3
    while i + 7 <= len(data):
        typ = data[i]
        ts = int.from_bytes(data[i + 1 : i + 5], "big")
        out.append((typ, ts, data[i + 5], data[i + 6]))
        i += 7
    return out


def describe_resp(data: bytes) -> str:
    """Human-readable interpretation of a watch response frame."""
    if not data:
        return "empty"
    op = data[0]
    known = {
        0x26: "current data (steps/dist/cal)",
        0x06: "current data error",
        0x29: "device time",
        0x09: "device time read error",
        0x22: "set time ok",
        0x02: "set time error",
        0x08: "clear data error",
        0x28: "clear data ok",
        0x23: "set personal info ok",
        0x03: "set personal info error",
        0x32: "set idle info ok",
        0x0D: "set idle info error",
        0x42: "threshold set ok",
        0x12: "threshold set error",
        0x24: "history curve data",
        0x04: "curve data error",
        0xB1: "offline history records",
        0x61: "offline history: no data",
        0xA2: "running data for date",
        0x93: "sensor state",
        0x99: "sensor value",
        0x33: "sensor switch ok",
        0x13: "sensor switch error",
        0xF3: "alarm event",
        0xF5: "take picture",
        0xF6: "music control",
        0x9F: "call control",
        0xA4: "notification ack",
        0xE3: "weather refresh request",
        0xF7: "(F7 no-op)",
        0xF8: "(F8 no-op)",
        0xFB: "firmware version",
    }
    s = known.get(op, f"unknown 0x{op:02X}")

    if op == 0x26 and len(data) >= 11:
        steps = int.from_bytes(data[2:5], "big")
        dist = int.from_bytes(data[5:8], "big")
        cal = int.from_bytes(data[8:11], "big")
        return f"{s}: steps={steps} dist={dist}m cal={cal}kcal"
    if op == 0x29 and len(data) >= 8:
        y, mo, d, h, mi, se = data[2:8]
        return f"{s}: 20{y:02d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:{se:02d}"
    if op == 0xFB and len(data) >= 14:
        raw = data[2:14]
        try:
            return f"{s}: {raw.decode('ascii')}"
        except UnicodeDecodeError:
            return f"{s}: hex={hexdump(raw)}"
    if op == 0x99 and len(data) >= 8:
        stype = int.from_bytes(data[2:6], "big")
        if stype == 0x80000000:
            bpm = int.from_bytes(data[6:8], "big")
            return f"HEART {bpm} bpm" if bpm else "HEART measuring ..."
        if stype == 0x40000000:
            return f"BLOOD PRESSURE {data[6]}/{data[7]} mmHg"
        return f"{s}: stype=0x{stype:08X}"
    if op == 0x93 and len(data) >= 8:
        stype = int.from_bytes(data[2:6], "big")
        on = 1 if int.from_bytes(data[6:8], "big") else 0
        kind = "heart" if stype == 0x80000000 else "blood pressure"
        return f"{s}: {kind} {'on' if on else 'off'}"
    if op == 0xA2 and len(data) >= 11:
        d = data[2]
        if d == 4:
            return (f"running {data[3]:02d}-{data[4]:02d}-{data[5]:02d} "
                    f"steps={int.from_bytes(data[6:8], 'big')} "
                    f"dist={int.from_bytes(data[8:11], 'big')}m "
                    f"cal={int.from_bytes(data[11:13], 'big')}kcal")
    if op == 0xF6 and len(data) >= 3:
        m = {1: "toggle/pause", 2: "prev", 3: "next", 4: "stop",
             5: "vol+", 6: "vol-"}.get(data[2], hex(data[2]))
        return f"{s}: {m}"
    if op == 0xF3 and len(data) >= 3:
        return f"{s}: {hex(data[2])}"
    return s