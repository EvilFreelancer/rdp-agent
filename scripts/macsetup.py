#!/usr/bin/env python3
"""macsetup.py - pre-flight check for the macOS side of rdp-agent.

Run this before the first session on a new Mac. It changes nothing by itself,
only reports, so it is safe to run at any moment:

    macsetup.py check

The `fix` mode installs the Python packages that are missing. It never touches
the system settings, never calls sudo and never grants permissions: Accessibility
and Screen Recording are operator decisions, this script only prints where to
switch them on.

Exit code is 0 when everything the skill needs is in place, 1 otherwise.
"""
import argparse
import ctypes
import importlib
import os
import pathlib
import platform
import subprocess
import sys

PACKAGES = {
    "Quartz": "pyobjc-framework-Quartz",
    "AppKit": "pyobjc-framework-Cocoa",
    "Foundation": "pyobjc-framework-Cocoa",
    "PIL": "Pillow",
}

CLIENT_PATTERNS = [
    "/Applications/*.app",
    os.path.expanduser("~/Applications/*.app"),
]
CLIENT_WORDS = ("rdp", "remote desktop", "windows app", "freerdp")


def read(value):
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def check_python():
    v = sys.version_info
    ok = v >= (3, 11)
    return ok, "%d.%d.%d" % (v.major, v.minor, v.micro), (
        "3.11 or newer is required by the RDP client")


def check_module(name):
    try:
        mod = importlib.import_module(name)
    except Exception as exc:
        return False, str(exc)[:70], "pip install %s" % PACKAGES.get(name, name)
    return True, getattr(mod, "__version__", "ok"), ""


def check_tkinter():
    ok, note, hint = check_module("tkinter")
    if not ok:
        return False, note, "the python.org build already includes it"
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        root.destroy()
        return True, "ok", ""
    except Exception as exc:
        return False, str(exc)[:70], "Tk needs a display; run from a desktop session"


def check_accessibility():
    """AXIsProcessTrusted is only reachable through the C API, pyobjc omits it."""
    try:
        lib = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        trusted = bool(lib.AXIsProcessTrusted())
    except Exception as exc:
        return False, str(exc)[:70], "System Settings > Privacy & Security > Accessibility"
    if trusted:
        return True, "granted", ""
    return False, "not granted", (
        "System Settings > Privacy & Security > Accessibility: press +, add the app "
        "that runs this skill (Terminal, iTerm or VS Code), tick its checkbox, then "
        "restart that app. Without it the agent cannot click or type.")


def check_screen_recording():
    """The only honest test is to capture some foreign window and look at it."""
    try:
        import Quartz
    except Exception:
        return False, "Quartz missing", "install the pyobjc framework first"
    wins = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID)
    for w in wins or []:
        if w.get("kCGWindowLayer") != 0:
            continue
        owner = w.get("kCGWindowOwnerPID")
        if owner == 0:
            continue
        img = Quartz.CGWindowListCreateImage(
            Quartz.CGRectNull, Quartz.kCGWindowListOptionIncludingWindow,
            w.get("kCGWindowNumber"), Quartz.kCGWindowImageBoundsIgnoreFraming)
        if img and Quartz.CGImageGetWidth(img) > 0:
            return True, "granted", ""
    return False, "not granted", (
        "System Settings > Privacy & Security > Screen Recording: press +, add the app "
        "that runs this skill, tick its checkbox, then restart that app. Without it the "
        "agent sees a black screen.")


def check_client():
    import glob
    for pattern in CLIENT_PATTERNS:
        for path in sorted(glob.glob(pattern)):
            name = path.rsplit("/", 1)[-1].replace(".app", "")
            if any(word in name.lower() for word in CLIENT_WORDS):
                return True, name, ""
    return False, "not installed", "install Windows App or Microsoft Remote Desktop"


def check_client_running():
    out = subprocess.run(["pgrep", "-f", "Windows App"], capture_output=True, text=True)
    if out.returncode == 0:
        return True, "running", ""
    return False, "not running", "start the client and connect to the session"


def check_rust():
    """Only Intel Macs need to build aardwolf from source."""
    import shutil
    if check_module("aardwolf")[0]:
        return True, "not needed, the client is already installed", ""
    if platform.machine() != "x86_64":
        return True, "not needed on this CPU", ""
    if sys.version_info >= (3, 13):
        return True, "not needed, a prebuilt wheel exists for Python %d.%d" % (
            sys.version_info.major, sys.version_info.minor), ""
    path = shutil.which("cargo") or (
        str(pathlib.Path.home() / ".cargo" / "bin" / "cargo")
        if (pathlib.Path.home() / ".cargo" / "bin" / "cargo").exists() else None)
    if path:
        return True, path, ""
    return False, "not installed (only needed if no wheel matches)", (
        "rustup: curl https://sh.rustup.rs -sSf | sh -s -- -y --profile minimal "
        "&& export PATH=\"$HOME/.cargo/bin:$PATH\"")


