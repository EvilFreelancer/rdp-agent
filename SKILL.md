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
  Russian: «настрой подключение к RDP», «настрой RDP», «настрой удалёнку»,
  «RDP скилл», «RDP навык», «подключись к удалённой машине».
metadata:
  version: 0.1.2
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
2. take the task as a whole: plan the few actions it needs and say them in one sentence;
3. do them in order, taking a screenshot whenever you need to see the result or pick
   coordinates, and verifying each step with the next one instead of a fresh screenshot
   after every action;
4. when the task is done, report once in the operator's words and ask what is next;
5. leave the session running unless they asked to close it.

Ask only about a problem (a step failed twice, the connection dropped, the screen is not
what the task expected), a fork (two different variants and the choice changes the
result), or a password or second factor, which the operator always enters themselves. A
screenshot, a click inside the task, scrolling or saving a file the task asked for is
never a reason to ask.

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

The target is any host that speaks RDP. On the operator's side the skill runs on
Linux with X11, or on macOS.

**Linux:** `xfreerdp` (FreeRDP 2 or 3), `Xvfb`, `xdpyinfo`, ImageMagick (`import`,
`convert`), Python 3 with `python-Xlib`, and optionally `x11vnc` plus any VNC viewer
for the operator's window.

**macOS:** `aardwolf` (installed by `macsetup.py fix`), `Pillow` and `tkinter`, both of
which come with the python.org build. No X server is involved and no system permissions
are needed: frames and input travel over the RDP protocol and the Tk window only draws
them locally. `references/macos.md` has the details and the rules.

Pillow is used for the screen-state heuristic and is optional on both.

## State and logs

`~/.local/state/rdp-agent/<profile>/` holds `session.json` with the PIDs, the
process logs and a `shots/` directory with every screenshot.

## macOS

On macOS there is no Xvfb and no per-display VNC server, so the client runs headless
inside the skill and input travels over the RDP protocol. The operator's cursor and
keyboard focus are never used, which is what makes parallel work possible.

### How it works here

On macOS the session runs through `scripts/rdpview.py`: it connects over the RDP
protocol with `aardwolf`, draws the frames in a Tk window and sends input over the
same protocol. The operator's cursor and keyboard focus are not used at all, which is
what allows parallel work.

Do **not** use the `.rdp` file and the Windows App window as the transport on macOS:
input sent that way only reaches the session when that window has focus, and it steals
the operator's keyboard. That path was removed from the repository, so `rdpview.py` is
the only macOS transport.

Start it with the host as the only argument:

```bash
python3 scripts/rdpview.py --host <host>
```

The operator types the password in the hidden prompt. There is no `start` command on
macOS and no `rdpctl_mac.py` any more.

The command channel is always open: `rdpview.py` prints its path on start-up and uses
`$TMPDIR/rdp_agent_cmd.txt` unless `RDP_CMD_FILE` points elsewhere. Write one command
per line into that file to make the session type, open programs or press keys. There
is no need to restart the session to gain control: it is on from the first second.

Run it from a shell without a TTY and it reopens itself in Terminal, because the
password prompt needs a real terminal. Do not drive Terminal through AppleScript:
`do script` fails with error -609 unless the Automation permission is granted, while
`open -a Terminal <file>.command` always works.

### Do not verify the warm-up yourself

You still **ask** for it: say plainly that the operator must open their RDP client,
sign in, and reach the desktop, and wait for their confirmation. What you must not do
is check it technically.

Trust the operator when they say the session is warm. Do not check it with `lsof`,
`netstat` or a port scan: on macOS a live RDP session often shows no established
connection on 3389, and the check produces false negatives that block a working
setup.

The truth is revealed by the session itself: if the warm-up was not done,
`rdpview.py` logs `сессия не ответила за 20 секунд` and exits without sending
anything. That is the check, and it costs nothing.

### Before the first session

**You never install anything on your own.** Run the check, show the operator the full
report, name what is missing, and ask for explicit permission. Install only after the
operator agrees, and only what the report named.

Run the check with an interpreter that is 3.11 or newer. The system `python3` on
macOS is 3.9 and must not be used: on macOS 14 it can even abort with SIGABRT before
the script starts. The python.org installer is the safest source: it is signed, universal2, and already
carries Tcl/Tk for the preview window. Its interpreter lives at
`/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12`.

Do not install Python with Homebrew on macOS 14 without checking first. When Homebrew
has no bottle for the configuration it compiles the interpreter from source and pulls
a downgrade of `openssl@3`, which breaks other packages. `brew info python@3.12` shows
whether a bottle exists; if it does not, say so and use the python.org installer
instead. Never let a source build run without the operator's explicit consent.

