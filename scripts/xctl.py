#!/usr/bin/env python3
"""xctl.py - drive a GUI application on an X display via XTEST (python-Xlib).

Intended for capturing documentation screenshots of desktop apps (Cursor, IDEs,
browsers) that run on an ISOLATED virtual display (Xvfb), so synthetic input and
captures never touch the operator's real desktop. Pick the display with
$XCTL_DISPLAY or --display; the default is :99, never :0.

Commands:
  move  X Y                                move the pointer
  click X Y [--button N] [--double]        move, then click (1 left / 2 mid / 3 right)
  key   COMBO [COMBO ...]                  press chords, e.g. ctrl+shift+l Return Escape
  type  TEXT                               type ASCII text (layout-correct via keymap)
  scroll up|down [N]                       wheel N notches (buttons 4/5)
  drag  X1 Y1 X2 Y2 [--button N]           press, move, release (resize panels/windows)
  place SUBSTR X Y W H                      move+resize first window matching name/class
  shot  OUT.png [--crop X Y W H]           capture the whole display (ImageMagick import)
  winlist                                  list mapped windows (name, class, geometry)

For unicode / non-Latin text (e.g. Russian) do NOT use `type`: put the string on
the clipboard with xpaste.py and paste with `key ctrl+v`. Synthetic typing fights
the editor's auto-closing brackets and dead keys and silently mangles symbols.
"""
import argparse
import os
import subprocess
import sys
import time

from Xlib import X, XK, display as _display
from Xlib.ext import xtest

MODIFIERS = {
    "ctrl": "Control_L", "control": "Control_L",
    "shift": "Shift_L",
    "alt": "Alt_L", "meta": "Alt_L",
    "super": "Super_L", "win": "Super_L",
}
# friendly names -> X keysym names for non-character keys
NAMED = {
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
    "tab": "Tab", "space": "space", "backspace": "BackSpace", "delete": "Delete",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next",
}


def get_display():
    name = None
    if "--display" in sys.argv:
        name = sys.argv[sys.argv.index("--display") + 1]
    return _display.Display(name or os.environ.get("XCTL_DISPLAY") or ":99")


