#!/usr/bin/env python3
"""rdpctl.py - a long-lived RDP session driven by an AI agent.

The session lives on an isolated Xvfb display, so the agent's synthetic input
never touches the operator's real desktop. The same display is streamed over
x11vnc into a viewer window, so the operator watches the work and can take over
by hand at any moment. The session survives between agent turns and is torn down
only by an explicit stop.

  rdpctl.py start [--profile NAME] [--no-viewer] [--no-keepalive] [--timeout 45]
  rdpctl.py status
  rdpctl.py shot [OUT.png] [--crop X Y W H] [--zoom N]
  rdpctl.py click X Y [--button N] [--double]
  rdpctl.py move X Y | drag X1 Y1 X2 Y2 | scroll up|down [N]
  rdpctl.py key ctrl+alt+Delete Return Escape
  rdpctl.py type "ascii text"        # Latin text, Windows layout must be EN
  rdpctl.py typeru "любой текст"     # Cyrillic via YCUKEN, Windows layout RU
  rdpctl.py lang | langtoggle        # layout indicator / switch with Alt+Shift
  rdpctl.py paste "text"             # clipboard, if redirection is not blocked
  rdpctl.py keepalive on|off         # mouse jiggler against the idle lock
  rdpctl.py state                    # desktop / locked / unknown + screenshot
  rdpctl.py unlock [--type-password] # clear the lock screen
  rdpctl.py reconnect                # restart the client, keep the session
  rdpctl.py winlist | logs [-n 40] | viewer
  rdpctl.py stop                     # only on the operator's explicit order

The profile is read from ~/.config/rdp-agent/<profile>.env (mode 600, never in git).
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
XCTL = HERE / "xctl.py"
XPASTE = HERE / "xpaste.py"
JIGGLE = HERE / "jiggle.py"
CONF_DIR = Path.home() / ".config" / "rdp-agent"
STATE_ROOT = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "rdp-agent"
REMMINA_DIR = Path.home() / ".local/share/remmina"

DEFAULTS = {
    "RDP_PORT": "3389",
    "RDP_DISPLAY": ":77",
    "RDP_VNC_PORT": "5977",
    "RDP_GEOMETRY": "1920x1080",
    "RDP_JIGGLE_INTERVAL": "90",
}


# ------------------------------------------------ Cyrillic without a clipboard
# On hosts where clipboard redirection is disabled by policy, the only way to
# enter Cyrillic is to switch the Windows layout to Russian and send US-layout
# keys according to YCUKEN. Value: character -> US key that produces it.
YCUKEN = {
    "ф": "a", "и": "b", "с": "c", "в": "d", "у": "e", "а": "f", "п": "g", "р": "h",
    "ш": "i", "о": "j", "л": "k", "д": "l", "ь": "m", "т": "n", "щ": "o", "з": "p",
    "й": "q", "к": "r", "ы": "s", "е": "t", "г": "u", "м": "v", "ц": "w", "ч": "x",
    "н": "y", "я": "z", "х": "[", "ъ": "]", "ж": ";", "э": "'", "б": ",", "ю": ".",
    "ё": "`",
}
RU_PUNCT = {  # character -> (US key, shift) in the Russian layout
    ".": ("/", False), ",": ("/", True), ";": ("4", True), ":": ("6", True),
    "?": ("7", True), '"': ("2", True), "№": ("3", True), "/": ("\\", True),
    "\\": ("\\", False),
}
# same in both layouts, shift comes from the live keymap (None)
RU_SAME = set("0123456789 -=()_+!%*")


def ru_keys(text):
    """Text -> list of (key, shift) plus characters the RU layout cannot type.

    shift=None means "plain ASCII, let the xctl charmap pick the shift state",
    anything else is a US key with an explicit shift state.
    """
    out, bad = [], []
    for ch in text:
        low = ch.lower()
        if low in YCUKEN:
            out.append((YCUKEN[low], ch != low))
        elif ch in RU_PUNCT:
            out.append(RU_PUNCT[ch])
        elif ch in RU_SAME:
            out.append((ch, None))
        elif ch == "\n":
            out.append(("Return", False))
        else:
            bad.append(ch)
    return out, bad


# --------------------------------------------------------------------- profile
def load_profile(name):
    path = CONF_DIR / f"{name}.env"
    if not path.exists():
        sys.exit(f"rdpctl: no such profile: {path}")
    if path.stat().st_mode & 0o077:
        sys.exit(f"rdpctl: {path} is world/group readable, run chmod 600 on it")
    conf = dict(DEFAULTS)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        conf[k.strip()] = v.strip()
    for req in ("RDP_HOST", "RDP_USER", "RDP_PASSWORD"):
        if not conf.get(req):
            sys.exit(f"rdpctl: profile {name} does not set {req}")
    conf["_name"] = name
    return conf


def owner_file(display):
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    return STATE_ROOT / f"display{display.lstrip(':')}.owner"


def claim_display(conf):
    """Claim the display for this profile. Two profiles on one :NN fight."""
    f = owner_file(conf["RDP_DISPLAY"])
    if f.exists():
        owner = f.read_text().strip()
        if owner and owner != conf["_name"]:
            sys.exit(f"rdpctl: display {conf['RDP_DISPLAY']} is owned by profile "
                     f"{owner}. Give profile {conf['_name']} its own RDP_DISPLAY "
                     f"and RDP_VNC_PORT.")
    f.write_text(conf["_name"])


def release_display(conf):
    f = owner_file(conf["RDP_DISPLAY"])
    if f.exists() and f.read_text().strip() == conf["_name"]:
        f.unlink()


def state_dir(name):
    d = STATE_ROOT / name
    for path in (STATE_ROOT, d, d / "shots"):
        path.mkdir(exist_ok=True)
        path.chmod(0o700)      # a strict umask created dirs without +x
    return d


def read_state(name):
    p = state_dir(name) / "session.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def write_state(name, st):
    (state_dir(name) / "session.json").write_text(json.dumps(st, indent=2))


# ------------------------------------------------------------------- processes
KEY_MARKERS = {                      # expected in /proc/<pid>/cmdline
    "xvfb_pid": "Xvfb",
    "rdp_pid": "freerdp",
    "vnc_pid": "x11vnc",
    "jiggle_pid": "jiggle.py",
    "viewer_pid": "remmina",
}


def alive(pid, marker=None):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    if marker:
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
        except OSError:
            return False
        return marker in cmd
    return True


def kill(pid, sig=signal.SIGTERM):
    try:
        os.kill(int(pid), sig)
    except (OSError, TypeError, ValueError):
        pass


PROXY_VARS = ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY",
               "all_proxy", "ALL_PROXY", "ftp_proxy", "FTP_PROXY")


def direct_env(display=None):
    """Proxy-free env: xfreerdp reads http_proxy itself and tunnels RDP into it."""
    env = {k: v for k, v in os.environ.items() if k not in PROXY_VARS}
    env["no_proxy"] = env["NO_PROXY"] = "*"
    if display:
        env["DISPLAY"] = display
    return env


def redact(cmd):
    """Strip anything secret-looking from a command line before logging it."""
    out = []
    for a in cmd:
        low = a.lower()
        if low.startswith("/p:") or low.startswith("/gp:") or "password" in low:
            a = a.split(":", 1)[0] + ":<redacted>" if ":" in a else "<redacted>"
        out.append(a)
    return " ".join(out)


def kill_tracked(st, keys, sig=signal.SIGTERM):
    """Kill only our own processes: PID plus a cmdline match."""
    for key in keys:
        if alive(st.get(key), KEY_MARKERS.get(key)):
            kill(st[key], sig)


def find_xvfb(display):
    """PID of an Xvfb already serving this display, if any."""
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            argv = (d / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if len(argv) > 1 and argv[0].endswith(b"Xvfb") and argv[1].decode() == display:
            return int(d.name)
    return None


def spawn(cmd, log_path, stdin_data=None, env=None):
    log = open(log_path, "ab", buffering=0)
    log.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} {redact(cmd)}\n".encode())
    p = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
        stdout=log, stderr=log, start_new_session=True, env=env,
    )
    if stdin_data is not None:
        try:
            p.stdin.write(stdin_data.encode())
            p.stdin.flush()
            p.stdin.close()
        except BrokenPipeError:
            pass
    return p


def port_ready(port, host="127.0.0.1", timeout=8.0):
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s_:
            s_.settimeout(0.5)
            if s_.connect_ex((host, int(port))) == 0:
                return True
        time.sleep(0.2)
    return False


def viewer_attached(port):
    """Is a viewer attached to x11vnc (a Remmina tab holds the connection)."""
    import re
    try:
        r = subprocess.run(["ss", "-tn", "state", "established"],
                           capture_output=True, text=True)
    except OSError:
        return False          # no ss: assume no viewer, a spare tab is harmless
    return bool(re.search(rf"(?:127\.0\.0\.1|\[::1\]):{int(port)}\b", r.stdout or ""))


def need(binary):
    if not shutil.which(binary):
        sys.exit(f"rdpctl: {binary} not found")


# ------------------------------------------------------------------------- X11
def xctl(conf, *args, capture=False, check=True):
    env = dict(os.environ, XCTL_DISPLAY=conf["RDP_DISPLAY"])
    r = subprocess.run([sys.executable, str(XCTL), *map(str, args)],
                       env=env, check=False,
                       capture_output=capture, text=True)
    if check and r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip() if capture else ""
        sys.exit(f"rdpctl: xctl {args[0]} failed: {msg}")
    return r


def x_ready(display):
    return subprocess.run(["xdpyinfo", "-display", display],
                          capture_output=True).returncode == 0


def rdp_window(conf):
    """Geometry of the xfreerdp window on the virtual display, if present."""
    r = xctl(conf, "winlist", capture=True, check=False)
    for line in (r.stdout or "").splitlines():
        low = line.lower()
        if "class='xfreerdp'" in low or "freerdp" in low:
            return line.strip()
    return None


# ----------------------------------------------------------------------- start
def session_ok(conf):
    st = read_state(conf["_name"])
    return (alive(st.get("xvfb_pid"), "Xvfb")
            and alive(st.get("rdp_pid"), "freerdp")
            and x_ready(conf["RDP_DISPLAY"]))


AUTH_MARKERS = ("LOGON_FAILURE", "ACCOUNT_LOCKED", "ACCOUNT_DISABLED",
                "PASSWORD_EXPIRED", "PASSWORD_MUST_CHANGE", "AUTHENTICATION_FAILED",
                "SEC_E_LOGON_DENIED", "ERRCONNECT_LOGON")


def auth_blocked(conf):
    """Did the last attempt fail on auth. Blind retries lock domain accounts."""
    st = read_state(conf["_name"])
    return st.get("auth_failed_at"), st.get("auth_error")


def launch_client(conf, sd, st, timeout=45):
    """Start xfreerdp on the virtual display and wait for its window."""
    name, disp = conf["_name"], conf["RDP_DISPLAY"]
    target = f"{conf['RDP_HOST']}:{conf['RDP_PORT']}"
    rdp_cmd = [
        "xfreerdp", f"/v:{target}", f"/u:{conf['RDP_USER']}",
        f"/size:{conf['RDP_GEOMETRY']}", "/from-stdin", "/cert:ignore",
        "+clipboard", "+auto-reconnect", "/auto-reconnect-max-retries:5",
        "/compression", "/bpp:24", "/log-level:INFO",
    ]
    if conf.get("RDP_DOMAIN"):
        rdp_cmd.insert(3, f"/d:{conf['RDP_DOMAIN']}")
    rdp_log = sd / "xfreerdp.log"
    rdp_log.write_bytes(b"")
    proc = spawn(rdp_cmd, rdp_log, stdin_data=conf["RDP_PASSWORD"] + "\n",
                 env=direct_env(disp))
    st["rdp_pid"] = proc.pid
    write_state(name, st)

    deadline = time.time() + float(timeout)
    win = None
    while time.time() < deadline:
        if proc.poll() is not None:
            log_text = rdp_log.read_text(errors="replace")
            tail = log_text.strip().splitlines()[-15:]
            bad = [m for m in AUTH_MARKERS if m in log_text.upper()]
            print("rdpctl: xfreerdp exited, the connection was not established.", file=sys.stderr)
            print("\n".join(tail), file=sys.stderr)
            if bad:
                st["auth_failed_at"] = datetime.now().isoformat(timespec="seconds")
                st["auth_error"] = bad[0]
                write_state(name, st)
                print(f"\nThis is an authentication failure ({bad[0]}). Do NOT retry "
                      "blindly, domain accounts lock out. Show this to the operator, "
                      "verify the password in the profile, then retry with --force.",
                      file=sys.stderr)
            else:
                print("\nLooks like a network problem rather than a password one. "
                      "Check the VPN and reachability of port 3389.", file=sys.stderr)
            sys.exit(2)
        win = rdp_window(conf)
        if win:
            break
        time.sleep(0.5)
    if not win:
        sys.exit(f"rdpctl: no RDP window after {timeout}s, see {rdp_log}")
    time.sleep(2.5)  # let the remote desktop paint
    return win


def reconnect_client(conf, timeout=45):
    """Restart only the RDP client; Xvfb, VNC and the viewer window stay up.

    Windows clears the lock itself when the client comes back with the same
    credentials over NLA, so no password has to be typed into the lock form.
    """
    name = conf["_name"]
    st = read_state(name)
    if alive(st.get("rdp_pid"), "freerdp"):
        kill(st["rdp_pid"])
        for _ in range(30):
            if not alive(st.get("rdp_pid"), "freerdp"):
                break
            time.sleep(0.2)
        else:
            kill(st["rdp_pid"], signal.SIGKILL)
            time.sleep(0.5)
    return launch_client(conf, state_dir(name), st, timeout)


def cmd_start(conf, args):
    name = conf["_name"]
    sd = state_dir(name)
    if session_ok(conf):
        print("Session is already up, reusing it.")
        if args.no_keepalive:
            stop_jiggle(conf)
        else:
            start_jiggle(conf)
        return cmd_status(conf, args)

    when, why = auth_blocked(conf)
    if when and not args.force:
        sys.exit(f"rdpctl: authentication failed at {when} ({why}). Logging in "
                 f"again without checking the password may lock the account. "
                 f"Ask the operator, then run with --force.")

    claim_display(conf)
    for b in ("Xvfb", "xfreerdp", "xdpyinfo", "import"):
        need(b)
    st = read_state(name)
    killed_xvfb = alive(st.get("xvfb_pid"), KEY_MARKERS["xvfb_pid"])
    kill_tracked(st, ("jiggle_pid", "viewer_pid", "vnc_pid", "rdp_pid", "xvfb_pid"))
    time.sleep(0.3)

    disp = conf["RDP_DISPLAY"]
    if killed_xvfb:                       # else we may store an already dead PID
        for _ in range(20):
            if not x_ready(disp):
                break
            time.sleep(0.2)
    w, h = conf["RDP_GEOMETRY"].split("x")
    sock = Path(f"/tmp/.X11-unix/X{disp.lstrip(':')}")
    if sock.exists() and not x_ready(disp):
        sys.exit(f"rdpctl: socket {sock} belongs to another X server, change RDP_DISPLAY")

    st = {"display": disp, "vnc_port": conf["RDP_VNC_PORT"],
          "geometry": conf["RDP_GEOMETRY"], "host": conf["RDP_HOST"],
          "user": conf["RDP_USER"], "started": datetime.now().isoformat(timespec="seconds")}

    if not x_ready(disp):
        p = spawn(["Xvfb", disp, "-screen", "0", f"{w}x{h}x24", "-nolisten", "tcp"],
                  sd / "xvfb.log")
        st["xvfb_pid"] = p.pid
        for _ in range(50):
            if x_ready(disp):
                break
            time.sleep(0.2)
        else:
            sys.exit(f"rdpctl: Xvfb did not start, see {sd/'xvfb.log'}")
    else:
        st["xvfb_pid"] = read_state(name).get("xvfb_pid") or find_xvfb(disp)
        if not st["xvfb_pid"]:
            sys.exit(f"rdpctl: {disp} already has an X server that is not this "
                     f"profile's Xvfb. Change RDP_DISPLAY or stop that server.")

    win = launch_client(conf, sd, st, args.timeout)

    if shutil.which("x11vnc"):
        v = spawn(["x11vnc", "-display", disp, "-rfbport", conf["RDP_VNC_PORT"],
                   "-localhost", "-nopw", "-forever", "-shared", "-noxdamage",
                   "-repeat", "-quiet"], sd / "x11vnc.log", env=direct_env(disp))
        st["vnc_pid"] = v.pid
        if not port_ready(conf["RDP_VNC_PORT"]):
            print(f"rdpctl: x11vnc did not open port {conf['RDP_VNC_PORT']}, "
                  f"see {sd/'x11vnc.log'}", file=sys.stderr)
    write_state(name, st)

    st.pop("auth_failed_at", None)
    st.pop("auth_error", None)
    write_state(name, st)
    if args.no_keepalive:
        st["keepalive"] = False
        write_state(name, st)
    else:
        st = start_jiggle(conf, st)
    if not args.no_viewer:
        open_viewer(conf, st)

    shot = take_shot(conf, None)
    print(f"Session is up. RDP window {win}")
    print("Note: this takes over the same Windows session the operator uses. "
          "If they were logged in interactively, they were just disconnected.")
    print(f"Display {disp}, VNC 127.0.0.1:{conf['RDP_VNC_PORT']}, screenshot {shot}")


def open_viewer(conf, st=None, force=False):
    st = st or read_state(conf["_name"])
    if not force and viewer_attached(conf["RDP_VNC_PORT"]):
        print("A viewer is already attached, not opening a second one.")
        return
    if not shutil.which("remmina"):
        print("remmina not found, watch the session with any VNC client on "
              f"127.0.0.1:{conf['RDP_VNC_PORT']}")
        return
    REMMINA_DIR.mkdir(parents=True, exist_ok=True)
    prof = REMMINA_DIR / f"rdp_agent_{conf['_name']}.remmina"
    prof.write_text(
        "[remmina]\n"
        f"name=RDP agent {conf['_name']} ({conf['RDP_HOST']})\n"
        "protocol=VNC\n"
        f"server=127.0.0.1:{conf['RDP_VNC_PORT']}\n"
        "username=\npassword=\n"
        "colordepth=32\nquality=9\nviewonly=0\ndisableencryption=1\n"
        "scale=1\naspectscale=1\nviewmode=1\nkeymap=\n"
        "window_width=1600\nwindow_height=900\nwindow_maximize=0\n"
        "disableclipboard=0\nshowcursor=1\n", encoding="utf-8")
    p = spawn(["remmina", "-c", str(prof)], state_dir(conf["_name"]) / "viewer.log")
    st["viewer_pid"] = p.pid
    write_state(conf["_name"], st)
    print(f"Viewer window opened (Remmina, profile {prof.name}).")


# ---------------------------------------------------------------------- status
def start_jiggle(conf, st=None, quiet=False):
    """Keep the remote session active while the agent session is alive.

    The choice is stored as `keepalive` in session.json, so ensure_jiggle can
    bring the jiggler back when its process is gone.
    """
    name = conf["_name"]
    st = st if st is not None else read_state(name)
    st["keepalive"] = True
    if alive(st.get("jiggle_pid"), KEY_MARKERS["jiggle_pid"]):
        write_state(name, st)
        if not quiet:
            print("Mouse jiggler is already running.")
        return st
    p = spawn([sys.executable, str(JIGGLE), conf["RDP_DISPLAY"],
               conf["RDP_JIGGLE_INTERVAL"]], state_dir(name) / "jiggle.log")
    st["jiggle_pid"] = p.pid
    write_state(name, st)
    if not quiet:
        print(f"Mouse jiggler started, every {conf['RDP_JIGGLE_INTERVAL']}s "
              f"(pid {p.pid}). This is synthetic input into the remote session, for a "
              f"look-only run use start --no-keepalive.")
    return st


def stop_jiggle(conf):
    st = read_state(conf["_name"])
    if alive(st.get("jiggle_pid"), KEY_MARKERS["jiggle_pid"]):
        kill(st["jiggle_pid"])
        print("Mouse jiggler stopped.")
    else:
        print("Mouse jiggler is not running.")
    st.pop("jiggle_pid", None)
    st["keepalive"] = False
    write_state(conf["_name"], st)


def ensure_jiggle(conf):
    """Restart the jiggler when keepalive is on but its process is gone.

    A session started once with --no-keepalive, or a jiggler that exited, used to
    stay without keepalive until someone noticed: `start` on a live session only
    printed the status. The remote side then fell into the idle lock and the next
    typed text landed in the logon form as a password attempt.
    """
    st = read_state(conf["_name"])
    if st.get("keepalive", True) and not alive(st.get("jiggle_pid"), KEY_MARKERS["jiggle_pid"]):
        st = start_jiggle(conf, st, quiet=True)
        print(f"rdpctl: keepalive is on but the mouse jiggler was not running, "
              f"restarted it (pid {st['jiggle_pid']})", file=sys.stderr)


def cmd_status(conf, args):
    name = conf["_name"]
    st = read_state(name)
    ok = session_ok(conf)
    win = rdp_window(conf) if ok else None
    if ok and not win:
        state_word = "CLIENT ALIVE, NO WINDOW (reconnecting?)"
    else:
        state_word = "UP" if ok else "NOT CONNECTED"
    print(f"profile    {name} -> {conf['RDP_USER']}@{conf['RDP_HOST']}:{conf['RDP_PORT']}")
    print(f"state      {state_word}")
    print(f"display    {conf['RDP_DISPLAY']}  ({conf['RDP_GEOMETRY']})")
    when, why = auth_blocked(conf)
    if when:
        print(f"WARNING    auth failed at {when} ({why}), start needs --force")
    print(f"xvfb       pid={st.get('xvfb_pid')} {'ok' if alive(st.get('xvfb_pid'),'Xvfb') else 'down'}")
    print(f"xfreerdp   pid={st.get('rdp_pid')} {'ok' if alive(st.get('rdp_pid'),'freerdp') else 'down'}")
    print(f"x11vnc     pid={st.get('vnc_pid')} {'ok' if alive(st.get('vnc_pid'),'x11vnc') else 'down'}"
          f"  port 127.0.0.1:{conf['RDP_VNC_PORT']}")
    print(f"viewer     {'attached' if viewer_attached(conf['RDP_VNC_PORT']) else 'closed (rdpctl.py viewer)'}")
    if alive(st.get("jiggle_pid"), KEY_MARKERS["jiggle_pid"]):
        jiggler = f"on, every {conf['RDP_JIGGLE_INTERVAL']}s"
    elif ok and st.get("keepalive", True):
        jiggler = "DOWN although keepalive is on, the next command restarts it"
    else:
        jiggler = "off (rdpctl.py keepalive on)"
    print(f"jiggler    {jiggler}")
    if ok:
        print(f"rdp window {win or 'not found'}")
    log = state_dir(name) / "xfreerdp.log"
    if ok and log.exists():
        tail = log.read_text(errors="replace").upper()[-4000:]
        bad = [m for m in ("ERRCONNECT", "AUTO RECONNECT", "DISCONNECT PROVIDER",
                           "NETWORK ERROR") if m in tail]
        if bad:
            print(f"log says   {', '.join(bad).lower()}, the frame may be stale, "
                  f"take a fresh shot")
    print(f"logs       {state_dir(name)}")
    return 0 if ok else 1


def require_session(conf, keepalive=True):
    if not session_ok(conf):
        sys.exit("rdpctl: no session, run `rdpctl.py start` first")
    if keepalive:
        ensure_jiggle(conf)


def type_ru(conf, text):
    """Type Cyrillic with US-layout keys, the Windows layout must be RU."""
    keys, bad = ru_keys(text)
    if bad:
        print(f"rdpctl: the RU layout cannot type {''.join(sorted(set(bad)))!r}, "
              f"these characters were skipped", file=sys.stderr)
    os.environ["XCTL_DISPLAY"] = conf["RDP_DISPLAY"]
    sys.path.insert(0, str(HERE))
    import xctl  # noqa: E402  - same XTEST, without a process per character
    c = xctl.Ctl()
    for key, shift in keys:
        if shift is None:
            c.type_text(key)          # ( ) _ + ! % * and digits - shift from the map
        else:
            c.key(f"shift+{key}" if shift else key)
    print(f"typed {len(keys)} characters")


def screen_state(conf, shot=None):
    """What is on screen: the desktop, the lock screen, or something unclear.

    The signal is cheap: on the desktop the bottom rows are the near-black
    taskbar, on the logon screen they are wallpaper. A full-screen window also
    produces a non-black bottom, so an unknown verdict means "look at the
    screenshot", never "type the password".
    """
    shot = shot or take_shot(conf, None)
    try:
        from PIL import Image
    except ImportError:
        return "unknown", None, shot
    im = Image.open(shot).convert("L")
    w, h = im.size
    px = im.load()
    strip = [px[x, y] for y in range(h - 8, h) for x in range(0, w, 3)]
    dark = sum(1 for v in strip if v < 45) / len(strip)
    verdict = "desktop" if dark > 0.85 else "locked" if dark < 0.35 else "unknown"
    return verdict, dark, shot


def type_password(conf):
    """Type the password into the lock form. It is never printed or logged."""
    os.environ["XCTL_DISPLAY"] = conf["RDP_DISPLAY"]
    sys.path.insert(0, str(HERE))
    import xctl
    c = xctl.Ctl()
    c._build_charmap()
    if any(ch not in c._charmap for ch in conf["RDP_PASSWORD"]):
        sys.exit("rdpctl: the password contains a character the display keymap "
                 "cannot produce. NOTHING WAS TYPED, no login attempt was spent.")
    c.key("Escape")          # wake the logon screen
    time.sleep(0.8)
    c.type_text(conf["RDP_PASSWORD"])
    time.sleep(0.3)
    c.key("Return")


def cmd_unlock(conf, args):
    """Clear the lock screen without dropping the session."""
    verdict, dark, shot = screen_state(conf)
    print(f"before: {verdict} (dark bottom {dark if dark is None else round(dark, 3)}), {shot}")
    if verdict == "desktop" and "--force" not in args:
        print("Screen is not locked, doing nothing.")
        return
    print("Restarting the client, Windows usually clears the lock itself over NLA.")
    reconnect_client(conf)
    time.sleep(2.0)
    verdict, dark, shot = screen_state(conf)
    print(f"after reconnect: {verdict} "
          f"(dark bottom {dark if dark is None else round(dark, 3)}), {shot}")
    if verdict == "desktop":
        print("Session unlocked and still alive.")
        return
    if "--type-password" in args and "--operator-confirmed" not in args:
        print("Typing the password needs the operator's confirmation. Show them the "
              "screenshot, confirm it is the Windows logon form and the layout says "
              "ENG, then repeat with --type-password --operator-confirmed.")
        sys.exit(4)
    if "--type-password" not in args:
        print("Still locked. Show the screenshot to the operator: if it is the "
              "logon form and the layout indicator says ENG, repeat with "
              "--type-password --operator-confirmed.")
        sys.exit(3)
    if verdict != "locked":
        sys.exit("rdpctl: the screen is not recognised as a logon form, refusing to type")
    print("Typing the password into the logon form (one attempt).")
    type_password(conf)
    time.sleep(6.0)
    verdict, dark, shot = screen_state(conf)
    print(f"result: {verdict} (dark bottom {dark if dark is None else round(dark, 3)}), {shot}")
    if verdict != "desktop":
        sys.exit("rdpctl: login did not go through. Do not retry blindly, the "
                 "account locks out. Show the screenshot to the operator.")
    print("Session unlocked.")


def take_shot(conf, out, crop=None, zoom=None):
    """Screenshot. The caller prints the path, xctl stays quiet (was doubled)."""
    if not out:
        out = str(state_dir(conf["_name"]) / "shots"
                   / f"{datetime.now():%Y%m%d-%H%M%S-%f}.png")  # seconds alone collided
    a = ["shot", out]
    if crop:
        a += ["--crop", *map(str, crop)]
    xctl(conf, *a, capture=True)
    if zoom and float(zoom) != 1:
        subprocess.run(["convert", out, "-filter", "point",
                        "-resize", f"{float(zoom) * 100:g}%", out], check=False)
    return out


# ------------------------------------------------------------------------ main
def main():
    # macOS has no X server: hand over to the native implementation, the Linux
    # path below stays exactly as it is.
    if sys.platform == "darwin":
        # macOS has no X display: the session is driven over the RDP protocol and
        # shown in a Tk window, so every argument goes to rdpview.py
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rdpview.py")
        os.execv(sys.executable, [sys.executable, script] + sys.argv[1:])

    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--profile", default=os.environ.get("RDP_PROFILE", "default"))
    ap.add_argument("--timeout", default=45)
    ap.add_argument("--no-viewer", action="store_true")
    ap.add_argument("--no-keepalive", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("cmd", nargs="?")
    ap.add_argument("rest", nargs=argparse.REMAINDER)
    if len(sys.argv) == 1 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return
    # argparse.REMAINDER swallows everything after the command, so flags that
    # take a value are pulled out of argv by hand: `start --profile X` used to
    # silently fall back to the default profile.
    argv = sys.argv[1:]
    for flag in ("--profile", "--timeout"):
        while flag in argv:
            i = argv.index(flag)
            if i + 1 >= len(argv):
                sys.exit(f"rdpctl: {flag} needs a value")
            ap.set_defaults(**{flag[2:]: argv[i + 1]})
            del argv[i:i + 2]
    args = ap.parse_args(argv)
    for flag in ("no_viewer", "no_keepalive", "force"):
        if f"--{flag.replace('_', '-')}" in args.rest:
            setattr(args, flag, True)
    conf = load_profile(args.profile)
    cmd, r = args.cmd, args.rest

    if cmd == "start":
        cmd_start(conf, args)
    elif cmd == "status":
        sys.exit(cmd_status(conf, args))
    elif cmd == "viewer":
        require_session(conf)
        open_viewer(conf, force="--force" in r)
    elif cmd == "shot":
        require_session(conf)
        out = r[0] if r and not r[0].startswith("--") else None
        crop = None
        if "--crop" in r:
            i = r.index("--crop")
            crop = r[i + 1:i + 5]
        zoom = r[r.index("--zoom") + 1] if "--zoom" in r else None
        print(take_shot(conf, out, crop, zoom))
    elif cmd in ("click", "move", "drag", "scroll", "key", "type", "winlist", "place"):
        require_session(conf)
        xctl(conf, cmd, *r)
    elif cmd == "keepalive":
        require_session(conf, keepalive=False)
        if r and r[0] in ("off", "stop"):
            stop_jiggle(conf)
        else:
            start_jiggle(conf)
    elif cmd == "unlock":
        require_session(conf)
        cmd_unlock(conf, r)
    elif cmd == "reconnect":
        require_session(conf)
        print(reconnect_client(conf))
        print(take_shot(conf, None))
    elif cmd == "state":
        require_session(conf)
        verdict, dark, shot = screen_state(conf)
        print(f"{verdict} (dark bottom {dark if dark is None else round(dark, 3)})")
        print(shot)
    elif cmd == "typeru":
        require_session(conf)
        if not r:
            sys.exit("rdpctl: typeru needs text")
        type_ru(conf, r[0])
    elif cmd == "lang":
        require_session(conf)
        crop = conf.get("RDP_LANG_CROP", "1540 1040 200 35").split()
        print(take_shot(conf, None, crop, zoom=4))
        print("Read the indicator on the screenshot: EN or ENG is Latin, RU is Cyrillic.")
    elif cmd == "langtoggle":
        require_session(conf)
        xctl(conf, "key", "alt+Shift_L")
        time.sleep(0.8)
        crop = conf.get("RDP_LANG_CROP", "1540 1040 200 35").split()
        print(take_shot(conf, None, crop, zoom=4))
    elif cmd == "paste":
        require_session(conf)
        if not r:
            sys.exit("rdpctl: paste needs text")
        env = dict(os.environ, XCTL_DISPLAY=conf["RDP_DISPLAY"])
        pr = subprocess.Popen([sys.executable, str(XPASTE), "-", "20"],
                              env=env, stdin=subprocess.PIPE,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, start_new_session=True)
        pr.stdin.write(r[0].encode())      # not via argv, that shows up in ps
        pr.stdin.close()
        time.sleep(0.8)
        xctl(conf, "key", "ctrl+v")
        print("pasted via the clipboard")
    elif cmd == "logs":
        n = int(r[r.index("-n") + 1]) if "-n" in r else 40
        for f in ("xfreerdp.log", "x11vnc.log", "xvfb.log"):
            p = state_dir(conf["_name"]) / f
            if p.exists():
                print(f"--- {f}")
                print("\n".join(p.read_text(errors="replace").splitlines()[-n:]))
    elif cmd == "stop":
        st = read_state(conf["_name"])
        kill_tracked(st, ("jiggle_pid", "viewer_pid", "vnc_pid", "rdp_pid", "xvfb_pid"))
        time.sleep(0.5)
        kill_tracked(st, ("vnc_pid", "rdp_pid", "xvfb_pid"), signal.SIGKILL)
        write_state(conf["_name"], {})
        release_display(conf)
        print("Session closed. The Windows session stays in disconnected state. "
              "The Remmina tab outlives it and has to be closed by hand.")
    else:
        sys.exit(f"rdpctl: unknown command {cmd!r}, see --help")


if __name__ == "__main__":
    main()
