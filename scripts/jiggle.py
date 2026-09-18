#!/usr/bin/env python3
"""jiggle.py - nudges the pointer by one pixel so the remote session does not
fall into the idle lock while an agent is working with it.

    jiggle.py :77 [interval_seconds]

The nudge is relative to the current position and is undone immediately, so it
does not disturb the agent's own clicking. The process exits by itself once the
virtual display is gone; any other error skips one nudge instead of ending the
keepalive, and rdpctl.py restarts the jiggler if its process disappears anyway.
"""
import sys
import time

from Xlib import display as _display
from Xlib import error as _xerror


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    disp_name = args[0] if args else ":77"
    interval = float(args[1]) if len(args) > 1 else 90.0

    d = _display.Display(disp_name)
    root = d.screen().root
    width = root.get_geometry().width

    while True:
        time.sleep(interval)
        try:
            p = root.query_pointer()
            x, y = p.root_x, p.root_y
            root.warp_pointer(x - 1 if x >= width - 1 else x + 1, y)
            d.sync()
            time.sleep(0.2)
            root.warp_pointer(x, y)
            d.sync()
        except _xerror.ConnectionClosedError as e:  # display died with the session
            sys.stderr.write(f"jiggle: stopped ({e})\n")
            return 0
        except Exception as e:
            sys.stderr.write(f"jiggle: nudge failed, retrying in {interval:g}s ({e})\n")


if __name__ == "__main__":
    sys.exit(main())
