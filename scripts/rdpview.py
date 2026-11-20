"""rdpview.py - RDP session preview with take-over, driven by the RDP protocol.

The client runs headless inside this script, so the macOS cursor and keyboard focus
are never used: the operator keeps working in any other window while the agent drives
the remote machine, and watches it here.

Clicking inside the window takes over: input becomes the operator's and the agent
sends nothing until F5. While the take-over is active the agent can still be
instructed from the chat through a command file (RDP_CMD_FILE), one command per line:

    TYPE <text>          type text into the focused window
    RUN <program> [args] open a program through the Run dialog
    ENTER                press Enter
    KEY <name|hex> [N]   press a named key N times, a raw scancode, or a chord such
                         as ctrl+a, ctrl+shift+esc, alt+f4
    SCROLL up|down [N] [line]  scroll N screens, or N lines with the arrows
    CLICK x y [right] [double] press and release the button at a point
    DRAG x1 y1 x2 y2 [steps] press, move and release between two points
    SHOT [path] [x y w h [zoom]] save the frame, or a cropped and zoomed part of it
    PAUSE / RESUME       stop or resume the agent's own work
"""
import argparse
import asyncio
import os
import tempfile
import time
import getpass
import hashlib
import json
import queue
import sys
import threading
import tkinter as tk

from aardwolf.connection import RDPConnection
from aardwolf.commons.target import RDPTarget
from aardwolf.commons.iosettings import RDPIOSettings
from asyauth.common.credentials import UniCredential
from asyauth.common.constants import asyauthSecret, asyauthProtocol
from PIL import Image, ImageTk

# There is no scroll-wheel support here on purpose. aardwolf does not advertise
# MOUSE_HAS_WHEEL, a patched info packet did not help either, and a wheel event made
# the server reset the connection twice. Scrolling goes through PageDown and PageUp.

DEFAULT_CMD_FILE = os.path.join(tempfile.gettempdir(), "rdp_agent_cmd.txt")
LOG_FILE = os.path.join(tempfile.gettempdir(), "rdp_agent.log")
STATUS_FILE = os.path.join(tempfile.gettempdir(), "rdp_agent_status.json")


def _mean(small):
    # histogram avoids the deprecated getdata() and is faster on flat images
    hist = small.convert("L").histogram()
    total = sum(hist)
    return sum(i * n for i, n in enumerate(hist)) / float(total or 1)


def screen_verdict(img):
    """locked, desktop or unknown.

    A lock screen is one flat colour. A desktop has a taskbar: a horizontal strip
    at the bottom that is clearly brighter or darker than the area above it. That
    works on dark wallpapers too, where a plain brightness test fails.
    """
    try:
        w, h = img.size
        band = img.crop((0, int(h * 0.93), w, h))
        above = img.crop((0, int(h * 0.80), w, int(h * 0.90)))
        diff = abs(_mean(band) - _mean(above))
        colors = len(img.convert("RGB").resize((120, 90)).getcolors(20000) or [])
    except Exception:
        return "unknown"
    if diff > 12 and colors > 200:
        return "desktop"
    if colors <= 80:
        return "locked"
    return "desktop" if colors >= 800 else "unknown"


def frame_hash(img):
    """A cheap fingerprint of the screen.

    The agent needs to know whether a scroll or a drag changed anything, and it has
    no view of the session on macOS. A downscaled grayscale dump answers that
    without saving a megabyte and without asking the operator to look.
    """
    try:
        return hashlib.sha1(img.convert("L").resize((64, 48)).tobytes()).hexdigest()[:12]
    except Exception:
        return None


def log(msg):
    """Everything goes to the terminal and to a file the agent can read later."""
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass

SC_CTRL, SC_ALT, SC_DEL = 0x1D, 0x38, 0x53

# Named keys, so the agent never has to remember that an arrow is 0x50 and that it
# must go extended. The second field is the extended flag.
KEY_NAMES = {
    "enter": (0x1C, False), "return": (0x1C, False),
    "esc": (0x01, False), "escape": (0x01, False),
    "tab": (0x0F, False), "space": (0x39, False),
    "backspace": (0x0E, False),
    "delete": (0x53, True), "del": (0x53, True),
    "insert": (0x52, True), "ins": (0x52, True),
    "home": (0x47, True), "end": (0x4F, True),
    "up": (0x48, True), "down": (0x50, True),
    "left": (0x4B, True), "right": (0x4D, True),
    "pgup": (0x49, True), "pageup": (0x49, True),
    "pgdn": (0x51, True), "pagedown": (0x51, True),
}

# Modifiers for chords such as ctrl+a or alt+F4. Win has to go extended.
MODIFIERS = {
    "ctrl": (0x1D, False), "control": (0x1D, False),
    "shift": (0x2A, False), "alt": (0x38, False),
    "win": (0x5B, True), "super": (0x5B, True), "cmd": (0x5B, True),
}

