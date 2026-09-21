---
name: rdp-agent
description: >
  Drive a remote Windows machine over RDP in a live session that the operator can
  watch in a normal window and take over by hand at any moment. Use when the user
  asks to do something "on the remote machine", "over RDP", "on the remote
  desktop", to look at what is open there, to click through a GUI that has no CLI,
  or when a remote session has fallen into the lock screen and has to be recovered.
  The session is created once, survives between agent turns, and is closed only on
  the operator's explicit order.
metadata:
  version: 0.1.1
  author: Pavel Rykov <paul@drteam.rocks>
  homepage: https://github.com/EvilFreelancer/rdp-agent
  triggers: >
    rdp, remote desktop, xfreerdp, freerdp, windows over rdp, remote windows box,
    drive an rdp session, look at the remote desktop, rdp lock screen, unlock rdp
    session, rdp keepalive, по rdp, по рдп, удалённая машина, удалённый рабочий
    стол, подключись к рдп, экран блокировки, залипла сессия
---

# Working on a remote Windows machine over RDP

The agent drives a real RDP session by pixels: it takes a screenshot, looks at it,
clicks and types. Anything that a machine exposes without a GUI (PowerShell over
WinRM, SSH, an API) is faster and far more reliable than pixel automation, so
prefer those when they exist on the target and use this skill for what only the
desktop can do.

## How it is wired

The RDP client does not run on the operator's screen. It runs on an isolated Xvfb
display, that display is streamed by x11vnc to localhost, and a VNC viewer window
shows it to the operator. Three properties follow:

- the agent's synthetic input never moves the operator's real mouse or steals
  their focus;
- the operator sees the work live and can grab the mouse in the viewer window;
- closing the viewer window does not break the RDP session, it can be reopened.

```
xfreerdp -> Xvfb :77 (1920x1080) -> x11vnc 127.0.0.1:5977 -> viewer window
              ^
              XTEST: clicks and keys from the agent
```

## The one rule

**Never close the session.** Not after finishing a task, not at the end of a turn,
not "to clean up". `rdpctl.py stop` runs only when the operator says the work is
over. The operator may close the viewer window at any time, the session stays up.

## Work loop

1. `rdpctl.py status`, and if it says NOT CONNECTED, `rdpctl.py start`;
2. `rdpctl.py shot`, read the PNG, add `--crop X Y W H --zoom 4` for small text;
3. one action (click, key, text), then another `shot` to verify it landed;
4. when the next step is unclear or the task needs a human decision, stop, send
   the operator a fresh screenshot and ask;
5. after their answer, go back to step 2 and finish the job;
6. when they say it is done, leave the session running unless they asked to close
   it.

Always ask the operator when a password or a second factor is required, when the
action is irreversible (deleting, sending, deploying, rebooting), when an unknown
dialog is on screen, or when two attempts in a row did not produce the expected
result.

## Commands

```bash
python3 <skill>/scripts/rdpctl.py <command>
```