Run the check and show the result to the operator:

```bash
python3 scripts/macsetup.py check
```

It exits with `0` when everything is ready and `1` otherwise. If packages are missing,
ask the operator for permission and run `python3 scripts/macsetup.py fix`.

On Intel Macs `fix` needs Rust; the check reports it. Do not install Rust on your own.

No system permissions are involved on macOS: nothing is captured from the local screen
and no local input is posted, so Accessibility and Screen Recording are not needed. Do
not send the operator to System Settings for them.

The operator types the login and the password themselves. Say so before the first
connection: the terminal asks for the login as `DOMAIN\user` and then for the password
in a hidden prompt. These are the same corporate credentials the operator uses for the
company VPN client, for example NGate, Cisco AnyConnect or OpenVPN, there is no separate
account for the skill. The password authorises the connection only and is never written
to disk. Do not ask for it in the chat and do not store it. If the session shows a logon
form, do not type the password into it either, send `Ctrl+Alt+Del` instead.

### Starting

Ask the operator to open the RDP client they normally use for this machine (Windows
App, Microsoft Remote Desktop or any other) and reach the desktop. This warm-up is
mandatory: after it the session is authorised and `Ctrl+Alt+Del` is enough to unlock.

Do not start `rdpview.py` until the operator confirms in the chat that they are on the
desktop. Explaining the warm-up is not a confirmation: wait for their answer, "прогрел",
"дошёл до десктопа" or the like. Starting without it wastes the single connection
attempt and can leave the session hanging.

Then start the skill. One connection attempt per run. If a connection is dropped, wait at least ten minutes
before the next attempt: repeated reconnects leave the session hanging on the "Please
wait" screen. This is a limit on reconnects only. A live session has no time limit and
runs as long as the task needs, hours included.

### Typing text: TYPE first

`TYPE` sends real key events and handles Cyrillic correctly. `PASTE` depends on clipboard
redirection, which many corporate hosts disable: the call succeeds, the log says
"вставлено N символов", and nothing appears in the window.

When text does not show up, switch to `TYPE` before investigating anything else. Do not
chase focus or timing problems that are really a blocked clipboard.

### First connection: prove that input works

On the very first connection add `--selftest`. The script then opens `cmd`, types
`echo Hello World!` and presses Enter by itself, a few seconds after the desktop
appears.

Ask the operator to confirm they see `Hello World!` in the console. That single line
proves the whole chain: the session is up, frames arrive, and input reaches Windows.
If they see it, tell them the setup is done and switch to normal instructions.

### Right after start

The script prints two lines and opens the preview window:

```
Канал команд: /var/folders/.../rdp_agent_cmd.txt  (TYPE / RUN / ENTER / KEY / CLICK / SCROLL / DRAG / SHOT / PASTE / STATE / STOP / PAUSE / RESUME)
Открыл Terminal: введите логин и пароль в новом окне.
```

That is the whole start-up report. Tell the operator the session is up, give them the
command channel path in one line, and stop.

Do **not** go looking for the process afterwards: no `ps`, no `CGWindowListCopyWindowInfo`,
no `screencapture`, no reading the script or the references. Those checks add minutes of
noise and nothing else. The window either appears or the script prints an error; both
are already visible.

Wait for the operator's instruction. When one arrives, write it into the command file.

### Session commands on macOS