# Scancodes for the base key of a chord. Unicode input ignores modifier state, so a
# chord has to press a real scancode, and the scancode depends on the physical key.
KEYCODES = {
    "a": 0x1E, "b": 0x30, "c": 0x2E, "d": 0x20, "e": 0x12, "f": 0x21, "g": 0x22,
    "h": 0x23, "i": 0x17, "j": 0x24, "k": 0x25, "l": 0x26, "m": 0x32, "n": 0x31,
    "o": 0x18, "p": 0x19, "q": 0x10, "r": 0x13, "s": 0x1F, "t": 0x14, "u": 0x16,
    "v": 0x2F, "w": 0x11, "x": 0x2D, "y": 0x15, "z": 0x2C,
    "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06,
    "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B,
    "f1": 0x3B, "f2": 0x3C, "f3": 0x3D, "f4": 0x3E, "f5": 0x3F, "f6": 0x40,
    "f7": 0x41, "f8": 0x42, "f9": 0x43, "f10": 0x44, "f11": 0x57, "f12": 0x58,
    "-": 0x0C, "=": 0x0D, "[": 0x1A, "]": 0x1B, ";": 0x27, "'": 0x28,
    "`": 0x29, "\\": 0x2B, ",": 0x33, ".": 0x34, "/": 0x35,
}

# There is no wheel constant on purpose: a wheel event resets the RDP connection on
# this host (seen twice, both times on SCROLL), so scrolling is done with PageDown
# and PageUp. Do not reintroduce the wheel without a host where it is proven safe.