| Command | What it does |
|---------|--------------|
| `start [--no-viewer] [--no-keepalive] [--force]` | Bring up Xvfb, connect, start VNC, the viewer window and the jiggler. Idempotent, on a live session it only applies the keepalive choice. `--no-keepalive` for a look-only run, `--force` to retry after an authentication failure. |
| `status` | Session liveness, display, VNC port, whether a viewer is attached, warnings from the client log. |
| `shot [OUT.png] [--crop X Y W H] [--zoom N]` | Screenshot, prints the path. Without `--zoom` a cropped region stays at native size and small text is barely readable. |
| `state` | `desktop`, `locked` or `unknown`, plus a screenshot. |
| `click X Y [--button N] [--double]` | Click at full-screen pixel coordinates. |
| `move`, `drag X1 Y1 X2 Y2`, `scroll up\|down [N]` | Mouse. Move the pointer over the region before scrolling. |
| `key COMBO ...` | Keys and chords: `Return`, `Escape`, `alt+F4`, `ctrl+a`, `Super_L`, `ctrl+alt+Delete`. |
| `type "text"` | Latin text and digits, the Windows layout must be EN. |
| `typeru "текст"` | Cyrillic via the YCUKEN mapping, the Windows layout must be RU. |
| `lang` / `langtoggle` | Zoomed shot of the tray layout indicator / switch it with Alt+Shift and shoot again. |
| `paste "text"` | Clipboard transfer, works only where redirection is not blocked by policy. |
| `unlock [--type-password --operator-confirmed]` | Clear the lock screen. Typing the password requires the operator's confirmation. |
| `reconnect` | Restart only the RDP client, keeping Xvfb, VNC and the viewer. |
| `keepalive on\|off` | Mouse jiggler against the idle lock. The choice persists, a jiggler that died comes back with the next command while it is on. |
| `viewer [--force]` | Reopen the viewer window if the operator closed it. |
| `winlist` | Windows of the virtual X display, not Windows windows. One line with the xfreerdp window, useful only to confirm the client is alive. |
| `logs [-n N]` | Tails of the xfreerdp, x11vnc and Xvfb logs. |
| `stop` | Close the session. Only on the operator's explicit order. |

`--profile NAME` selects the profile and works in any position.

## The lock screen

A session can fall into the lock screen while the RDP connection stays up: instead
of the desktop you get the logon form. The agent does not die from this and does
not type the password by hand.

- `state` tells the desktop from the logon form by the bottom rows of the screen,
  the desktop has a near-black taskbar there and the logon form has wallpaper. An
  `unknown` verdict means "look at the screenshot yourself", a full-screen
  application produces it too. The blind spot is a dark wallpaper on the logon
  screen, which reads as a false `desktop`. Brightness spread does not separate
  the two cases, this was measured. So the rule is simple: if clicks produce no
  visible change, call `unlock --force` regardless of the verdict;
- `unlock` restarts the client, and Windows clears the lock by itself because the
  client comes back with the same credentials over NLA. The session keeps every
  open window;
- `unlock --type-password --operator-confirmed` types the password into the logon
  form. This is the fallback for hosts where policy forbids the NLA unlock. It
  runs only on a `locked` verdict, makes exactly one attempt and verifies the
  result.

Before using the typing fallback, look at the screenshot and make sure it really
is the Windows logon form and that the layout indicator says ENG. With a Cyrillic
layout the password goes in as garbage, which is a failed login attempt and a step
towards locking the domain account. If the screen is still not a desktop after
typing, do not repeat, show the screenshot to the operator.

To keep the lock from happening at all, a mouse jiggler (`scripts/jiggle.py`)
starts together with the session and nudges the pointer by one pixel every 90
seconds. It is disabled by `start --no-keepalive` or `keepalive off`, and the
interval lives in the profile as `RDP_JIGGLE_INTERVAL`. Note that this defeats the
idle-lock policy of the target machine, so it is the operator's call.

The choice is stored in `session.json`. While keepalive is on, every command that
needs the session brings back a jiggler whose process is gone and says so on
stderr, and `status` shows `DOWN` in between. A plain `start` on a live session
switches keepalive back on, `start --no-keepalive` on a live session switches it
off. A session started with `--no-keepalive` has no jiggler until someone asks for
it, so after a pause look at the `state` screenshot before typing anything.

## What is running inside Windows

There is no command for it, `winlist` shows X windows and not Windows windows. The
working method is to shoot the screen, look at the taskbar and see which buttons
carry the "running" indicator. On a dark Windows 10 theme a running application's
button sits on about (54,53,51) with a light strip about (179,178,175) in the two
bottom rows, while a pinned but not running shortcut keeps the plain taskbar
background about (38,37,36). Crop the taskbar with `--zoom 4` and read it.

## Typing text

