# macOS

macOS has no Xvfb and no per-display VNC server, so the Linux wiring does not map one
to one. The session is a real RDP client running headless inside the skill, and the
operator watches it in a Tk window.

## How it is wired

```
aardwolf (headless RDP) -> rdpview.py (Tk window) -> operator
        ^                        |
        |-- command channel: rdp_agent_cmd.txt (TYPE / RUN / CLICK / SCROLL / DRAG)
```

* `rdpview.py` connects with `aardwolf`, keeps the desktop buffer, draws it in a Tk
  window scaled to that window, and sends input over the same connection;
* the operator's cursor, keyboard focus and screen are never used, which is what makes
  parallel work possible;
* `rdpctl.py` stays the Linux entry point. On macOS its `main()` hands over to
  `rdpview.py`, the Linux code path is untouched.

Do **not** use a `.rdp` file and the Windows App window as the transport. Input sent
that way only arrives while that window has the focus and it steals the operator's
keyboard. That path was removed.

## Linux and macOS side by side

| Linux | macOS | Why |
|---|---|---|
| `Xvfb :77` | none | no X server on macOS |
| `xfreerdp` on that display | `aardwolf` in the same process | the client does not need a display |
| `x11vnc` + a VNC viewer | a Tk window drawing frames | macOS has no per-display VNC server |
| XTEST for input | RDP input events | input travels over the protocol |
| `import` for screenshots | `SHOT` saves the frame | the frame is already in memory |
| `python3-Xlib` | `pyobjc` not required | nothing local is driven |

## Rules

1. **Never change the remote resolution.** The session stays at the size the server
   negotiated, and the preview window scales the frame to whatever size it has.
2. **One connection per session.** Windows allows one client per user session. Do not
   start a second client while the skill holds the session.
3. **Coordinates are pixels of the frame**, not of the preview window. A 1024x768
   session is addressed as 0..1023 by 0..767 whatever the window size is. `SHOT` and
   `--status` print the size.
4. **No scroll wheel.** A wheel event makes this host reset the connection, seen twice
   in a row. Scrolling goes through `PageDown` and `PageUp`, or the arrows for single
   lines. Do not reintroduce the wheel without a host where it is proven safe.
5. **A drag ends with a release event.** `is_pressed` false is the mouse-up; a second
   press keeps the button held and the drop never happens.
6. **The keyboard needs the focus in the content.** A browser with the cursor in the
   address bar ignores both the wheel and `Page Down`. Click into the page first.
7. **Do not trust a frozen frame.** When the transport drops the process stays alive and
   the window keeps the last frame. `rdpview.py` reports `СОЕДИНЕНИЕ ПОТЕРЯНО`,
   `--status` prints `связь: потеряна`, and commands are refused. Wait ten minutes
   before reconnecting, otherwise the session hangs on "Please wait".

## Commands the agent uses

`SHOT`, `CLICK x y [right] [double]`, `TYPE`, `RUN`, `ENTER`,
`KEY <name> [N]`, `SCROLL up|down [N] [line]`, `PASTE`, `DRAG x1 y1 x2 y2 [steps]`,
`STATE`, `STOP`.

`SCROLL` and `DRAG` log `экран изменился` or `экран не изменился`, comparing the frame
before and after. `CLICK` logs the same, but a click that only moves the focus changes
nothing and that verdict is not a failure.

## Known limitations

* the wheel is unavailable, so scrolling is per screen or per line;
* there is no horizontal scroll;
* a full screen preview is slower than a window on a low resolution session;
* only one RDP connection can be active at a time.
