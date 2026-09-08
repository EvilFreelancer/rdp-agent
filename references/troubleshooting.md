# Troubleshooting

Symptoms seen in real runs, with the cause and the fix. Everything here was
reproduced on a live host, not derived from documentation.

## The client exits immediately with ERRCONNECT_CONNECT_TRANSPORT_FAILED

The shell exports `http_proxy` / `https_proxy`, FreeRDP reads them on its own and
tunnels the RDP connection through the proxy. Against a host that is only reachable
directly (a VPN route, an internal network) this fails and looks exactly like an
unreachable host.

`rdpctl` strips the proxy variables for `xfreerdp` and `x11vnc`. A hand-run client
needs the same:

```bash
env -u http_proxy -u https_proxy -u all_proxy -u HTTP_PROXY -u HTTPS_PROXY xfreerdp /v:host /u:user
```

Distinguish it from a real network problem by checking the port first:

```bash
timeout 5 bash -c 'cat < /dev/null > /dev/tcp/HOST/3389' && echo open || echo unreachable
```

## Connection fails and the account is at risk

FreeRDP prints `ERRCONNECT_LOGON_FAILURE`, `ACCOUNT_LOCKED_OUT`, `PASSWORD_EXPIRED`
and friends in its log. `rdpctl` recognises them, records the failure in
`session.json` and refuses the next `start` until `--force` is given. Do not remove
that guard and do not pass `--force` on the agent's own initiative, a few blind
retries lock a domain account for everyone.

Note that FreeRDP's auto-reconnect is not the danger here: it resumes an already
established session with a server-issued cookie, it does not replay the password.
The danger is a stale password in the profile plus a retry loop.

## The session is locked, the connection is alive

Restart the client, do not type anything:

```bash
python3 scripts/rdpctl.py unlock
```

Windows clears the lock when the client returns with the same credentials over NLA,
and every open window survives. Typing into the logon form is a fallback for hosts
where policy forbids that, it needs `--type-password --operator-confirmed`, and it
must never run with a Cyrillic keyboard layout active, that produces a failed login
attempt.

## `state` says `desktop` but clicks do nothing

The verdict is a pixel heuristic over the bottom rows of the screen, and a dark
wallpaper on the logon screen fools it. Brightness spread does not separate the two
cases, this was measured (standard deviation 14.5 on the desktop against 13.6 on
the lock screen). When actions produce no visible change, call `unlock --force`
regardless of the verdict.

## Clipboard transfer does nothing in either direction

Group policy on hardened images disables RDP clipboard redirection. Test it by
copying something in the remote session and checking whether anything owns the X
clipboard on the virtual display:

```bash
python3 -c "from Xlib import display; d=display.Display(':77'); print(d.get_selection_owner(d.intern_atom('CLIPBOARD')))"
```

Owner `0` after a copy on the remote side means the channel is dead. Use `typeru`
for Cyrillic and `type` for Latin instead.

## Typed Cyrillic comes out as Latin garbage or vice versa

The agent sends key codes, the character depends on the layout active in the target
window, and Windows keeps the layout per window. Check with `lang`, switch with
`langtoggle`, and remember that the layout can be different in the window you just
focused.

## The viewer window does not open

Two causes seen so far. First, the viewer was launched before `x11vnc` had opened
its port, which `rdpctl` now waits for. Second, Remmina runs as a single instance
and opens connections as tabs, so a "missing window" may be a tab behind another
one. `status` reports the viewer as attached based on a live TCP connection to the
VNC port, which is the only reliable signal.

## The whole shell dies while cleaning up

`pkill -f 'xfreerdp /v:host'` matches the command line of the shell running it and
kills that shell. Kill by PID from `session.json`, or use `pkill -x xfreerdp`.

## Screenshots overwrite each other

Fixed by putting microseconds into the file name. If you patch the naming, keep
sub-second resolution, two shots within one second are normal in an agent loop.