class Ctl:
    def __init__(self):
        self.d = get_display()
        self.root = self.d.screen().root
        self._charmap = None

    def sync(self):
        self.d.sync()

    # ---- pointer -----------------------------------------------------------
    def move(self, x, y):
        self.root.warp_pointer(x, y)
        self.sync()

    def button(self, press, detail=1):
        xtest.fake_input(self.d, X.ButtonPress if press else X.ButtonRelease, detail)
        self.sync()

    def click(self, x, y, detail=1, double=False):
        self.move(x, y)
        time.sleep(0.05)
        for _ in range(2 if double else 1):
            self.button(True, detail)
            time.sleep(0.03)
            self.button(False, detail)
            time.sleep(0.09 if double else 0.02)

    def scroll(self, direction, n=3):
        detail = 4 if direction == "up" else 5
        for _ in range(n):
            self.button(True, detail)
            self.button(False, detail)
            time.sleep(0.02)

    def drag(self, x1, y1, x2, y2, detail=1, steps=20):
        self.move(x1, y1)
        time.sleep(0.05)
        self.button(True, detail)
        time.sleep(0.05)
        for i in range(1, steps + 1):
            self.move(int(x1 + (x2 - x1) * i / steps), int(y1 + (y2 - y1) * i / steps))
            time.sleep(0.01)
        time.sleep(0.05)
        self.button(False, detail)

    # ---- keyboard ----------------------------------------------------------
    def _keycode(self, keysym_name):
        keysym = XK.string_to_keysym(keysym_name)
        if keysym == 0 and len(keysym_name) == 1:
            keysym = ord(keysym_name)
        return self.d.keysym_to_keycode(keysym)

    def key(self, combo):
        parts = combo.split("+")
        *mods, base = parts
        mod_codes = [self._keycode(MODIFIERS[m.lower()]) for m in mods]
        base = NAMED.get(base.lower(), base)
        code = self._keycode(base)
        for mc in mod_codes:
            xtest.fake_input(self.d, X.KeyPress, mc)
        self.sync()
        xtest.fake_input(self.d, X.KeyPress, code)
        self.sync()
        xtest.fake_input(self.d, X.KeyRelease, code)
        for mc in reversed(mod_codes):
            xtest.fake_input(self.d, X.KeyRelease, mc)
        self.sync()
        time.sleep(0.03)

    def _build_charmap(self):
        """char -> (keycode, needs_shift), read from the live keyboard mapping."""
        self._charmap = {}
        mn, mx = self.d.display.info.min_keycode, self.d.display.info.max_keycode
        mapping = self.d.get_keyboard_mapping(mn, mx - mn + 1)
        for i, syms in enumerate(mapping):
            code = mn + i
            for level, ks in enumerate(syms[:2]):  # unshifted, shifted
                if ks == 0:
                    continue
                ch = None
                if 0x20 <= ks <= 0x7E:
                    ch = chr(ks)
                if ch and ch not in self._charmap:
                    self._charmap[ch] = (code, level == 1)

    def type_text(self, text):
        if self._charmap is None:
            self._build_charmap()
        shift = self._keycode("Shift_L")
        for ch in text:
            entry = self._charmap.get(ch)
            if entry is None:
                sys.stderr.write(f"xctl: no key for {ch!r}, skipped (use xpaste.py)\n")
                continue
            code, need_shift = entry
            if need_shift:
                xtest.fake_input(self.d, X.KeyPress, shift)
            xtest.fake_input(self.d, X.KeyPress, code)
            xtest.fake_input(self.d, X.KeyRelease, code)
            if need_shift:
                xtest.fake_input(self.d, X.KeyRelease, shift)
            self.sync()
            time.sleep(0.02)

    # ---- windows -----------------------------------------------------------
    def _windows(self):
        out = []

        def walk(w):
            try:
                for c in w.query_tree().children:
                    try:
                        attrs = c.get_attributes()
                        if attrs.map_state == X.IsViewable:
                            name = c.get_wm_name() or ""
                            cls = c.get_wm_class() or ("", "")
                            g = c.get_geometry()
                            out.append((c, name, cls[1] if cls else "", g))
                    except Exception:
                        pass
                    walk(c)
            except Exception:
                pass

        walk(self.root)
        return out

    def winlist(self):
        for _, name, cls, g in self._windows():
            print(f"{g.width}x{g.height}+{g.x}+{g.y}  class={cls!r:20} name={name!r}")

    def place(self, substr, x, y, w, h):
        substr = substr.lower()
        for win, name, cls, _ in self._windows():
            if substr in (name or "").lower() or substr in (cls or "").lower():
                win.configure(x=x, y=y, width=w, height=h,
                              stack_mode=X.Above)
                self.sync()
                print(f"placed {cls or name!r} -> {w}x{h}+{x}+{y}")
                return
        sys.exit(f"xctl: no window matching {substr!r}")

    # ---- capture -----------------------------------------------------------
    def shot(self, out, crop=None):
        disp = self.d.get_display_name()
        subprocess.run(["import", "-display", disp, "-window", "root", out], check=True)
        if crop:
            x, y, w, h = crop
            subprocess.run(["convert", out, "-crop", f"{w}x{h}+{x}+{y}", "+repage", out],
                           check=True)
        print(out)


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--display")
    ap.add_argument("cmd")
    ap.add_argument("rest", nargs=argparse.REMAINDER)
    if len(sys.argv) == 1 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return
    args = ap.parse_args()
    c = Ctl()
    r = args.rest
    cmd = args.cmd

    if cmd == "move":
        c.move(int(r[0]), int(r[1]))
    elif cmd == "click":
        btn = int(r[r.index("--button") + 1]) if "--button" in r else 1
        c.click(int(r[0]), int(r[1]), btn, "--double" in r)
    elif cmd == "key":
        for combo in [x for x in r if x != "--display"]:
            c.key(combo)
    elif cmd == "type":
        c.type_text(r[0])
    elif cmd == "scroll":
        c.scroll(r[0], int(r[1]) if len(r) > 1 and r[1].isdigit() else 3)
    elif cmd == "drag":
        btn = int(r[r.index("--button") + 1]) if "--button" in r else 1
        c.drag(int(r[0]), int(r[1]), int(r[2]), int(r[3]), btn)
    elif cmd == "place":
        c.place(r[0], int(r[1]), int(r[2]), int(r[3]), int(r[4]))
    elif cmd == "winlist":
        c.winlist()
    elif cmd == "shot":
        crop = None
        if "--crop" in r:
            i = r.index("--crop")
            crop = tuple(int(v) for v in r[i + 1:i + 5])
        c.shot(r[0], crop)
    else:
        sys.exit(f"xctl: unknown command {cmd!r} (see --help)")


if __name__ == "__main__":
    main()