Latin and Cyrillic need different Windows layouts, because the agent sends key
codes and not characters. Run `lang` to see the current layout in the tray,
`langtoggle` to switch it with Alt+Shift and get a fresh shot for verification,
and restore the original layout when done.

Always click into the target field first, otherwise the keys go to another window.

The clipboard (`paste`) works only where the host allows clipboard redirection.
Locked-down corporate images often disable it by group policy, and then `paste`
silently does nothing in both directions. Cyrillic still goes in through `typeru`.

The characters `@ # $ ^ & [ ] { } < > \` cannot be typed in the Russian layout at
all, `typeru` skips them and says so. Type Latin fragments separately after
switching the layout.

## Profiles and secrets

A profile lives in `~/.config/rdp-agent/<name>.env` with mode 600, outside of any
repository. The password is handed to the client over stdin, never appears in `ps`,
and is stripped from the command line written to the log. The script refuses to
run if the file is readable by anyone else.

```
RDP_HOST=win-host.example.com
RDP_PORT=3389
RDP_USER=user
RDP_DOMAIN=example.com
RDP_PASSWORD=...
RDP_DISPLAY=:77
RDP_VNC_PORT=5977
RDP_GEOMETRY=1920x1080
RDP_LANG_CROP=1540 1040 200 35
RDP_JIGGLE_INTERVAL=90
```

If a login fails on authentication, the fact is recorded in `session.json` and the
next `start` refuses until the operator checks the password and allows a retry with
`--force`. This guard protects the domain account from lockout and must not be
worked around automatically.

A display is pinned to its profile through `~/.local/state/rdp-agent/displayNN.owner`,
so `start` of another profile on a busy display fails with a clear message instead
of quietly attaching to a foreign X server. A new machine means a new profile with
its own `RDP_DISPLAY` and `RDP_VNC_PORT`.

## Field notes

- **Proxy.** `xfreerdp` reads `http_proxy` from the environment and tunnels RDP
  through it, which fails with `ERRCONNECT_CONNECT_TRANSPORT_FAILED` and looks
  like a dead host. `rdpctl` strips the proxy variables for the client and for
  x11vnc, a hand-run client needs `env -u http_proxy -u https_proxy -u all_proxy`;
- **Account lockout.** Domain accounts lock after a few failed logins. Never retry
  a failed connection blindly, read the log and ask the operator;
- **Someone else's session.** Connecting takes over the same Windows session the
  operator uses, no second parallel session appears. If they were sitting at that
  machine, they are dropped to disconnected;
- **`pkill -f xfreerdp`** kills the agent's own shell when the pattern appears in
  its command line. Kill by PID from `session.json` or use `pkill -x xfreerdp`;
- **Viewer tabs.** Remmina opens connections as tabs inside one running instance,
  so a second `viewer` creates a second tab and `stop` cannot close them. `rdpctl`
  checks for a live TCP connection to the VNC port instead of trusting a PID;
- **Painting delay.** After opening a menu or launching an application wait one to
  three seconds, otherwise the screenshot catches an intermediate state;
- **Flags after the command.** The global `--profile` and `--timeout` are pulled
  out of argv by hand, because `argparse.REMAINDER` used to swallow them silently;
- **Jiggler and dragging.** The pointer twitches every 90 seconds, so a very long
  drag can collide with it. Turn `keepalive off` for such operations.

## Requirements

`xfreerdp` (FreeRDP 2 or 3), `Xvfb`, `xdpyinfo`, ImageMagick (`import`, `convert`),
Python 3 with `python-Xlib`, and optionally `x11vnc` plus any VNC viewer for the
operator's window. Pillow is used for the screen-state heuristic and is optional.
Linux with X11 on the operator's side; the target is any host that speaks RDP.

## State and logs

`~/.local/state/rdp-agent/<profile>/` holds `session.json` with the PIDs, the
process logs and a `shots/` directory with every screenshot.