| What | How |
|---|---|
| state of a live session | `python3 scripts/rdpview.py --status` prints host, pid, frames, screen verdict, take-over owner and the log path |
| screen | `STATE` in the command channel logs `desktop`, `locked` or `unknown` |
| type | `TYPE <text>` — the default way to enter text, Cyrillic included |
| open a program | `RUN <program> [args]` |
| press a key | `ENTER`, or `KEY up|down|left|right [N]`, and the same way `esc`, `tab`, `home`, `end`, `pgup`, `pgdn`, `delete`. Names are easier than scancodes; a raw `KEY 0x51 ext` still works |
| key combination | `KEY ctrl+a`, `KEY ctrl+shift+esc`, `KEY alt+f4`, `KEY win+r`. Modifiers are `ctrl`, `shift`, `alt`, `win`, the base key is a letter, a digit, `f1` to `f12` or any name from the row above |
| paste | `PASTE <text>` puts it into the RDP clipboard and presses Ctrl+V. Unreliable on locked-down hosts: the log reports success while nothing appears. `TYPE` is the default path |
| scroll | `SCROLL up|down [N]` scrolls N screens with `PageDown` / `PageUp`, 1 by default. Add `line` to scroll N lines with the arrows instead, for fine tuning. The wheel is not used on macOS: a wheel event resets the connection on this host, so the keyboard is the only safe path. The page or list has to hold the focus, click into it first |
| click | `CLICK x y [right] [double]` presses and releases the button, the way to set the focus before typing or scrolling. A click that only moves the focus reports `экран не изменился`, and that is not a failure |
| drag | `DRAG x1 y1 x2 y2 [steps]` presses at the first point, moves in `steps` increments (20 by default) and releases at the second. Needed for files, sliders and window edges |
| screenshot | `SHOT [path] [x y w h [zoom]]` saves the current frame to `$TMPDIR/rdp_agent_shot.png` and logs the path with its size. Add `x y w h` to save only that rectangle, and a number after it to zoom it up to 8 times: `SHOT 600 700 400 40 4` reads small text without a round trip through Pillow. On macOS the session is only drawn in the operator's window, so this is the only way the agent can see it. Take one before a `DRAG` to pick the coordinates |
| stop the session | `STOP` disconnects, terminates and logs it. Only on the operator's order |
| logs | `$TMPDIR/rdp_agent.log`, the same lines the terminal shows, no passwords |

All coordinates are pixels of the session frame, not of the preview window: a
1024x768 session is addressed as 0..1023 by 0..767 no matter how large the operator
window is. `SHOT` and `--status` print the size, take it from there instead of guessing.

`SCROLL` and `DRAG` compare the frame before and after and put the verdict in the log:
`экран изменился` or `экран не изменился`. That line is the check. Do not take a
screenshot to confirm a command, and never repeat the same command three times: if the
log says the screen did not change, report the coordinates you used and stop.

The wheel turns the window under the pointer, not the focused one. `SCROLL` moves the
pointer itself, but if nothing moves the content may still have no focus: click into
the page or the pane first, then scroll. A browser with the cursor in the address bar
ignores both the wheel and `Page Down`.

Keepalive nudges the pointer every 55 seconds so the session does not lock while idle;
`--no-keepalive` turns it off.

### Speak human, act in commands

The operator talks about the task, never about the protocol. "Scroll down a bit",
"drag this window to the right", "type my name in that field" are complete
instructions: translate them into channel commands yourself. Never ask the operator to
write `SCROLL`, to pick a button or to give you coordinates, and never read the command
list back to them.

| They say | You send |
|---|---|
| "scroll down", "листни вниз", "прокрути" | `SCROLL down 1`, one screen; "ещё" means the same again, "до конца" means 5 or more |
| "scroll up", "вернись наверх" | `SCROLL up 1` |
| "на строчку ниже", "чуть-чуть вниз" | `SCROLL down 3 line`, the arrows instead of whole pages |
| "стрелку вниз три раза", "перейди в соседнюю ячейку" | `KEY down 3`, the same way `KEY left`, `KEY right`, `KEY up` |
| "выдели всё", "скопируй", "вставь", "сохрани" | `KEY ctrl+a`, `KEY ctrl+c`, `KEY ctrl+v`, `KEY ctrl+s` |
| "закрой окно", "переключись на другое окно" | `KEY alt+f4`, `KEY alt+tab` |
| "click on ...", "нажми на ...", "поставь курсор в поле" | `SHOT`, take the point, then `CLICK x y` |
| "drag this window", "перетащи окно" | `SHOT`, take the title bar and the destination, then `DRAG` |
| "move the file there" | `SHOT`, take the file and the folder, then `DRAG` |
| "show me the screen" | `SHOT` and read the picture yourself, describe what is on it |
| "open ..." | `RUN ...` |
| "type ..." | `TYPE ...`, and `ENTER` when they ask to submit |

Rules:

- take the coordinates from `SHOT`; the frame is the session size, not the window size;
- one `SCROLL` step is one screen, "до конца" is 5 steps or more;
- when the target is ambiguous, meaning two windows or several files, ask one short
  question in their own words and offer the choice. Do not guess silently;
- if a command reports `экран не изменился`, say it in one sentence and propose what to
  change, another point or another count. Do not repeat the same command three times;
- report the outcome in their words: "пролистал ниже", "окно переставил", not "отправил
  DRAG 400 100 650 300".

### One task, one confirmation

A task is given once, and that single approval covers the whole task. "Открой гугл,
найди xml to json и пролистай вниз" already allows every command needed: open the
browser, click, type, press Enter, scroll, and take a screenshot whenever you need
coordinates. Never ask permission for a screenshot or for the next command inside the
task: a screenshot is your eyes, not an action on the operator's machine.

