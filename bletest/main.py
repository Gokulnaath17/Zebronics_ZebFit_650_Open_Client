#!/usr/bin/env python3
"""Interactive test harness for the Zebfit650 BLE watch.

Uses bleak (bluez backend) on the host system Bluetooth stack.

Usage:
  python main.py scan                     # list nearby BLE devices
  python main.py info <addr>              # connect + dump GATT services
  python main.py test <addr> <cmd> [...]  # send one command, wait for reply

Commands for `test`:
  sync          C6 01 08 08        sync current data
  time          set device time to host clock
  gettime       89 00 00           read device time
  info          FB 00 00           read firmware version
  hist          start history sync (args: week hour hours)
  clear         88 00 00           clear device data
  offline       B1 11 + ts         request offline history
  running       A2 00 yy MM dd     running data for date
  bandmode      F4 01 <mode> <mode>
  callack       2F 00 00           ack call control
  vibparams     [mode]             9B 0F device params (byte7 vib: 0/1/2)
  ancs <idx> <flag> [title] [content]
                                  A4 ANCS push (multi-frame)
  vibrate       vibparams(2) + WhatsApp-style A4 push (full sequence)
  timeformat    [12|24]           set watch time format (default: 24h)
  sweep <start> <end> [gap s]
                                  A4 pushes for each appIdx (icon probe)
  ota           FC 00 00           enter DFU mode (DANGEROUS)

Exit: 0 = reply seen, 1 = timeout/no reply, 2 = error.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

import bleak
from bleak import BleakClient, BleakScanner

import protocol as P


class ZebfitClient:
    def __init__(self, addr: str, timeout: float = 10.0):
        self.addr = addr
        self.timeout = timeout
        self.client: BleakClient | None = None
        self._frames: asyncio.Queue[bytes] = asyncio.Queue()

    async def __aenter__(self):
        self.client = BleakClient(self.addr, timeout=self.timeout)
        await self.client.connect()
        self._cb_handle = None
        self._cb_uuid = None
        return self

    async def __aexit__(self, *exc):
        if self.client and self.client.is_connected:
            await self.client.disconnect()

    def _on_notify(self, _handle: int, data: bytearray):
        self._frames.put_nowait(bytes(data))

    async def discover(self):
        for svc in self.client.services:
            print(f"  SERVICE {svc.uuid}")
            for ch in svc.characteristics:
                print(f"    {ch.uuid} props={ch.properties}")

    async def start_notify(self, uuid: str = P.UUID_RX) -> bool:
        await self.client.start_notify(uuid, self._on_notify)
        return True

    async def write(self, data: bytes, response: bool = True):
        await self.client.write_gatt_char(P.UUID_TX, data, response=response)

    async def read_reply(self, timeout: float, max_frames: int = 64) -> list[bytes]:
        """Collect notification frames for `timeout` seconds, draining the queue."""
        deadline = asyncio.get_event_loop().time() + timeout
        out = []
        try:
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                frame = await asyncio.wait_for(self._frames.get(), remaining)
                out.append(frame)
                if len(out) >= max_frames:
                    break
        except asyncio.TimeoutError:
            pass
        return out


async def do_scan(args):
    print(f"scanning {args.timeout}s for BLE devices ...")
    found: dict[str, tuple[str, int]] = {}

    def cb(device, adv):
        rssi = adv.rssi
        old = found.get(device.address)
        if old is None or rssi > old[1]:
            found[device.address] = (device.name or "(no name)", rssi)

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(args.timeout)
    await scanner.stop()
    if not found:
        print("no devices found")
        return 1
    for addr, (name, rssi) in sorted(found.items(), key=lambda kv: -kv[1][1]):
        print(f"  {addr}  {name:24s} rssi={rssi}")
    return 0


async def do_info(args):
    async with ZebfitClient(args.addr) as z:
        await z.discover()
    return 0


def _build_cmd(name: str, extra: list[str]) -> bytes:
    if name == "sync":
        return P.sync_current()
    if name == "time":
        if extra:
            dt = datetime.strptime(" ".join(extra[:2]), "%Y-%m-%d %H:%M:%S")
            return P.set_time(dt)
        return P.set_time()
    if name == "gettime":
        return P.get_time()
    if name == "info":
        return P.read_device_info()
    if name == "hist":
        week, hour, hours = (int(x, 0) for x in extra[:3])
        return P.sync_history(week, hour, hours)
    if name == "clear":
        return P.clear_data()
    if name == "offline":
        ts = int(extra[0], 0) if extra else int(datetime.now().timestamp())
        return P.request_offline(ts)
    if name == "running":
        yy, mo, day = (int(x, 0) for x in extra[:3])
        return P.get_running(yy, mo, day)
    if name == "bandmode":
        m = int(extra[0], 0) if extra else 0x60
        return bytes([0xF4, 0x01, m, m])
    if name == "callack":
        return P.ack_call()
    if name == "vibparams":
        mode = int(extra[0], 0) if extra else 2
        return P.update_device_params(vibrate=mode)
    if name == "timeformat":
        fmt = extra[0] if extra else "24"
        if fmt not in ("12", "24"):
            raise SystemExit("timeformat: 12 or 24")
        return P.update_device_params(is_24h=(fmt == "24"))
    if name == "ancs":
        return P.ancs_frames(int(extra[0], 0), int(extra[1], 0),
                             extra[2] if len(extra) > 2 else "",
                             extra[3] if len(extra) > 3 else "")
    if name == "vibrate":
        return None  # handled in do_test
    if name == "sweep":
        return None  # handled in do_test
    if name == "notify":
        return bytes([0xF1, 0x01, int(extra[0], 0)])
    if name == "notifyex":
        a, b, c = (int(x, 0) for x in extra[:3])
        return P.alert_notify_ex(a, b, c)
    if name == "sport":
        mode = int(extra[0], 0) if extra else 0x04
        on = int(extra[1], 0) if len(extra) > 1 else 1
        return P.sport_mode(mode, bool(on))
    if name == "ota":
        return P.enter_ota()
    raise SystemExit(f"unknown command: {name}")


async def do_test(args):
    cmd = _build_cmd(args.cmd, args.args)
    print(f"conn {args.addr} ...")
    async with ZebfitClient(args.addr) as z:
        await z.discover()
        print("enabling notifications on", P.UUID_RX)
        await z.start_notify()
        await asyncio.sleep(0.5)

        if args.cmd == "vibrate":
            frames = [P.update_device_params(vibrate=2)] + P.ancs_frames(
                7, 0x08, "Zebfit Test", "Vibration test")
        elif args.cmd == "sweep":
            start, end = (int(x, 0) for x in args.args[:2])
            gap = float(args.args[2]) if len(args.args) > 2 else 3.0
            frames = []
            for idx in range(start, end + 1):
                flag = {0: 0x80, 1: 0x40, 2: 0x80, 3: 0x40, 4: 0x04, 5: 0x20,
                        6: 0x02, 7: 0x08, 9: 0x20, 10: 0x00, 11: 0x00}.get(idx, 0x00)
                frames.extend([(gap, fr) for fr in P.ancs_frames(
                    idx, flag, f"App {idx}", "icon")])
            for gap, fr in frames:
                print(f">>> idx={fr[2]:3d} send {P.hexdump(fr)}")
                await z.write(fr)
                await asyncio.sleep(gap if fr[1] == 3 else 0.3)
            frames_rx = await z.read_reply(2)
            for f in frames_rx:
                print(f"<<< {P.hexdump(f)}  ->  {P.describe_resp(f)}")
            return 0
        elif isinstance(cmd, list):
            frames = cmd
        else:
            frames = [cmd]

        for fr in frames:
            print(f">>> send {P.UUID_TX}: {P.hexdump(fr)}")
            await z.write(fr)
            await asyncio.sleep(0.3)
        frames_rx = await z.read_reply(args.timeout)
        if not frames_rx:
            print("!!! no notification received")
            return 1
        for f in frames_rx:
            print(f"<<< {P.hexdump(f)}  ->  {P.describe_resp(f)}")
    return 0


async def do_interact(args):
    """Interactive session: connect once, push notifications on demand."""
    print(f"conn {args.addr} ...")
    async with ZebfitClient(args.addr) as z:
        await z.discover()
        await z.start_notify()
        print("ready. commands:")
        print("  ancs <idx> <flag> [title] [content]   push a notification")
        print("  vibparams [mode]                      9B 0F params")
        print("  raw <hex...>                          send raw bytes")
        print("  q                                     quit")
        while True:
            try:
                line = (await asyncio.get_event_loop().run_in_executor(
                    None, input, "> ")).strip()
            except EOFError:
                break
            if not line:
                continue
            if line == "q":
                break
            parts = line.split()
            try:
                if parts[0] == "ancs":
                    idx, flag = (int(x, 0) for x in parts[1:3])
                    title = parts[3] if len(parts) > 3 else f"App {idx}"
                    content = parts[4] if len(parts) > 4 else "icon"
                    frames = P.ancs_frames(idx, flag, title, content)
                elif parts[0] == "vibparams":
                    mode = int(parts[1], 0) if len(parts) > 1 else 2
                    frames = [P.update_device_params(vibrate=mode)]
                elif parts[0] == "raw":
                    frames = [bytes.fromhex(parts[1])]
                else:
                    print("unknown command")
                    continue
            except (ValueError, IndexError):
                print("bad args")
                continue
            for fr in frames:
                print(f">>> send {P.hexdump(fr)}")
                await z.write(fr)
                await asyncio.sleep(0.3)
            rx = await z.read_reply(1.5)
            for f in rx:
                print(f"<<< {P.hexdump(f)}  ->  {P.describe_resp(f)}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="action", required=True)

    s = sub.add_parser("scan")
    s.add_argument("--timeout", type=int, default=12)
    s.set_defaults(func=do_scan)

    i = sub.add_parser("info")
    i.add_argument("addr")
    i.set_defaults(func=do_info)

    t = sub.add_parser("test")
    t.add_argument("addr")
    t.add_argument("cmd")
    t.add_argument("args", nargs="*")
    t.add_argument("--timeout", type=float, default=8.0)
    t.set_defaults(func=do_test)

    it = sub.add_parser("interact")
    it.add_argument("addr")
    it.add_argument("--timeout", type=float, default=10.0)
    it.set_defaults(func=do_interact)

    args = ap.parse_args()
    try:
        rc = asyncio.run(args.func(args))
    except KeyboardInterrupt:
        rc = 130
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}")
        rc = 2
    sys.exit(rc)


if __name__ == "__main__":
    main()