class Session:
    """The RDP connection, running in its own asyncio thread."""

    def __init__(self, host, port, cred, on_frame, on_state, demo=False, marathon=0):
        self.demo = demo
        self.marathon = marathon
        self.host = host
        self.started = time.strftime("%Y-%m-%d %H:%M:%S")
        self.frames_total = 0
        self.screen_state = "unknown"
        self.cmd_path = os.environ.get("RDP_CMD_FILE") or DEFAULT_CMD_FILE
        self.keepalive_on = True
        self.last_size = None
        self.last_frame = None
        self.last_hash = None
        self.alive = True
        self.dead_reason = None
        self.last_status = 0
        self.paused = False
        self.marathon_task = None
        self.queue = queue.Queue()
        self.on_state = on_state
        self.loop = asyncio.new_event_loop()
        self.conn = None
        self.proc = threading.Thread(
            target=self.loop.run_until_complete,
            args=(self.run(host, port, cred),), daemon=True)
        self.proc.start()

    async def run(self, host, port, cred):
        target = RDPTarget(ip=host, port=port, unsafe_ssl=True, autologon=True)
        self.conn = RDPConnection(target, cred, RDPIOSettings())
        try:
            self.on_state("подключение ...")
            await self.conn.connect()
        except Exception as exc:
            self.on_state("ошибка подключения: %s" % exc)
            return
        self.on_state("соединение установлено")

        # the MCS channel is joined asynchronously; input sent before that raises
        # KeyError and the keystrokes land nowhere
        ready = False
        for _ in range(40):
            await asyncio.sleep(0.5)
            try:
                if self.conn.get_desktop_buffer() is not None:
                    ready = True
                    break
            except Exception:
                pass
        if not ready:
            log("сессия не ответила за 20 секунд: кадры не приходят")
            log("вероятно, сервер ещё держит предыдущее подключение.")
            log("закройте окно и подождите 10 минут перед следующей попыткой")
            self.on_state("сессия не ответила: подождите 10 минут")
            try:
                await self.conn.terminate()
            except Exception:
                pass
            return
        log("сессия готова")

        try:
            await self.conn.send_focus_in()
            log("focus_in отправлен")
        except Exception as exc:
            log("focus_in: %s" % exc)
        await asyncio.sleep(1.5)
        try:
            for sc, down, ext in ((SC_CTRL, True, False), (SC_ALT, True, False),
                                  (SC_DEL, True, True), (SC_DEL, False, True),
                                  (SC_ALT, False, False), (SC_CTRL, False, False)):
                await self.conn.send_key_scancode(sc, down, ext)
                await asyncio.sleep(0.05)
        except Exception as exc:
            self.on_state("Ctrl+Alt+Del не ушёл: %s" % exc)

        if self.keepalive_on and os.environ.get("RDP_KEEPALIVE", "1") != "0":
            asyncio.ensure_future(self.keepalive())

        if self.demo:
            await self.show_demo()
        if self.marathon:
            self.marathon_task = asyncio.ensure_future(self.run_marathon())

        while True:
            if not self.alive:
                await asyncio.sleep(0.5)
                continue
            cmds = self.poll_commands()
            if cmds:
                for cmd in (cmds if isinstance(cmds, list) else [cmds]):
                    try:
                        await self.exec_command(cmd)
                    except Exception as exc:
                        log("cmd error: %r" % exc)
            await asyncio.sleep(0.25)
            try:
                img = self.conn.get_desktop_buffer()
            except Exception as exc:
                self.mark_dead("кадр не получен: %r" % exc)
                continue
            if img is not None and hasattr(img, "width") and img.width > 0:
                self.queue.put(img)
                self.last_size = (img.width, img.height)
                self.last_frame = img
                # count real updates, not loop iterations: a frozen connection
                # used to look alive because the counter grew anyway
                current = frame_hash(img)
                if current != self.last_hash:
                    self.last_hash = current
                    self.frames_total += 1
                    if self.frames_total <= 3 or self.frames_total % 10 == 0:
                        self.screen_state = screen_verdict(img)
                now = time.time()
                if now - self.last_status >= 5:
                    self.last_status = now
                    self.write_status()

    async def sc(self, code, down, ext=False):
        for attempt in range(20):
            try:
                await self.conn.send_key_scancode(code, down, ext)
                break
            except KeyError:
                await asyncio.sleep(0.3)
            except Exception as exc:
                self.mark_dead("клавиатура: %r" % exc)
                return
        await asyncio.sleep(0.04)

    async def tap(self, code, ext=False):
        await self.sc(code, True, ext)
        await self.sc(code, False, ext)

    async def chord(self, mods, code, ext=False):
        """Hold the modifiers, tap the base key, release in the reverse order."""
        for mod_code, mod_ext in mods:
            await self.sc(mod_code, True, mod_ext)
            await asyncio.sleep(0.03)
        await self.tap(code, ext)
        await asyncio.sleep(0.03)
        for mod_code, mod_ext in reversed(mods):
            await self.sc(mod_code, False, mod_ext)
            await asyncio.sleep(0.03)

    @staticmethod
    def parse_chord(name):
        """ctrl+shift+esc -> ([(0x1D, False), (0x2A, False)], 0x01, False).

        Returns (None, None, None) when the combination cannot be parsed.
        """
        names = [part.strip() for part in name.split("+")]
        mods = []
        for mod in names[:-1]:
            if mod in ("", "ctrl", "control"):
                if mod:
                    mods.append(MODIFIERS["ctrl"])
                continue
            if mod not in MODIFIERS:
                return None, None, None
            mods.append(MODIFIERS[mod])
        base = names[-1]
        if not mods:
            return None, None, None
        if base in KEY_NAMES:
            code, ext = KEY_NAMES[base]
        elif base in KEYCODES:
            code, ext = KEYCODES[base], False
        else:
            try:
                code, ext = int(base, 0), False
            except Exception:
                return None, None, None
        return mods, code, ext

    async def show_demo(self):
        """Win+R, cmd, echo Hello World! - a visible proof that input works."""
        try:
            await asyncio.sleep(1.0)
            log("demo: Win+R")
            # park the pointer in the middle first: if it sits on a desktop icon,
            # the text typed later can land on whatever that icon opens
            if self.last_size:
                from aardwolf.commons.queuedata.constants import MOUSEBUTTON
                await self.conn.send_mouse(MOUSEBUTTON.MOUSEBUTTON_HOVER,
                                           self.last_size[0] // 2,
                                           self.last_size[1] // 2, False)
                await asyncio.sleep(0.3)

            # the Run dialog needs time; typing before it opens sends the text to
            # the desktop, where Windows turns it into a search
            await self.sc(0x5B, True, True)
            await self.tap(0x13)
            await self.sc(0x5B, False, True)
            await asyncio.sleep(2.5)
            log("demo: cmd")
            for ch in "cmd":
                await self.conn.send_key_char(ch, True)
                await self.conn.send_key_char(ch, False)
                await asyncio.sleep(0.25)
            await self.tap(0x1C)
            await asyncio.sleep(2.5)
            log("demo: echo")
            for ch in "echo Hello World!":
                await self.conn.send_key_char(ch, True)
                await self.conn.send_key_char(ch, False)
                await asyncio.sleep(0.06)
            await self.tap(0x1C)
            self.on_state("демо: Hello World! отправлен")
            log("demo: sent")
        except Exception as exc:
            print("demo error: %r" % exc, flush=True)
            self.on_state("демо не удалось: %s" % exc)

    async def key_char(self, ch):
        try:
            await self.conn.send_key_char(ch, True)
            await self.conn.send_key_char(ch, False)
        except Exception as exc:
            print("key error: %r" % exc, flush=True)

    async def key_scan(self, code, ext=False):
        try:
            await self.conn.send_key_scancode(code, True, ext)
            await self.conn.send_key_scancode(code, False, ext)
        except Exception as exc:
            print("key error: %r" % exc, flush=True)

    async def type_text(self, text):
        for ch in text:
            await self.conn.send_key_char(ch, True)
            await self.conn.send_key_char(ch, False)
            await asyncio.sleep(0.05)

    async def keepalive(self):
        """A small pointer nudge, so the session does not lock while it idles."""
        from aardwolf.commons.queuedata.constants import MOUSEBUTTON
        while True:
            await asyncio.sleep(55)
            try:
                await self.conn.send_mouse(MOUSEBUTTON.MOUSEBUTTON_HOVER, 5, 5, False)
            except Exception:
                pass

    async def run_dialog(self, name):
        """Open a program straight from Run: no console, so the new window keeps
        the focus and the text typed next lands in it."""
        await self.sc(0x5B, True, True)
        await self.tap(0x13)
        await self.sc(0x5B, False, True)
        await asyncio.sleep(3.0)
        for ch in name:
            await self.conn.send_key_char(ch, True)
            await self.conn.send_key_char(ch, False)
            await asyncio.sleep(0.2)
        await asyncio.sleep(0.6)
        await self.tap(0x1C)
        await asyncio.sleep(4.0)
        log("запущено: %s" % name)

    async def exec_command(self, line):
        log("cmd: %s" % line)
        verb, _, arg = line.partition(" ")
        verb = verb.upper().strip()
        arg = arg.strip()
        if not self.alive and verb not in ("STATE", "SHOT", "STOP"):
            log("команда не отправлена: соединение потеряно (%s)" % self.dead_reason)
            return
        if verb == "TYPE":
            await self.text_out(arg) if hasattr(self, "text_out") else await self.type_text(arg)
        elif verb == "RUN":
            await self.run_dialog(arg)
        elif verb == "SCROLL":
            parts = arg.split()
            direction = parts[0].lower() if parts else "down"
            if direction not in ("up", "down"):
                log("прокрутка: нужно up или down, получено %r" % direction)
                return
            words = [p.lower() for p in parts[1:]]
            line_mode = any(w in ("line", "lines", "строка", "строки", "строчку") for w in words)
            numbers = [int(p) for p in parts[1:] if p.isdigit()]
            times = numbers[0] if numbers else (3 if line_mode else 1)
            times = max(1, min(times, 50 if line_mode else 30))
            # The wheel is deliberately not used: on this host a wheel event resets
            # the connection, twice in a row, while clicks and drags go through.
            # One step is a screen with PageDown / PageUp, or a line with the arrows,
            # the same keys a human presses. Arrows move the caret in an editor, so
            # the line mode is for fine tuning, not for long documents.
            if line_mode:
                scan = 0x50 if direction == "down" else 0x48
                pause = 0.06
            else:
                scan = 0x51 if direction == "down" else 0x49
                pause = 0.15
            before = frame_hash(self.current_frame())
            for _ in range(times):
                await self.tap(scan, True)
                await asyncio.sleep(pause)
            await asyncio.sleep(0.35)
            after = frame_hash(self.current_frame())
            changed = "экран изменился" if before and after and before != after else "экран не изменился"
            log("прокрутка %s x%d (%s): %s"
                % (direction, times, "строки" if line_mode else "экраны", changed))
        elif verb == "DRAG":
            from aardwolf.commons.queuedata.constants import MOUSEBUTTON
            parts = arg.split()
            try:
                x1, y1, x2, y2 = [int(v) for v in parts[:4]]
            except Exception:
                log("drag: нужны четыре числа x1 y1 x2 y2")
                return
            steps = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 20
            steps = max(2, min(steps, 60))
            if self.last_size:
                x1 = max(0, min(x1, self.last_size[0] - 1))
                y1 = max(0, min(y1, self.last_size[1] - 1))
                x2 = max(0, min(x2, self.last_size[0] - 1))
                y2 = max(0, min(y2, self.last_size[1] - 1))
            # the release is a separate event: is_pressed False leaves the button
            # flag set without DOWN, and only that tells the server the drag ended.
            # Sending a second DOWN instead keeps the button held and the drop
            # never happens, the item stays glued to the pointer.
            before = frame_hash(self.current_frame())
            try:
                await self.mouse_ev(MOUSEBUTTON.MOUSEBUTTON_LEFT, x1, y1, True)
                for i in range(1, steps + 1):
                    await self.mouse_ev(MOUSEBUTTON.MOUSEBUTTON_HOVER,
                                        x1 + (x2 - x1) * i // steps,
                                        y1 + (y2 - y1) * i // steps, False)
                    await asyncio.sleep(0.03)
                await asyncio.sleep(0.05)
            finally:
                await self.mouse_ev(MOUSEBUTTON.MOUSEBUTTON_LEFT, x2, y2, False)
            await asyncio.sleep(0.35)
            after = frame_hash(self.current_frame())
            changed = "экран изменился" if before and after and before != after else "экран не изменился"
            log("перетаскивание %d,%d -> %d,%d (шагов %d): %s"
                % (x1, y1, x2, y2, steps, changed))
        elif verb == "CLICK":
            from aardwolf.commons.queuedata.constants import MOUSEBUTTON
            parts = arg.split()
            try:
                cx, cy = int(parts[0]), int(parts[1])
            except Exception:
                log("click: нужны два числа x y")
                return
            words = [p.lower() for p in parts[2:]]
            right = "right" in words or "правая" in words
            double = "double" in words or "двойной" in words
            if self.last_size:
                cx = max(0, min(cx, self.last_size[0] - 1))
                cy = max(0, min(cy, self.last_size[1] - 1))
            button = MOUSEBUTTON.MOUSEBUTTON_RIGHT if right else MOUSEBUTTON.MOUSEBUTTON_LEFT
            before = frame_hash(self.current_frame())
            await self.mouse_ev(MOUSEBUTTON.MOUSEBUTTON_HOVER, cx, cy, False)
            await asyncio.sleep(0.08)
            for _ in range(2 if double else 1):
                await self.mouse_ev(button, cx, cy, True)
                await asyncio.sleep(0.05)
                await self.mouse_ev(button, cx, cy, False)
                await asyncio.sleep(0.12)
            await asyncio.sleep(0.3)
            after = frame_hash(self.current_frame())
            # a click that only moves the focus changes nothing on screen, so the
            # verdict is a hint here and not a failure
            changed = "экран изменился" if before and after and before != after else "экран не изменился"
            log("клик %d,%d%s%s: %s"
                % (cx, cy, " right" if right else "", " double" if double else "", changed))
        elif verb == "STATE":
            log("экран: %s" % self.screen_state)
        elif verb == "SHOT":
            # on macOS the frame is only drawn in the operator's window, the agent
            # has no other way to see the screen, so it has to be saved to a file.
            # SHOT [path] [x y w h [zoom]] - a crop and a nearest-neighbour zoom save
            # a round trip through Pillow when the text is small.
            parts = arg.split()
            path = None
            numbers = []
            for token in parts:
                if token.lstrip("-").isdigit():
                    numbers.append(int(token))
                elif path is None:
                    path = token
            path = path or os.path.join(tempfile.gettempdir(), "rdp_agent_shot.png")
            img = self.last_frame
            if img is None:
                log("снимок: кадров ещё нет, подождите пару секунд")
                return
            if not self.alive:
                log("снимок: соединение потеряно, это последний полученный кадр, не текущий экран")
            box = None
            zoom = 1
            if len(numbers) >= 4:
                x, y, w, h = numbers[:4]
                w = max(1, min(w, img.width - x)) if x < img.width else 1
                h = max(1, min(h, img.height - y)) if y < img.height else 1
                x = max(0, min(x, img.width - 1))
                y = max(0, min(y, img.height - 1))
                box = (x, y, x + w, y + h)
                zoom = numbers[4] if len(numbers) > 4 else 1
                zoom = max(1, min(zoom, 8))
                try:
                    img = img.crop(box)
                    if zoom > 1:
                        img = img.resize((img.width * zoom, img.height * zoom), Image.NEAREST)
                except Exception as exc:
                    log("снимок: кроп не удался: %r" % exc)
                    return
            try:
                img.save(path)
                if box is None:
                    log("снимок: %s (%dx%d)" % (path, img.width, img.height))
                else:
                    log("снимок: %s (%dx%d, кроп %d,%d %dx%d, зум %d)"
                        % (path, img.width, img.height, box[0], box[1], box[2] - box[0],
                           box[3] - box[1], zoom))
            except Exception as exc:
                log("снимок не сохранён: %r" % exc)
        elif verb == "STOP":
            log("команда STOP: закрываю сессию")
            try:
                await self.conn.send_disconnect()
            except Exception:
                pass
            await asyncio.sleep(1.0)
            await self.conn.terminate()
            self.write_status({"stopped": True})
            log("сессия закрыта")
            os._exit(0)
        elif verb == "PASTE":
            try:
                await self.conn.set_current_clipboard_text(arg)
                await asyncio.sleep(0.3)
                await self.sc(0x1D, True)          # Ctrl down
                await self.tap(0x2F)               # V
                await self.sc(0x1D, False)         # Ctrl up
                log("вставлено из буфера: %d символов" % len(arg))
            except Exception as exc:
                log("paste error: %r" % exc)
        elif verb == "PAUSE":
            self.set_paused(True)
        elif verb == "RESUME":
            self.set_paused(False)
        elif verb == "ENTER":
            await self.tap(0x1C)
        elif verb == "KEY":
            parts = arg.split()
            if not parts:
                log("key: нужно имя клавиши, сочетание (ctrl+a) или код в hex")
                return
            name = parts[0].lower()
            count = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
            count = max(1, min(count, 50))
            if "+" in name:
                mods, code, ext = self.parse_chord(name)
                if mods is None:
                    log("key: не разобрал сочетание %r, нужно вида ctrl+a или alt+f4" % name)
                    return
                for _ in range(count):
                    await self.chord(mods, code, ext)
                    await asyncio.sleep(0.08)
                log("сочетание %s x%d" % (name, count))
                return
            if name in KEY_NAMES:
                code, ext = KEY_NAMES[name]
            elif name in KEYCODES:
                code, ext = KEYCODES[name], False
            else:
                try:
                    code = int(name, 0)
                except Exception:
                    log("key: неизвестная клавиша %r" % name)
                    return
                ext = len(parts) > 1 and parts[1].lower() == "ext"
            for _ in range(count):
                await self.tap(code, ext)
                await asyncio.sleep(0.05)
            log("клавиша %s x%d" % (name, count))
        elif verb.lower() in KEY_NAMES:
            # "esc" or "down 3" typed without the KEY word: accept it, both the agent
            # and the operator spelled it that way at least once
            code, ext = KEY_NAMES[verb.lower()]
            count = int(arg) if arg.isdigit() else 1
            count = max(1, min(count, 50))
            for _ in range(count):
                await self.tap(code, ext)
                await asyncio.sleep(0.05)
            log("клавиша %s x%d" % (verb.lower(), count))
        else:
            print("cmd: неизвестная команда %r" % verb, flush=True)

    def current_frame(self):
        """The frame as it is right now, without waiting for the drawing loop."""
        try:
            img = self.conn.get_desktop_buffer()
        except Exception:
            return None
        if img is None or not getattr(img, "width", 0):
            return None
        self.last_frame = img
        self.last_size = (img.width, img.height)
        return img

    async def mouse_ev(self, button, x, y, pressed, steps=None):
        """Send a pointer event and never lose the reason it failed.

        aardwolf swallows exceptions inside send_mouse and returns (None, err), so a
        bare await looks successful while nothing reaches Windows. That silence is
        what made the first mouse bugs invisible.
        """
        try:
            if steps is None:
                ok, err = await self.conn.send_mouse(button, int(x), int(y), pressed)
            else:
                ok, err = await self.conn.send_mouse(button, int(x), int(y), pressed, steps=steps)
        except Exception as exc:
            log("мышь: исключение %r" % exc)
            if isinstance(exc, OSError):
                self.mark_dead("мышь: %r" % exc)
            return False
        if err is not None or ok is None:
            log("мышь: событие не отправлено (%r)" % (err,))
            if isinstance(err, OSError):
                self.mark_dead("мышь: %r" % (err,))
            return False
        return True

    def mark_dead(self, reason):
        """The socket is gone, the window still shows the last frame.

        Without this the process looks healthy: the loop keeps putting the same
        buffer into the queue, the frame counter grows, and screenshots look fine
        while nothing has reached Windows for minutes.
        """
        if not self.alive:
            return
        self.alive = False
        self.dead_reason = reason
        log("СОЕДИНЕНИЕ ПОТЕРЯНО: %s" % reason)
        log("кадры ниже этой строки не обновляются, закройте окно и подключитесь заново через 10 минут")
        self.on_state("соединение потеряно")
        self.write_status({"alive": False, "reason": reason})

    def write_status(self, extra=None):
        """A small JSON file, so --status works without touching the window."""
        data = {
            "pid": os.getpid(),
            "host": self.host,
            "started": self.started,
            "frames": self.frames_total,
            "command_file": self.cmd_path,
            "paused": self.paused,
            "screen": self.screen_state,
            "size": ("%dx%d" % self.last_size) if self.last_size else None,
            "alive": self.alive,
            "reason": self.dead_reason,
        }
        data.update(extra or {})
        try:
            with open(STATUS_FILE, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except Exception as exc:
            log("статус не записан: %r" % exc)

    def poll_commands(self):
        path = os.environ.get("RDP_CMD_FILE") or DEFAULT_CMD_FILE
        try:
            with open(path, encoding="utf-8") as fh:
                data = fh.read().strip()
        except FileNotFoundError:
            return None
        except Exception:
            return None
        if not data:
            return None
        lines = [ln.strip() for ln in data.splitlines() if ln.strip()]
        try:
            open(path, "w").close()
        except Exception:
            pass
        return lines[0] if len(lines) == 1 else lines

    async def run_marathon(self):
        """Type 1, 2, 3 ... into Notepad until the operator takes over."""
        try:
            await asyncio.sleep(1.0)
            log("marathon: Win+R notepad")
            await self.sc(0x5B, True, True)
            await self.tap(0x13)
            await self.sc(0x5B, False, True)
            await asyncio.sleep(1.2)
            await self.type_text("notepad")
            await self.tap(0x1C)
            await asyncio.sleep(3.0)
            n = 1
            remaining = float(self.marathon)
            while True:
                if self.paused:
                    # paused time is the operator's time, it does not count
                    await asyncio.sleep(0.2)
                    continue
                if remaining <= 0:
                    break
                await self.type_text("%d " % n)
                log("marathon: %d" % n)
                n += 1
                remaining -= 1.5
                await asyncio.sleep(1.5)
            log("marathon: finished")
            self.on_state("марафон завершён")
        except Exception as exc:
            print("marathon error: %r" % exc, flush=True)

    def toggle_pause(self, value=True):
        return self.set_paused(value)

    def set_paused(self, value):
        self.paused = value
        # the operator can take over or hand back at any moment, and --status has to
        # show it at once, not on the next frame: a still desktop produces no frames
        self.write_status()
        return self.paused

    async def mouse(self, x, y, pressed):
        if not self.conn:
            return
        from aardwolf.commons.queuedata.constants import MOUSEBUTTON
        ok = await self.mouse_ev(MOUSEBUTTON.MOUSEBUTTON_LEFT, x, y, pressed)
        if not ok:
            self.on_state("мышь: событие не отправлено, причина в логе")

    def send_char(self, ch):
        asyncio.run_coroutine_threadsafe(self.key_char(ch), self.loop)

    def send_scan(self, code, ext=False):
        asyncio.run_coroutine_threadsafe(self.key_scan(code, ext), self.loop)

    def click(self, x, y):
        asyncio.run_coroutine_threadsafe(self.mouse(x, y, True), self.loop)
        asyncio.run_coroutine_threadsafe(self.mouse(x, y, False), self.loop)


def relaunch_in_terminal():
    """getpass needs a real TTY. Without one, reopen ourselves in Terminal."""
    import tempfile, subprocess
    script = os.path.abspath(__file__)
    args = " ".join('"%s"' % a for a in sys.argv[1:])
    fd, path = tempfile.mkstemp(suffix=".command", prefix="rdpview_")
    with os.fdopen(fd, "w") as fh:
        fh.write("#!/bin/zsh\ncd \"%s\"\nexec python3 \"%s\" %s\n"
                 % (os.path.dirname(script), script, args))
    os.chmod(path, 0o755)
    subprocess.call(["open", "-a", "Terminal", path])
    print("Открыл Terminal: введите логин и пароль в новом окне.")
    return 0


def print_status():
    try:
        with open(STATUS_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:
        print("файл состояния не записан, показываю последние строки лога:")
        try:
            with open(LOG_FILE, encoding="utf-8") as fh:
                for line in fh.readlines()[-10:]:
                    print("  " + line.rstrip())
        except Exception:
            print("  лога тоже нет: процесс не запускался")
        return 1
    try:
        os.kill(int(d.get("pid")), 0)
        alive = True
    except Exception:
        alive = False
    print("хост:      %s" % d.get("host"))
    print("процесс:   %s (%s)" % (d.get("pid"), "жив" if alive else "не найден"))
    print("запуск:    %s" % d.get("started"))
    print("кадров:    %s" % d.get("frames"))
    print("экран:     %s" % d.get("screen"))
    print("размер:    %s" % d.get("size"))
    if d.get("alive") is False:
        print("связь:     потеряна (%s)" % d.get("reason"))
    else:
        print("связь:     жива")
    print("перехват:  %s" % ("у оператора" if d.get("paused") else "у агента"))
    print("команды:   %s" % d.get("command_file"))
    print("лог:       %s" % LOG_FILE)
    if not alive:
        print()
        print("сессия не активна: процесс завершён, можно подключаться заново")
        return 1
    if d.get("alive") is False:
        print()
        print("транспорт потерян: кадры заморожены, ввод не доходит.")
        print("закройте окно и подключитесь заново не раньше чем через 10 минут")
        return 1
    return 0


def main():
    if "--status" in sys.argv:
        return print_status()
    if not sys.stdin.isatty() and "--no-tty-check" not in sys.argv:
        return relaunch_in_terminal()
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=3389)
    ap.add_argument("--user", default="")
    ap.add_argument("--demo", dest="selftest", action="store_true",
                    help="write Hello World! in cmd after connect, a visible proof of input")
    ap.add_argument("--selftest", dest="selftest", action="store_true",
                    help="same as --demo")
    ap.add_argument("--status", action="store_true", help="print the state of a live session")
    ap.add_argument("--no-keepalive", dest="keepalive", action="store_false", default=True,
                    help="do not nudge the pointer while idle")
    ap.add_argument("--marathon", type=int, default=0, help="type 1 2 3 ... for N seconds, click pauses")
    args = ap.parse_args()

    user = args.user or input("Логин (DOMAIN\\user): ").strip()
    password = getpass.getpass("Пароль: ")
    domain, _, account = user.rpartition("\\")
    cred = UniCredential(secret=password, username=account or user, domain=domain or None,
                         stype=asyauthSecret.PASSWORD, protocol=asyauthProtocol.NTLM)
    del password

    root = tk.Tk()
    root.title("RDP session (preview)")
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry("%dx%d+%d+%d" % (int(sw * 0.60), int(sh * 0.62),
                                   int(sw * 0.02), int(sh * 0.06)))
    status = tk.Label(root, text="запуск ...", anchor="w", bg="#222", fg="#ddd")
    status.pack(side="bottom", fill="x")
    label = tk.Label(root, bg="black")
    label.pack(fill="both", expand=True)

    holder = {"session": None, "photo": None, "frame_px": None}

    def draw():
        try:
            img = holder["session"].queue.get_nowait()
        except queue.Empty:
            root.after(100, draw)
            return
        w = max(1, label.winfo_width())
        h = max(1, label.winfo_height())
        # never blow the frame up beyond its own size: upscaling is what made the
        # full screen mode crawl
        scale = min(w / float(img.width), h / float(img.height))
        dw, dh = max(1, int(img.width * scale)), max(1, int(img.height * scale))
        holder["frame_px"] = (img.width, img.height)
        holder["disp"] = (dw, dh, (w - dw) // 2, (h - dh) // 2)
        if (dw, dh) == (img.width, img.height):
            shown = img
        else:
            shown = img.resize((dw, dh), Image.BILINEAR)
        holder["photo"] = ImageTk.PhotoImage(shown)
        label.configure(image=holder["photo"])
        label.image = holder["photo"]
        status.configure(text="%dx%d  |  клик внутри = перехват" % (img.width, img.height))
        root.after(60, draw)

    def on_click(event):
        """Any click inside the picture is a take-over, at any window size."""
        session = holder["session"]
        if not session or not holder["frame_px"]:
            return
        disp = holder.get("disp")
        if not disp:
            return "break"
        dw, dh, ox, oy = disp
        lx = event.x_root - label.winfo_rootx() - ox
        ly = event.y_root - label.winfo_rooty() - oy
        if not (0 <= lx < dw and 0 <= ly < dh):
            return "break"
        fw, fh = holder["frame_px"]
        px, py = int(lx * fw / float(dw)), int(ly * fh / float(dh))
        session.click(px, py)
        # a click is always a take-over: mouse and keyboard both belong to the operator
        session.set_paused(True)
        status.configure(text="ПЕРЕХВАТ: вы управляете. Fn+F5 (или F5) - вернуть агенту")
        return "break"

    SCAN = {"Return": (0x1C, False), "KP_Enter": (0x1C, False),
            "BackSpace": (0x0E, False), "Tab": (0x0F, False),
            "Escape": (0x01, False), "space": (0x39, False),
            "Delete": (0x53, True), "Left": (0x4B, True), "Right": (0x4D, True),
            "Up": (0x48, True), "Down": (0x50, True),
            "Home": (0x47, True), "End": (0x4F, True),
            "Prior": (0x49, True), "Next": (0x51, True)}

    def on_key(event):
        """Keys reach the remote only while the operator holds the take-over."""
        session = holder["session"]
        if not session or not session.paused:
            return
        ks = event.keysym
        if ks in SCAN:
            code, ext = SCAN[ks]
            session.send_scan(code, ext)
            return "break"
        if event.char and event.char.isprintable():
            session.send_char(event.char)
            return "break"

    label.bind("<Button-1>", on_click)
    root.bind("<Button-1>", on_click)
    root.bind("<Key>", on_key)
    root.bind("<Escape>", lambda e: root.destroy())

    def resume(_event=None):
        holder["session"].set_paused(False)
        status.configure(text="агент продолжает печатать")
        return "break"

    root.bind("<F5>", resume)

    cmd_path = os.environ.get("RDP_CMD_FILE") or DEFAULT_CMD_FILE
    open(cmd_path, "a").close()
    print("Канал команд: %s  (TYPE / RUN / ENTER / KEY / CLICK / SCROLL / DRAG / SHOT / PASTE / STATE / STOP / PAUSE / RESUME)" % cmd_path, flush=True)

    holder["session"] = Session(args.host, args.port, cred,
                                on_frame=None,
                                on_state=lambda t: status.configure(text=t),
                                demo=args.selftest, marathon=args.marathon)
    holder["session"].keepalive_on = bool(args.keepalive) if hasattr(args, "keepalive") else True
    root.after(200, draw)
    root.mainloop()


if __name__ == "__main__":
    main()
