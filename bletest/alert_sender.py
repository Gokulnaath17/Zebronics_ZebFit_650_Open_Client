#!/usr/bin/env python3
"""alert_sender.py - push health alerts to the Zebfit650 watch.

Broadcasts one alert per interval as an A4 ANCS push with the MAIL icon.
Title = two-line header: line 1 = category padded to 11 chars (invisible
fill, nothing clips), line 2 = WARNING. Body = short grammatical
cardiac/pneumo/spo2 text that streams across frames.

Controls:
  F5 (camera shutter)            -> start cycling alerts
  F6 (music control, any key)    -> switch category (CARDIAC -> PNEUMO -> SPO2)
  93 heart-off presses (window of 7 s between presses):
      1st  -> 20 s rest (mute)
      2nd  -> switch interval: 30 s -> 10 s -> 1 min -> 30 s ...
      3rd+ -> stop everything and EXIT the program
  (a press more than 7 s after the last resets the count to 1st)

Usage:
  .venv/bin/python bletest/alert_sender.py [addr] [--interval 10]
Exit codes: 0 = clean disconnect, 3 = stopped by watch (3x heart-off).
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time

from bleak import BleakClient, BleakScanner

import protocol as P

MAIL_IDX, MAIL_FLAG = 1, 0x40
HEART_STYPE = 0x80000000
OFF_WINDOW = 7.0
MUTE_SECONDS = 20.0
INTERVALS = (30.0, 10.0, 60.0)
CATEGORIES = ("CARDIAC", "PNEUMO", "SPO2")


def _rnd(a: int, b: int) -> int:
    return random.randint(a, b)


def _head(cat: str) -> str:
    return f"{cat:<11}\nWARNING"


# category -> (header, severity, body) - headers are 11+1+<=12 chars so the
# watch renders exactly two lines; bodies are compact but grammatical.
ALERTS: dict[str, list[tuple[str, str, object]]] = {
    "CARDIAC": [
        (_head("CARDIAC"), "Severe", lambda: f"Heart rate high: {_rnd(150, 200)} bpm (Severe)"),
        (_head("CARDIAC"), "Warning", lambda: f"Heart rate low: {_rnd(40, 58)} bpm (Warning)"),
        (_head("CARDIAC"), "Severe", lambda: f"Rhythm irregular: {_rnd(90, 140)} bpm (Severe)"),
        (_head("CARDIAC"), "Warning", lambda: f"Pulse irregular: {_rnd(95, 135)} bpm (Warning)"),
        (_head("CARDIAC"), "Severe", lambda: f"Cardiac load high: {_rnd(150, 195)} bpm (Severe)"),
        (_head("CARDIAC"), "Severe", lambda: f"Cardiac stress high: {_rnd(140, 185)} bpm (Severe)"),
    ],
    "PNEUMO": [
        (_head("PNEUMO"), "Warning", lambda: f"Breathing rapid: {_rnd(22, 30)}/min (Warning)"),
        (_head("PNEUMO"), "Warning", lambda: f"Breathing shallow: {_rnd(8, 12)}/min (Warning)"),
        (_head("PNEUMO"), "Severe", lambda: f"Breathing strained: {_rnd(26, 34)}/min (Severe)"),
    ],
    "SPO2": [
        (_head("SPO2"), "Severe", lambda: f"Oxygen low: {_rnd(85, 90)}% (Severe)"),
        (_head("SPO2"), "Warning", lambda: f"Oxygen dropping: {_rnd(90, 94)}% (Warning)"),
    ],
}

DEFAULT_INTERVAL = 10.0


class AlertRunner:
    def __init__(self, interval: float = DEFAULT_INTERVAL):
        self.interval = interval
        self.running = False
        self.category_idx = 0
        self.alert_idx = 0
        self.client: BleakClient | None = None
        self._lock = asyncio.Lock()
        self._immediate = False
        self._off_count = 0
        self._last_off_ts = 0.0
        self._mute_until = 0.0
        self._interval_pos = -1
        self._exit = False

    # ------------------------------------------------------------ control

    def _on_notify(self, _handle: int, data: bytearray):
        frame = bytes(data)
        try:
            if frame[0] == 0xF5:
                print("*** camera shutter: START")
                self.running = True
                self._immediate = True
                self._off_count = 0
                self._last_off_ts = 0.0
                asyncio.ensure_future(self._kick_hr())
            elif frame[0] == 0xF6:
                self.category_idx = (self.category_idx + 1) % len(CATEGORIES)
                self.alert_idx = 0
                print(f"*** music control: category -> {CATEGORIES[self.category_idx]}")
            elif frame[0] == 0x93 and len(frame) >= 8:
                stype = int.from_bytes(frame[2:6], "big")
                on = int.from_bytes(frame[6:8], "big")
                if stype == HEART_STYPE and not on:
                    now = time.monotonic()
                    if now - self._last_off_ts > OFF_WINDOW:
                        self._off_count = 0  # window expired, fresh count
                    self._off_count += 1
                    self._last_off_ts = now
                    if self._off_count == 1:
                        self._mute_until = now + MUTE_SECONDS
                        print(f"heart-off 1/3: muted for {MUTE_SECONDS:g} s")
                    elif self._off_count == 2:
                        self._interval_pos = (self._interval_pos + 1) % len(INTERVALS)
                        self.interval = INTERVALS[self._interval_pos]
                        print(f"heart-off 2/3: interval -> {self.interval:g}s")
                    else:
                        self._stop_all(f"heart-off {self._off_count}x within {OFF_WINDOW:g} s")
                        self._exit = True
        except Exception as e:  # noqa: BLE001 - never die in the notifier
            print(f"notify handler error: {e}")

    def _stop_all(self, why: str):
        if self.running:
            print(f"*** STOP: {why}")
        self.running = False
        self._immediate = False

    # ------------------------------------------------------------ sending

    async def _kick_hr(self):
        try:
            await self.client.write_gatt_char(P.UUID_TX, P.heart_measure(True))
        except Exception as e:  # noqa: BLE001
            print(f"heart start error: {e}")

    async def send_alert(self):
        header, sev, body_fn = ALERTS[CATEGORIES[self.category_idx]][self.alert_idx]
        body = body_fn()
        for fr in P.ancs_frames(MAIL_IDX, MAIL_FLAG, header, body):
            await self.client.write_gatt_char(P.UUID_TX, fr)
            await asyncio.sleep(0.3)
        print(f">>> alert [{header.replace(chr(10), ' / ').strip()} "
              f"({sev})]: {body!r}")

    async def alert_loop(self):
        next_send = 0.0
        while True:
            await asyncio.sleep(1)
            async with self._lock:
                now = time.monotonic()
                muted = now < self._mute_until
                if muted and self._immediate:
                    self._immediate = False  # holds the next alert for unmute
                if self.running and not muted and (self._immediate or
                                                   now >= next_send):
                    self._immediate = False
                    await self.send_alert()
                    self.alert_idx = (self.alert_idx + 1) % len(
                        ALERTS[CATEGORIES[self.category_idx]])
                    next_send = now + self.interval

    # -------------------------------------------------------------- main

    async def run(self, addr: str | None):
        if addr:
            device = await BleakScanner.find_device_by_address(addr, timeout=10)
        else:
            print("scanning for ZEB-FIT650 ...")
            device = await BleakScanner.find_device_by_name(
                "ZEB-FIT650", timeout=10)
        if not device:
            print("watch not found; is it advertising?")
            return 1

        async with BleakClient(device, timeout=10) as client:
            self.client = client
            await client.start_notify(P.UUID_RX, self._on_notify)
            await client.write_gatt_char(P.UUID_TX, P.sync_current())
            await asyncio.sleep(0.5)
            await client.write_gatt_char(P.UUID_TX, P.set_time())
            print(f"connected to {device.address}; waiting for F5 to start. "
                  f"interval={self.interval:g}s")

            task = asyncio.create_task(self.alert_loop())
            try:
                while client.is_connected and not self._exit:
                    await asyncio.sleep(0.5)
            finally:
                task.cancel()
                try:
                    await client.write_gatt_char(
                        P.UUID_TX, P.heart_measure(False))
                except Exception:  # noqa: BLE001
                    pass
        if self._exit:
            print("*** exited by watch (3x heart-off)")
        return 3 if self._exit else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("addr", nargs="?", help="watch MAC; scan if omitted")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                    help=f"seconds between alerts (default {DEFAULT_INTERVAL})")
    args = ap.parse_args()
    try:
        rc = asyncio.run(AlertRunner(args.interval).run(args.addr))
    except KeyboardInterrupt:
        rc = 130
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}")
        rc = 2
    sys.exit(rc)


if __name__ == "__main__":
    main()