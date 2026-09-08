#!/usr/bin/env python3
"""xpaste.py - serve UTF-8 text on the X CLIPBOARD so an app can paste it.

Reliable way to get unicode / non-Latin (Russian) text into a GUI app: run this
in the background to own the CLIPBOARD selection, then `xctl.py key ctrl+v` in the
target. This sidesteps synthetic typing, which mangles symbols and trips editor
auto-close of brackets and quotes.

    python3 xpaste.py "любой текст с @ и {скобками}" 15 &   # serve for 15 s
    python3 xctl.py key ctrl+v

Display comes from $XCTL_DISPLAY or --display (default :99). Serves both CLIPBOARD
and PRIMARY. Exits after the timeout (default 20 s) or once the selection is taken
over by another owner.
"""
import os
import sys
import time

from Xlib import X, Xatom, display as _display


def main():
    argv = [a for a in sys.argv[1:]]
    disp_name = ":99"
    if "--display" in argv:
        i = argv.index("--display")
        disp_name = argv[i + 1]
        del argv[i:i + 2]
    disp_name = os.environ.get("XCTL_DISPLAY", disp_name) if "--display" not in sys.argv else disp_name
    # "-" means read the text from stdin, so it never shows up in ps
    text = (sys.stdin.buffer.read() if argv[0] == "-" else argv[0].encode("utf-8"))
    timeout = float(argv[1]) if len(argv) > 1 else 20.0

    d = _display.Display(disp_name)
    root = d.screen().root
    win = root.create_window(0, 0, 1, 1, 0, d.screen().root_depth)

    CLIPBOARD = d.intern_atom("CLIPBOARD")
    PRIMARY = Xatom.PRIMARY
    UTF8 = d.intern_atom("UTF8_STRING")
    TARGETS = d.intern_atom("TARGETS")
    STRING = Xatom.STRING

    for sel in (CLIPBOARD, PRIMARY):
        win.set_selection_owner(sel, X.CurrentTime)
    d.sync()

    deadline = time.time() + timeout
    while time.time() < deadline:
        # give up if we lost ownership of CLIPBOARD (someone pasted / took over)
        if d.get_selection_owner(CLIPBOARD) != win:
            break
        while d.pending_events():
            e = d.next_event()
            if e.type != X.SelectionRequest:
                continue
            req = e
            prop = req.property or req.target
            if req.target in (UTF8, STRING):
                req.requestor.change_property(prop, req.target, 8, text)
            elif req.target == TARGETS:
                data = [UTF8, STRING, TARGETS]
                req.requestor.change_property(prop, Xatom.ATOM, 32, data)
            else:
                prop = 0
            ev = _selnotify(d, req, prop)
            req.requestor.send_event(ev)
            d.sync()
        time.sleep(0.02)


def _selnotify(d, req, prop):
    from Xlib.protocol import event
    return event.SelectionNotify(
        time=req.time, requestor=req.requestor, selection=req.selection,
        target=req.target, property=prop,
    )


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    main()
