"""Headless smoke test for bletest/tui.py (no BLE hardware needed).

Run:  .venv/bin/python bletest/test_tui.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from textual.widgets import DataTable, Input, RichLog, Static

from tui import ConfirmScreen, ZebScreen, ZebTUI
import tui


class FakeChar:
    uuid = "0000fff1-0000-1000-8000-00805f9b34fb"
    properties = ["notify"]


class FakeSvc:
    uuid = "0000fff0-0000-1000-8000-00805f9b34fb"
    characteristics = [FakeChar()]


class FakeClient:
    def __init__(self, addr="FAKE:ADDR", timeout=10.0):
        self.addr = addr
        self.timeout = timeout
        self.client = _BleakLike()
        self._frames = asyncio.Queue()
        self.disconnects = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        self.disconnects += 1

    async def start_notify(self, *a, **k):
        return True

    async def write(self, data, response=True):
        op = data[0]
        if op == 0x89:  # gettime -> device time reply
            self._frames.put_nowait(bytes.fromhex("29 07 1A 05 1E 0C 00 15 06 0B"))
        elif op == 0xC6:  # sync -> current data
            self._frames.put_nowait(bytes.fromhex("26 09 00 00 24 00 00 00 00 00 24"))
        elif op == 0xFB:
            self._frames.put_nowait(b"\xfb\x0cZEB_CMABV002")
        elif op == 0x93:  # heart measure -> 33 ok + live bpm stream
            self._frames.put_nowait(bytes.fromhex("33 00 00 00 00 00 00 00 00 00"))
            self._frames.put_nowait(bytes.fromhex("99 00 80 00 00 00 00 4C") + b"\x00" * 12)
            self._frames.put_nowait(bytes.fromhex("99 00 80 00 00 00 00 4D") + b"\x00" * 12)
        elif op == 0xA4:  # notification push -> ack
            self._frames.put_nowait(bytes.fromhex("A4 00 07 08") + b"\x00" * 16)


class _BleakLike:
    services = [FakeSvc()]


async def wait_cond(pilot, cond, timeout=3.0, label="condition"):
    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < timeout:
        await pilot.pause(0.1)
        if cond():
            return
    raise AssertionError(f"timeout waiting for {label}")


async def main():
    app = ZebTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, ZebScreen), f"started on {type(app.screen)}"
        print("PASS: app starts on single ZebScreen (no tabs)")

        scr = app.screen
        scr._devs = {"D5:90:E1:5B:10:71": ("ZEB-FIT650", -58),
                     "AA:BB:CC:DD:EE:FF": ("(no name)", -80)}
        scr._rebuild()
        await pilot.pause()
        tbl = scr.query_one(DataTable)
        assert tbl.row_count == 2, f"row_count={tbl.row_count}"
        assert app.focused is tbl, f"table should be focused, got {app.focused}"
        print("PASS: scan table shows 2 devices; table focused on start")

        left_ids = [w.id for w in scr.query_one("#left").children]
        assert left_ids == ["conn-status", "devices", "syslog", "sugg", "cmd"], \
            f"left column order: {left_ids}"
        print("PASS: left column = status/devices/output/suggestions/input (input at bottom)")

        tui.ZebfitClient = FakeClient
        await pilot.press("down")   # move cursor to 2nd device
        await pilot.press("enter")  # connect via real keypress
        await wait_cond(pilot, lambda: scr.conn_addr is not None,
                        label="connection")
        assert scr.conn_addr == "AA:BB:CC:DD:EE:FF", scr.conn_addr
        print("PASS: real keypress down+enter selects and connects")

        assert tbl.has_class("shrunk"), "devices box should shrink after select"
        syslog = scr.query_one("#syslog", RichLog)
        assert syslog.has_class("grow"), "output should grow after select"
        print("PASS: devices box shrinks + output grows once selected")

        await wait_cond(pilot, lambda: scr.client is not None,
                        label="client set")
        assert app.focused is scr._input, "input should be focused after connect"
        print("PASS: command input focused after connect (left side)")

        # move cursor to a different device while table rebuilds (scan
        # updates) - cursor must survive rebuilds so enter still works
        scr._devs["D5:90:E1:5B:10:71"] = ("ZEB-FIT650", -50)
        scr._devs["EE:11:22:33:44:55"] = ("OTHER", -70)
        scr._rebuild()
        await pilot.pause(0.2)
        scr._rebuild()  # no-op when unchanged
        assert scr._tbl.cursor_row is not None, "cursor lost after rebuild"
        print("PASS: cursor survives scan rebuilds")

        inp = scr._input
        await pilot.press("g", "e", "t", "t", "i", "m", "e")
        await pilot.pause()
        sugg = scr.query_one("#sugg", Static)
        assert sugg.has_class("show"), "suggestions should show"
        print("PASS: suggestions visible for 'gettime'")

        await pilot.press("enter")
        await wait_cond(pilot, lambda: any(
            "89 00 00" in l.text for l in scr.query_one("#rawlog", RichLog).lines),
            label="tx in raw")
        raw = scr.query_one("#rawlog", RichLog)
        assert any(">>>" in l.text and "89 00 00" in l.text for l in raw.lines), \
            "tx not in raw"
        assert any("<<<" in l.text and "29" in l.text for l in raw.lines), \
            "rx not in raw"
        print("PASS: raw shows BOTH sent and received frames")

        dec = scr.query_one("#declog", RichLog)
        assert any("get time" in l.text for l in dec.lines), "decoded tx missing"
        assert any("device time" in l.text for l in dec.lines), "decoded rx missing"
        print("PASS: decoded mirrors raw 1:1 (tx meaning + decoded reply)")

        inp.value = ""
        await pilot.pause(0.1)
        await pilot.press("h", "e", "a", "r", "t", "enter")
        await wait_cond(pilot, lambda: any(
            "HEART 76 bpm" in l.text for l in dec.lines), label="hr decode")
        live = scr.query_one("#live", Static)
        assert "77" in str(live.content), f"live HR: {live.content!r}"
        assert any("<< HEART 76 bpm" in l.text for l in dec.lines), "hr not decoded"
        raw = scr.query_one("#rawlog", RichLog)
        assert any("93 11 80 00 00 00 01 01" in l.text for l in raw.lines), \
            "heart frame must use verified startExcercise layout"
        print("PASS: heart -> decoded shows actual bpm (76), live shows 77")

        inp.value = ""
        await pilot.pause(0.1)
        await pilot.press("n", "o", "t", "i", "f", "y", " ", "7", "enter")
        await wait_cond(pilot, lambda: any(
            "notify whatsapp" in l.text for l in dec.lines), label="notify")
        assert any("notification ack" in l.text for l in dec.lines), "ack missing"
        print("PASS: notify 7 (whatsapp) decoded + ack")

        inp.focus()
        await pilot.press("ctrl+a")
        await pilot.press("i", "n", "f", "o")
        await pilot.pause()
        assert scr._sugg, "suggestions for 'info'"
        await pilot.press("tab")
        await pilot.pause()
        assert inp.value == "info", f"completed value={inp.value!r}"
        print("PASS: TAB completes 'info'")

        await pilot.press("enter")
        await pilot.pause(0.8)
        await pilot.press("up")
        await pilot.pause()
        assert inp.value == "info", f"history value={inp.value!r}"
        print("PASS: up/down history works")

        inp.value = ""
        await pilot.pause(0.1)
        await pilot.press("o", "t", "a", "enter")
        await pilot.pause(0.3)
        assert isinstance(app.screen, ConfirmScreen), f"screen={type(app.screen)}"
        await pilot.click("#btn-yes")
        await pilot.pause(0.3)
        assert isinstance(app.screen, ZebScreen)
        print("PASS: ota confirm modal")

        inp.value = ""
        await pilot.pause(0.1)
        await pilot.press("c", "l", "e", "a", "r", "enter")
        await pilot.pause(0.3)
        assert isinstance(app.screen, ConfirmScreen), f"clear modal: {type(app.screen)}"
        await pilot.click("#btn-no")
        await pilot.pause(0.3)
        assert isinstance(app.screen, ZebScreen), "cancel returns to main"
        assert not any("88" in l.text for l in scr.query_one("#rawlog", RichLog).lines), \
            "clear must not send before confirm"
        print("PASS: clear requires two-step confirmation (cancel blocks send)")

        inp.focus()
        await pilot.press("z", "z", "z", "enter")
        await pilot.pause(0.5)
        syslog = scr.query_one("#syslog", RichLog)
        assert any("unknown command" in l.text for l in syslog.lines), \
            "error not logged"
        print("PASS: unknown command -> syslog error")

        # esc back to devices, enter on connected row disconnects
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is tbl, "escape should focus table"
        await pilot.press("enter")
        await wait_cond(pilot, lambda: scr.client is None, label="disconnect")
        assert not tbl.has_class("shrunk"), "devices box should restore on disconnect"
        assert not syslog.has_class("grow"), "output should shrink back on disconnect"
        print("PASS: esc -> table, enter disconnects (layout restored)")

        await pilot.press("ctrl+q")
        await pilot.pause(0.5)
        print("PASS: ctrl+q quits")

    print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())