#!/usr/bin/env python3
"""Zebfit650 BLE TUI - one window, three panes.

LEFT   : all terminal interactions - device list (enter connects),
         command input, suggestions, system log.
RIGHT  : top = RAW (every sent + received frame, hex), bottom = DECODED
         (one human line per frame, mirrors RAW 1:1, live HR/BP).

Run:  .venv/bin/python bletest/tui.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bleak
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (Button, DataTable, Input, Label, RichLog, Static)

import protocol as P
from main import ZebfitClient

# ----------------------------------------------------------------- commands

# name: (args, description)
COMMANDS: dict[str, tuple[str, str]] = {
    "sync": ("", "read steps / distance / kcal from watch"),
    "time": ("[YYYY-MM-DD HH:MM:SS]", "set watch time (default: now)"),
    "gettime": ("", "read watch time"),
    "info": ("", "read firmware version"),
    "heart": ("[on|off]", "heart rate measurement (live bpm streams in)"),
    "bp": ("[on|off]", "blood pressure measurement"),
    "notify": ("<app> [title] [msg]", "push notification -> watch vibrates"),
    "vibmode": ("[0|1|2]", "vibration: 0 off, 1 on+anti-lost, 2 on"),
    "band": ("[mode]", "band mode flags (0x20 ring, 0x40 vibrate, 0x80 sleep)"),
    "hist": ("<week> <hour> <hours>", "begin history curve sync"),
    "offline": ("[unix_ts]", "offline history since timestamp"),
    "running": ("<yy> <MM> <dd>", "running data for a date"),
    "clear": ("", "erase all watch data"),
    "callack": ("", "ack a call"),
    "ota": ("", "enter DFU mode (DANGEROUS, asks confirm)"),
    "raw": ("<hex bytes>", "send a raw frame"),
    "help": ("", "show this help"),
}

APP_LIST = (f"{i:2d} {name}" for i, name in sorted(P.APP_NAMES.items()))

ACCENT = "#ffd700"   # iron man gold
CYAN = "#ff4136"     # arc reactor red
AMBER = "#ffc94d"
RED = "#ff4d4d"
DIM = "#9a6b5a"
BG = "#0b0505"
PANEL = "#150a09"
BORDER = "#4d1210"

APP_CSS = f"""
Screen {{
    background: {BG};
}}
#hdr {{
    background: {PANEL};
    color: {ACCENT};
    padding: 0 2;
    border-bottom: solid {BORDER};
    text-style: bold;
}}
#statbar {{
    background: {PANEL};
    color: {DIM};
    padding: 0 1;
    border-top: solid {BORDER};
}}
#cols {{
    layout: horizontal;
    height: 1fr;
}}
/* ---------------- left: all interactions ---------------- */
#left {{
    width: 50%;
    border-right: solid {BORDER};
    layout: vertical;
}}
#conn-status {{
    background: {PANEL};
    color: {CYAN};
    padding: 0 2;
    border-bottom: solid {BORDER};
    height: 1;
}}
#conn-status.error {{ color: {RED}; }}
#conn-status.done {{ color: {ACCENT}; }}
#conn-status.busy {{ color: {AMBER}; }}
#devices.-disabled {{
    opacity: 0.4;
    border: round {DIM};
}}
#devices.-disabled:focus {{ border: round {DIM}; }}
#cmd.-disabled {{
    opacity: 0.4;
    border: tall {DIM};
}}
#devices {{
    border: round {BORDER};
    margin: 1 2 0 2;
    height: 1fr;
}}
#devices:focus {{ border: round {ACCENT}; }}
#devices.shrunk {{ height: 7; }}
#sugg {{
    display: none;
    background: {PANEL};
    color: {DIM};
    border: solid {BORDER};
    max-height: 8;
    overflow-y: auto;
    padding: 0 1;
    text-wrap: nowrap;
    margin: 1 2 0 2;
}}
#sugg.show {{ display: block; }}
#sugg .sel {{ color: {ACCENT}; text-style: bold; }}
#cmd {{
    background: #060a08;
    border: tall {BORDER};
    color: {ACCENT};
    margin: 1 2 0 2;
}}
#cmd:focus {{ border: tall {CYAN}; }}
#syslog {{
    border: round {BORDER};
    background: {BG};
    height: 7;
    margin: 1 2 1 2;
    overflow-y: auto;
}}
#syslog.grow {{ height: 1fr; }}
/* ---------------- right: raw + decoded ---------------- */
#right {{
    width: 50%;
    layout: vertical;
}}
#raw-box, #dec-box {{
    height: 1fr;
}}
#raw-hdr, #dec-hdr {{
    background: {PANEL};
    color: {DIM};
    padding: 0 2;
    border-bottom: solid {BORDER};
    height: 1;
}}
#rawlog, #declog {{
    border: none;
    background: {BG};
    height: 1fr;
}}
#live {{
    background: {PANEL};
    color: {AMBER};
    padding: 0 2;
    border-bottom: solid {BORDER};
    height: 1;
    text-style: bold;
}}
ConfirmScreen {{
    align: center middle;
    background: rgba(0, 0, 0, 0.65);
}}
#confirm-box {{
    width: 62;
    height: auto;
    border: thick {AMBER};
    background: {PANEL};
    padding: 1 2;
}}
#confirm-title {{
    color: {AMBER};
    text-style: bold;
    margin-bottom: 1;
}}
#confirm-text {{ margin-bottom: 1; }}
#confirm-btns {{
    layout: horizontal;
    height: 3;
    align-horizontal: right;
}}
"""


# ------------------------------------------------------------- modal confirm


class ConfirmScreen(ModalScreen[bool]):
    def __init__(self, title: str, text: str, danger: bool = False):
        super().__init__()
        self._title = title
        self._text = text
        self._danger = danger

    def compose(self) -> ComposeResult:
        yield Container(
            Label(self._title, id="confirm-title"),
            Label(self._text, id="confirm-text"),
            Container(
                Button("CANCEL", id="btn-no", variant="default"),
                Button("CONFIRM", id="btn-yes",
                       variant="error" if self._danger else "primary"),
                id="confirm-btns",
            ),
            id="confirm-box",
        )

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "btn-yes")


# --------------------------------------------------------------- command in


class CmdInput(Input):
    """Input that hijacks up/down/tab for suggestion & history navigation."""

    def on_key(self, event: events.Key):
        if event.key == "up":
            event.prevent_default()
            event.stop()
            self.screen.action_up()
        elif event.key == "down":
            event.prevent_default()
            event.stop()
            self.screen.action_down()
        elif event.key == "tab":
            event.prevent_default()
            event.stop()
            self.screen.action_complete()


# -------------------------------------------------------------- main screen


class ZebScreen(Screen):
    BINDINGS = [
        Binding("enter", "select", "connect", show=False),
        Binding("i", "focus_input", "type command"),
        Binding("escape", "focus_table", "devices"),
        Binding("r", "rescan", "rescan"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self):
        super().__init__()
        self.client: ZebfitClient | None = None
        self.conn_addr: str | None = None
        self._conn_busy = False
        self._devs: dict[str, tuple[str, int]] = {}
        self._scanner: bleak.BleakScanner | None = None
        self._stop_scan = False
        self._scanning = False
        self._scan_q: asyncio.Queue = asyncio.Queue()
        self._rx_count = 0
        self._history: list[str] = []
        self._hist_idx: int | None = None
        self._sugg: list[tuple[str, str]] = []
        self._sel = 0
        self._busy = False
        self._input: CmdInput | None = None
        self._cursor_addr: str | None = None
        self._last_rows: list | None = None
        # multi-packet response reassembly
        self._pending: bytes | None = None
        self._pending_op: int | None = None
        self._pending_have = 0
        self._pending_want = 1

    def compose(self) -> ComposeResult:
        yield Static("ZEBFIT650 // scan - connect - control", id="hdr")
        with Container(id="cols"):
            with Vertical(id="left"):
                yield Static("scanning ...", id="conn-status")
                yield DataTable(id="devices", cursor_type="row",
                                zebra_stripes=False)
                yield RichLog(id="syslog", highlight=False, wrap=False,
                              max_lines=100)
                yield Static("", id="sugg")
                yield CmdInput(placeholder="command ...  (help / q)", id="cmd")
            with Vertical(id="right"):
                with Vertical(id="raw-box"):
                    yield Static("RAW  sent / received", id="raw-hdr")
                    yield RichLog(id="rawlog", highlight=False, wrap=False,
                                  max_lines=500)
                with Vertical(id="dec-box"):
                    yield Static("DECODED", id="dec-hdr")
                    yield Static("HR -- bpm   BP --/--", id="live")
                    yield RichLog(id="declog", highlight=False, wrap=False,
                                  max_lines=500)
        yield Static(
            "up/down devices   enter connect/disconnect   i type command   "
            "esc back to devices   r rescan   ctrl+q quit",
            id="statbar",
        )

    def on_mount(self):
        self._input = self.query_one("#cmd", CmdInput)
        self._rawlog = self.query_one("#rawlog", RichLog)
        self._declog = self.query_one("#declog", RichLog)
        self._syslog = self.query_one("#syslog", RichLog)
        self._live = self.query_one("#live", Static)
        self._tbl = self.query_one(DataTable)
        self._tbl.add_columns("ADDRESS", "NAME", "RSSI")
        self._tbl.focus()
        self.run_worker(self._scan_worker(), exclusive=True, group="scan",
                        exit_on_error=False)
        self.run_worker(self._rx_pump(), exclusive=True, group="ble-rx",
                        exit_on_error=False)

    # ------------------------------------------------------------ scanning

    async def _scan_worker(self):
        while True:
            if self._stop_scan:
                await asyncio.sleep(0.2)
                continue
            self._scanning = True
            status = self.query_one("#conn-status", Static)
            status.remove_class("error")
            status.remove_class("done")

            def cb(device, adv):
                self._scan_q.put_nowait(
                    (device.address, device.name or "(no name)", adv.rssi))

            self._scanner = bleak.BleakScanner(detection_callback=cb)
            await self._scanner.start()
            try:
                while not self._stop_scan:
                    try:
                        addr, name, rssi = await asyncio.wait_for(
                            self._scan_q.get(), 0.5)
                    except asyncio.TimeoutError:
                        continue
                    old = self._devs.get(addr)
                    if old is None or rssi > old[1]:
                        self._devs[addr] = (name, rssi)
                        self._rebuild()
            finally:
                try:
                    await self._scanner.stop()
                except Exception:
                    pass
                self._scanner = None
                self._scanning = False
            await asyncio.sleep(0.5)

    def _rebuild(self):
        """Rebuild table only when content changed; keep cursor on the
        same device so enter always has a selected row."""
        rows = sorted(self._devs.items(), key=lambda kv: -kv[1][1])
        if rows == self._last_rows:
            return
        self._last_rows = rows
        if self._cursor_addr is None and self._tbl.cursor_row is not None \
                and self._tbl.row_count > 0:
            row = self._tbl.get_row_at(self._tbl.cursor_row)
            self._cursor_addr = row[0] if row else None
        self._tbl.clear()
        for idx, (addr, (name, rssi)) in enumerate(rows):
            self._tbl.add_row(addr, name, str(rssi), key=addr)
            if addr == self._cursor_addr:
                self._tbl.move_cursor(row=idx)
        n = len(self._devs)
        status = self.query_one("#conn-status", Static)
        if self.conn_addr:
            status.update(
                f"CONNECTED {self.conn_addr}  (scan: {n} device"
                f"{'s' if n != 1 else ''} found)")
        else:
            status.update(
                f"scanning ... {n} device{'s' if n != 1 else ''} found")

    def action_rescan(self):
        self._devs.clear()
        self._last_rows = None
        self._cursor_addr = None
        self._rebuild()

    # --------------------------------------------- focus flow (left side)

    def action_focus_table(self):
        self._tbl.focus()

    def action_focus_input(self):
        self._input.focus()

    # ----------------------------------------------------------- connecting

    def action_select(self):
        """Screen-level fallback for enter (fires when table not focused)."""
        if self._conn_busy or self._input.has_focus:
            return
        if self._tbl.cursor_row is None:
            return
        row = self._tbl.get_row_at(self._tbl.cursor_row)
        if row:
            self._select(row[0])

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        self._select(event.row_key.value)

    def _select(self, addr: str):
        if self._conn_busy:
            return
        self._conn_busy = True
        self._set_ui_busy(True)
        status = self.query_one("#conn-status", Static)
        status.remove_class("done")
        status.remove_class("error")
        status.add_class("busy")
        if addr == self.conn_addr:
            status.update(f"disconnecting {addr} ...")
            self.run_worker(self._disconnect(), group="conn",
                            exit_on_error=False)
        else:
            status.update(f"connecting {addr} ...")
            self.run_worker(self._connect(addr), group="conn",
                            exit_on_error=False)

    def _set_ui_busy(self, busy: bool):
        self._tbl.disabled = busy
        self._input.disabled = busy

    async def _disconnect(self):
        try:
            if self.client:
                await self.client.__aexit__(None, None, None)
                self.client = None
            self.conn_addr = None
            self._tbl.remove_class("shrunk")
            self.query_one("#syslog", RichLog).remove_class("grow")
            self._log_sys("disconnected")
        finally:
            self._conn_busy = False
            self._set_ui_busy(False)
            status = self.query_one("#conn-status", Static)
            status.remove_class("busy")
            status.remove_class("done")
            status.remove_class("error")
        self._rebuild()
        self._tbl.focus()

    async def _connect(self, addr: str):
        status = self.query_one("#conn-status", Static)
        status.update(f"connecting {addr} ...")
        if self.client:
            await self.client.__aexit__(None, None, None)
            self.client = None
            self.conn_addr = None
        self._stop_scan = True
        await asyncio.sleep(0.7)
        client = ZebfitClient(addr, timeout=10.0)
        try:
            await client.__aenter__()
        except Exception as e:
            self._stop_scan = False
            status.update(f"CONNECT FAILED: {e}")
            status.remove_class("busy")
            status.add_class("error")
            self.notify(f"connect failed: {e}", severity="error", timeout=6)
            self._log_sys(f"connect failed: {e}", err=True)
            self._conn_busy = False
            self._set_ui_busy(False)
            self._tbl.focus()
            return
        self.client = client
        self.conn_addr = addr
        name = self._devs.get(addr, ("(no name)", 0))[0]
        self._tbl.add_class("shrunk")
        self.query_one("#syslog", RichLog).add_class("grow")
        self._log_sys(f"connected to {addr} ({name})")
        self._rebuild()
        self._conn_busy = False
        self._set_ui_busy(False)
        status.remove_class("busy")
        self._input.focus()
        self.run_worker(self._setup(), exclusive=True, group="ble-setup",
                        exit_on_error=False)

    async def _setup(self):
        try:
            for svc in self.client.client.services:
                self._log_sys(f"service {svc.uuid}")
                for ch in svc.characteristics:
                    self._log_sys(f"  {ch.uuid}  props={','.join(ch.properties)}")
            await self.client.start_notify()
            self._log_sys(f"notifications on {P.UUID_RX}")
            await asyncio.sleep(0.5)
            self._log_sys("ready - try: sync  gettime  info  heart")
        except Exception as e:
            self._log_sys(f"SETUP ERROR: {e}", err=True)
            self.notify(f"setup error: {e}", severity="error", timeout=6)

    # -------------------------------------------------------------- rx pump

    async def _rx_pump(self):
        while True:
            if self.client is None:
                await asyncio.sleep(0.2)
                continue
            try:
                frame = await asyncio.wait_for(self.client._frames.get(), 0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return
            self._on_frame(frame)

    def _flush_pending(self):
        if self._pending:
            self._decode_line(self._pending, self._pending_have)
        self._pending = None
        self._pending_op = None
        self._pending_have = 0
        self._pending_want = 1

    def _on_frame(self, frame: bytes):
        if not frame:
            return
        self._rx_count += 1
        now = datetime.now().strftime("%H:%M:%S")
        self._log_raw("<<<", P.hexdump(frame), now)

        op = frame[0]
        want = 1
        if len(frame) > 2:
            if op == 0xB1:
                ln = int.from_bytes(frame[1:3], "big")
                want = (ln + 4 + P.MAX_CMD_LEN - 1) // P.MAX_CMD_LEN
            else:
                ln = frame[1]
                want = (ln + 3 + P.MAX_CMD_LEN - 1) // P.MAX_CMD_LEN
        if self._pending and op != self._pending_op:
            self._flush_pending()
        if self._pending is None:
            self._pending = frame
            self._pending_op = op
            self._pending_have = 1
            self._pending_want = want
        else:
            self._pending += frame
            self._pending_have += 1
        if self._pending_have >= self._pending_want:
            self._flush_pending()

    def _decode_line(self, data: bytes, n_pkts: int):
        now = datetime.now().strftime("%H:%M:%S")
        dec = P.describe_resp(data)
        if data[0] == 0x99 and len(data) >= 8:
            stype = int.from_bytes(data[2:6], "big")
            if stype == 0x80000000:
                bpm = int.from_bytes(data[6:8], "big")
                self._live.update(f"HR {bpm} bpm   BP --/--")
            elif stype == 0x40000000:
                self._live.update(
                    f"HR -- bpm   BP {data[6]}/{data[7]} mmHg")
        suffix = f"  ({n_pkts} packets)" if n_pkts > 1 else ""
        if data[0] == 0xB1 and len(data) > 6:
            recs = P.decode_offline(data)
            self._declog.write(
                f"[{DIM}]{now}[/]  << {dec}{suffix}  ({len(recs)} records)")
            for typ, ts, v1, v2 in recs:
                t = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
                if typ == 1:
                    self._declog.write(f"[{DIM}]   {t}[/]  heart {v2} bpm")
                elif typ == 2:
                    self._declog.write(
                        f"[{DIM}]   {t}[/]  bp {v1}/{v2} mmHg")
            return
        self._declog.write(f"[{DIM}]{now}[/]  << {dec}{suffix}")

    # ------------------------------------------------------------- logging

    def _log_raw(self, direction: str, hexstr: str, now: str | None = None):
        now = now or datetime.now().strftime("%H:%M:%S")
        if direction == ">>>":
            self._rawlog.write(f"[{DIM}]{now}[/]  [cyan]>>>[/] {hexstr}")
        else:
            self._rawlog.write(f"[{DIM}]{now}[/]  [green]<<<[/] {hexstr}")

    def _log_tx(self, fr: bytes):
        self._log_raw(">>>", P.hexdump(fr))
        now = datetime.now().strftime("%H:%M:%S")
        self._declog.write(f"[{DIM}]{now}[/]  >> {P.describe_cmd(fr)}")

    def _log_sys(self, msg: str, err: bool = False):
        color = RED if err else DIM
        self._syslog.write(f"[{color}]{msg}[/]")

    # ------------------------------------------- suggestions / history

    def _render_sugg(self):
        box = self.query_one("#sugg", Static)
        if not self._sugg:
            box.update("")
            box.remove_class("show")
            return
        lines = []
        for i, (name, tpl) in enumerate(self._sugg[:8]):
            mark = ">" if i == self._sel else " "
            lines.append(f"{mark} {name:<8s} {tpl[:50]}")
        box.update("\n".join(lines))
        box.add_class("show")

    def on_input_changed(self, event: Input.Changed):
        parts = event.value.strip().split()
        if not parts:
            self._sugg = []
            self._render_sugg()
            return
        word = parts[0].lower()
        self._sugg = [(n, t) for n, (t, _h) in COMMANDS.items()
                      if n.startswith(word) or word in n]
        self._sel = 0
        self._render_sugg()

    def action_up(self):
        if self._sugg:
            self._sel = (self._sel - 1) % len(self._sugg)
            self._render_sugg()
        else:
            if self._hist_idx is None:
                self._hist_idx = len(self._history) - 1
            else:
                self._hist_idx = max(0, self._hist_idx - 1)
            if self._hist_idx >= 0 and self._history:
                self._input.value = self._history[self._hist_idx]
                self._input.cursor_position = len(self._input.value)

    def action_down(self):
        if self._sugg:
            self._sel = (self._sel + 1) % len(self._sugg)
            self._render_sugg()
        else:
            if self._hist_idx is None or self._hist_idx >= len(self._history) - 1:
                self._hist_idx = None
                self._input.value = ""
            else:
                self._hist_idx += 1
                self._input.value = self._history[self._hist_idx]
                self._input.cursor_position = len(self._input.value)

    def action_complete(self):
        if not self._sugg:
            return
        name, tpl = self._sugg[self._sel]
        self._input.value = name + (" " + tpl if tpl else "")
        self._input.cursor_position = len(self._input.value)

    def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        value = event.value.strip()
        if not value and self._sugg:
            value = self._sugg[self._sel][0]
        if value:
            self._input.value = ""
            self._sugg = []
            self._render_sugg()
            self.run_worker(self._run_command(value), group="cmd",
                            exit_on_error=False)

    # ------------------------------------------------------- commands

    async def _run_command(self, line: str):
        if self._busy:
            self._log_sys("busy - wait for previous command", err=True)
            return
        parts = line.split()
        name = parts[0].lower()
        extra = parts[1:]
        self._busy = True
        try:
            if name in ("help", "?"):
                self._show_help()
                return
            if name == "ota":
                await self._confirm_ota()
                return
            if name == "clear":
                await self._confirm_clear()
                return
            if not self.client:
                self._log_sys("not connected - enter selects a device first",
                              err=True)
                return
            frames = self._build_frames(name, extra)
        except Exception as e:
            self._log_sys(f"bad command: {e}", err=True)
            return
        finally:
            self._busy = False
        if not frames:
            return
        self._history.append(line)
        self._hist_idx = None
        if isinstance(frames, bytes):
            frames = [frames]
        before = self._rx_count
        for item in frames:
            if isinstance(item, tuple):
                delay, fr = item
            else:
                delay, fr = 0.3, item
            self._log_tx(fr)
            try:
                await self.client.write(fr)
            except Exception as e:
                self._log_sys(f"write error: {e}", err=True)
                return
            await asyncio.sleep(delay)
        deadline = asyncio.get_event_loop().time() + 2.5
        while asyncio.get_event_loop().time() < deadline:
            if self._rx_count > before:
                break
            await asyncio.sleep(0.05)
        else:
            self._log_sys("(no reply within 2.5s)")

    def _show_help(self):
        self._log_sys("-- commands --")
        for name, (tpl, desc) in sorted(COMMANDS.items()):
            self._log_sys(f"  {name:<8s} {tpl:<24s} {desc}")
        self._log_sys("-- notify apps --")
        self._log_sys("  " + "  ".join(APP_LIST))

    async def _confirm_ota(self):
        def on_result(ok: bool | None):
            if ok:
                self.run_worker(self._send_only(P.enter_ota()), group="cmd",
                                exit_on_error=False)
        await self.app.push_screen(
            ConfirmScreen(
                "OTA / DFU MODE",
                "FC 00 00 kicks the watch into DFU mode.\n"
                "It leaves normal mode and will need recovery.\n\n"
                "Really send it?",
                danger=True,
            ),
            on_result,
        )

    async def _confirm_clear(self):
        def on_result(ok: bool | None):
            if ok:
                self.run_worker(self._run_command("clear"), group="cmd",
                                exit_on_error=False)
        await self.app.push_screen(
            ConfirmScreen(
                "CLEAR WATCH DATA",
                "88 00 00 erases ALL steps / history / records\n"
                "on the watch. This cannot be undone.\n\n"
                "Really erase everything?",
                danger=True,
            ),
            on_result,
        )

    async def _send_only(self, frame: bytes):
        self._log_tx(frame)
        await self.client.write(frame)

    def _build_frames(self, name: str, extra: list[str]) -> list[bytes] | bytes | None:
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
        if name == "heart":
            on = True
            if extra and extra[0] in ("off", "0", "stop"):
                on = False
            return P.heart_measure(on)
        if name == "bp":
            on = True
            if extra and extra[0] in ("off", "0", "stop"):
                on = False
            return P.bp_measure(on)
        if name == "notify":
            idx = int(extra[0], 0)
            if idx not in P.APP_NAMES:
                raise ValueError(f"unknown app {idx} - see help")
            flag = P.ANCS_FLAGS.get(idx, 0)
            title = extra[1] if len(extra) > 1 else P.APP_NAMES[idx]
            content = extra[2] if len(extra) > 2 else "notification"
            return P.ancs_frames(idx, flag, title, content)
        if name == "vibmode":
            mode = int(extra[0], 0) if extra else 2
            if mode not in (0, 1, 2):
                raise ValueError("vibmode: 0 off, 1 on+anti-lost, 2 on")
            return P.update_device_params(vibrate=mode)
        if name == "band":
            m = int(extra[0], 0) if extra else 0x60
            return bytes([0xF4, 0x01, m, m])
        if name == "hist":
            week, hour, hours = (int(x, 0) for x in extra[:3])
            return P.sync_history(week, hour, hours)
        if name == "offline":
            ts = int(extra[0], 0) if extra else int(datetime.now().timestamp())
            return P.request_offline(ts)
        if name == "running":
            yy, mo, day = (int(x, 0) for x in extra[:3])
            return P.get_running(yy, mo, day)
        if name == "clear":
            return P.clear_data()
        if name == "callack":
            return P.ack_call()
        if name == "ota":
            return P.enter_ota()
        if name == "raw":
            return bytes.fromhex("".join(extra))
        raise ValueError(f"unknown command: {name} - try help")


# -------------------------------------------------------------------- app


class ZebTUI(App):
    CSS = APP_CSS
    TITLE = "zebfit650"

    def on_mount(self):
        self.push_screen(ZebScreen())

    async def action_quit(self):
        screen = self.screen
        if hasattr(screen, "client") and screen.client is not None:
            try:
                await screen.client.__aexit__(None, None, None)
            except Exception:
                pass
        self.exit()


def main():
    ZebTUI().run()


if __name__ == "__main__":
    main()