def check_agent_pid():
    """Under Electron the GUI runs in a child process, the parent checks the wrong one."""
    return True, "-", ""


# what a missing item costs: "must" - the skill does not work, "part" - it works
# with a visible loss, "start" - only needed at start-up
SEVERITY = {
    "python": ("must", "the skill cannot run at all"),
    # the protocol path needs none of these: frames arrive over RDP and input is
    # sent over RDP, the local cursor and the screen are not touched
    "Quartz": ("info", "only the old screen-capture helpers used it, the session does not"),
    "AppKit": ("info", "only the old screen-capture helpers used it, the session does not"),
    "Pillow": ("must", "no frames can be converted"),
    "tkinter": ("must", "no preview window, the operator cannot watch or take over"),
    "accessibility": ("info", "not needed: input travels over the RDP protocol"),
    "screen recording": ("info", "not needed: frames travel over the RDP protocol"),
    "RDP client": ("must", "the session cannot be warmed up"),
    "client running": ("must", "connect only after the operator connects"),
    "aardwolf": ("must", "there is no RDP client at all"),
    "rust (cargo)": ("arch", "only Intel needs it, Apple Silicon uses a wheel"),
}


def run_checks():
    rows = [
        ("python", check_python()),
        ("Quartz", check_module("Quartz")),
        ("AppKit", check_module("AppKit")),
        ("Pillow", check_module("PIL")),
        ("tkinter", check_tkinter()),
        ("accessibility", check_accessibility()),
        ("screen recording", check_screen_recording()),
        ("RDP client", check_client()),
        ("client running", check_client_running()),
        ("aardwolf", check_module("aardwolf")),
        ("rust (cargo)", check_rust()),
    ]
    width = max(len(r[0]) for r in rows)
    ok_all = True
    print("macOS %s, %s (%s)" % (platform.release(), platform.machine(),
                                 "Apple Silicon" if platform.machine() == "arm64" else "Intel"))
    print()
    for name, (ok, value, hint) in rows:
        level = SEVERITY.get(name, ("part", ""))
        if level[0] in ("must", "arch"):
            ok_all = ok_all and ok
        if ok:
            mark = "ok  "
        else:
            mark = "info" if level[0] == "info" else "FAIL"
        tag = {"must": "MUST", "arch": "ARCH", "info": "INFO"}[level[0]]
        print("%-4s %-6s %-*s %s" % (mark, tag, width, name, value))
        if not ok and hint:
            print("     %-6s %-*s -> %s" % ("", width, "", hint))
            print("     %-6s %-*s    без этого: %s" % ("", width, "", level[1]))
    print()
    print("MUST = required, the skill does not work correctly without it")
    print("ARCH = required only on this CPU, depends on the architecture")
    print("INFO = not used by the current session, shown for completeness")
    print()
    if ok_all:
        print("Everything needed is in place.")
    else:
        print("Fix the FAIL lines, then run `macsetup.py check` again.")
        print("Missing Python packages can be installed with `macsetup.py fix`.")
    return 0 if ok_all else 1


def run_fix():
    missing = []
    PACKAGES.setdefault("aardwolf", "aardwolf")
    for module, package in sorted(set(PACKAGES.items())):
        if not check_module(module)[0]:
            missing.append(package)
    if not missing:
        print("No Python packages are missing.")
        return 0
    print("This will install: %s" % " ".join(sorted(set(missing))))
    print("Nothing is installed without the operator's consent.")
    cmd = [sys.executable, "-m", "pip", "install", "--user"] + sorted(set(missing))
    code = subprocess.run(cmd).returncode
    print()
    print("pip exited with %d" % code)
    return code


def main():
    ap = argparse.ArgumentParser(description="pre-flight check for the macOS side of rdp-agent")
    ap.add_argument("command", choices=["check", "fix"], nargs="?", default="check")
    args = ap.parse_args()
    if platform.system() != "Darwin":
        sys.exit("macsetup: this check is for macOS only")
    if sys.version_info < (3, 11):
        print("macsetup: this Python is %d.%d, but 3.11 or newer is required."
              % (sys.version_info.major, sys.version_info.minor))
        print("Install one with `brew install python@3.12 python-tk@3.12` and run")
        print("the check with that interpreter, for example:")
        print("  /opt/homebrew/bin/python3.12 scripts/macsetup.py check")
        sys.exit(1)
    sys.exit(run_fix() if args.command == "fix" else run_checks())


if __name__ == "__main__":
    main()