Say what you are about to do in one sentence, then do it end to end and report once:

> Открыл Chrome, нашёл «xml to json», пролистал три экрана, у дна страница кончилась.
> Дальше открыть второй результат?

These questions are forbidden:

- "Открыл Блокнот. Что дальше?" when the task already says what to do next;
- "Сохранить файл?" when saving was not asked for: leave it unsaved and mention it once
  in the final report instead of asking;
- "Мне сделать снимок?" or "Какие координаты взять?";
- "Продолжать?" after each command;
- "Какой вариант нужен?" when both readings lead to the same next action;
- "Закрыть лишнее окно / убрать черновик?" when the task did not ask to close or
  delete anything.

Do not tidy up after a task. Leave the windows and the drafts as they are, and mention
the leftovers in one line of the report. Closing something the task did not name is not
cleanup, it is a destructive action on the operator's machine.

Type the text once. If the screenshot after typing is ambiguous because the font is small,
take a cropped and zoomed one instead of typing the same text again:
`SHOT 300 100 500 300 3`. Typing the same lines twice leaves duplicates and looks like a
different task was done.

Check the text before opening a save dialog, never after. `Ctrl+S` in an unnamed document
puts the Save As window on top of the text, so the next frame shows the dialog and not the
lines, and the missing lines look like a failed input. The order is: type, `SHOT`, read
the lines, then `Ctrl+S`.

Overwriting is never silent, even when the task named the file. If `test1.txt` already
exists and the task says "save as test1", stop before confirming and warn:

> файл test1.txt уже есть на рабочем столе. Заменить, сохранить как test1-2 или отменить?

Give the three options in one line and wait. Windows shows its own "Файл already exists"
dialog with Да and Нет: that dialog is the overwrite confirmation, so do not click Да by
yourself, come back to the operator with the warning instead. The same applies to any
"replace?" or "overwrite?" question an application draws.

The rule is general, saving is only the most common case. Any conflict with data that
already exists counts: a target folder that holds a file with the same name, a dialog
that offers to replace, delete or discard, an application that asks whether to keep the
changes. In all of them stop first, say in one line what exactly will be lost, offer the
options, and wait for the answer. This is the one case where asking is always right.

Stop and ask exactly one question only in these four cases:

1. **an error you cannot explain:** a command reports an error, the screen does not match
   what the task expects, or the same step failed twice. Quote the exact log line, say
   what you think happened, and wait. Do not switch to another method, another window or
   another folder on your own.
2. **a problem:** the connection dropped;
3. **a fork:** the task is genuinely ambiguous, meaning two windows or two files fit
   equally, or the combination simply does not exist;
4. **a destructive action:** delete, send, overwrite an existing file, close with
   unsaved changes, reboot. Warn in one line about what exactly will be lost, give the
   options, and wait. A file name in the task is not permission to overwrite it.

Ask one question, give the options in one line, wait for the answer. Everything else
runs on the first confirmation.

### When something fails

An error is a message for the operator, not an invitation to improvise. These log lines
mean "this did not work":

- `мышь: событие не отправлено (...)` — the pointer event did not leave the client;
- `key: неизвестная клавиша '...'` or `key: не разобрал сочетание '...'` — the name was
  not understood, nothing was pressed;
- `cmd: неизвестная команда '...'` — the verb does not exist, nothing was executed;
- `СОЕДИНЕНИЕ ПОТЕРЯНО` — the session is dead and the frame on screen is the last one.

Report the line, say what you think it means, and wait. Do not retry with another
coordinate, another command, another window, another folder or another application on
your own: a silent workaround is how a small error turns into a wrong result.

`прокрутка ...: экран не изменился` is not always an error: at the end of a page it is
the correct answer. Judge by the task: if more content was expected, report it.

The only exception is a typo in your own command, an unknown verb or a wrong argument.
Fix the spelling once and carry on. Everything else is a stop.

### When to read the log

Everything is written to `$TMPDIR/rdp_agent.log` all the time, and that is fine. Read it
only when something did not work: a command reported an error, or the operator says the
result is not what they expected.

Do not read the log after every command. A normal exchange is: the operator asks, you
send the command, you report what should have happened. If they then say it did not
work, open the log and look at the last lines.

The exception is `SCROLL` and `DRAG`: read the one verdict line they write, because you
cannot see the screen yourself.

### If the connection drops

`ConnectionResetError` means the transport is gone. The window keeps showing the last
frame and the process stays alive, so nothing looks broken until input stops working.
The log says it plainly:

```
СОЕДИНЕНИЕ ПОТЕРЯНО: мышь: ConnectionResetError(54, 'Connection reset by peer')
кадры ниже этой строки не обновляются, закройте окно и подключитесь заново через 10 минут
```

`--status` then prints `связь: потеряна`, `SHOT` returns the last frame and commands are
refused with `команда не отправлена: соединение потеряно`. Do not read that frame as the
current screen.

Do not retry at once. Tell the operator the transport dropped, ask them to check the
company VPN client and their own RDP client, close the window, wait ten minutes and
connect again.

### Do not check the session before every action

One successful `--status` right after the connection is enough. After that, run the
operator's command straight away: `RUN`, `TYPE`, `PASTE`, `ENTER`. Do not repeat
`--status`, do not list files, do not grep the script between commands.

Check again only when the operator asks, or when a command visibly failed and you need
to understand why.

### During the session

- The operator clicks inside the preview window to take over. From that moment send
  nothing until they say so.
- The hotkey that hands control back is **F5**. Say F5 first, and add that if nothing
  happens the key is **Fn+F5**: on a MacBook the top row is media keys, while an iMac
  keyboard and external keyboards send F5 directly. The preview window must hold the
  focus, so do not switch to the terminal before pressing it.
- While the operator holds control you can still execute their instructions in the
  same session; there is no need to reconnect.
- Never type a password into the Windows logon form. Use `Ctrl+Alt+Del` instead.

### What to tell the operator

- The remote resolution is never changed.
- Full screen is slower than a window on a low resolution session.
- Only one connection can be active. Tell it neutrally: you may leave your client
  open, the session will end automatically and move to the new window, or close it
  yourself after the warm-up. There is no need to insist on either.

### Finding the host

A new operator often does not remember the address. Run `python3 scripts/rdpfind.py`
first: it reads `.rdp` files in Downloads, Documents and Desktop and the saved
connections of the RDP clients that keep them on disk, and prints what it found.

Which client is installed does not matter: `.rdp` files come from any of them, and the
client databases are only an extra source. If nothing is found, say so and point the
operator to whoever issued the machine. Never invent an address, and never guess from
a name that looks similar.

## Setup on a fresh machine

When the operator says "настрой подключение к RDP", "настрой RDP", "настрой удалёнку",
"RDP навык / RDP скилл", "подключись к удалённой машине", or in English "set up the RDP
connection" / "set up the remote desktop skill", follow this sequence exactly.
A bare "настрой" is not enough: it may belong to another skill. Do not skip a
step and do not run two steps at once.

1. **Report the platform first.** `python3 scripts/macsetup.py check` prints the macOS
   version, the CPU (Apple Silicon or Intel) and every check with a verdict.
2. **Read the report out loud.** List only the FAIL lines with their hint. Say plainly
   which ones need a command and which ones need the operator's hands.
3. **Nothing is installed by default.** For every missing item say what it is, what
   breaks without it, then offer both routes: here is the exact command, run it
   yourself, or say "ставь сам" / "install it" and the agent runs it.
   Everything in the report is required for the skill to work correctly. The only
   exception is Rust, and it is not a matter of taste: it is needed on Intel and
   skipped on Apple Silicon, because there the client comes as a ready wheel. Nothing is installed before one of those two answers. Give the
   command even when the operator asks you to do it: they must see what will run.
4. **Install only what was named, and only after the choice.** Python packages:
   `python3 scripts/macsetup.py fix`. Rust on Intel: the `rustup` command from the
   report, run either by the operator or by you if they said "ставь сам". A system
   toolchain deserves its own confirmation, so ask about Rust separately from the
   Python packages.
5. **No system permissions.** The session runs over the RDP protocol, so Accessibility
   and Screen Recording are not needed. Do not send the operator to System Settings.
6. **Explain the password rule.** The operator types the login (`DOMAIN\user`) and
   then the password themselves in the terminal, the password in a hidden prompt. These
   are the same credentials they use for the company VPN client. The password
   authorises the connection only and is never stored. Ask them to have both ready.
7. **Explain the warm-up and wait for their yes.** Ask them to open their usual RDP
   client and reach the desktop, then wait for confirmation. Do not run `lsof`,
   `netstat` or a port scan to prove it, those give false results on macOS. They open their usual RDP client (Windows App, Microsoft Remote Desktop or similar),
   connect, enter the password if the lock screen appears and reach the desktop. Only then does the skill connect.
8. **Run the check again.** When every line is `ok`, say so and offer to start.

If a step fails, stop and report it. Do not retry a connection blindly: a domain
account locks out and a session can hang on the logon screen.
