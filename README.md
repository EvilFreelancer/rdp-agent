# rdp-agent

An agent skill that lets an AI coding agent **drive a remote Windows machine over
RDP** in a session a human can watch and take over at any moment. It is a portable
skill: install it in Claude Code, Cursor, Codex or Coddy, or drop the folder into
any agent that reads `SKILL.md`.

The problem it solves: agents are good at shells and APIs, and helpless in front of
a GUI that only exists on someone else's desktop. Ready-made "computer use" servers
either need an agent installed on the target (impossible on a locked-down corporate
image) or hijack the operator's own screen. This skill takes the third path, it
runs a real RDP client on an isolated virtual display and drives it with synthetic
X input. On macOS there is no X server: the client runs headless over the same
protocol, see the macOS section below.

## How it works

```
xfreerdp -> Xvfb :77 (1920x1080) -> x11vnc 127.0.0.1:5977 -> viewer window
              ^
              XTEST: clicks and keys from the agent
```

- **Nothing is installed on the target.** Only the RDP protocol is used, so the
  skill works on hosts where you cannot deploy anything;
- **The operator's desktop is untouched.** Synthetic input goes to the virtual
  display, the real mouse and keyboard focus stay with the human;
- **The human is in the loop.** The same display is streamed over VNC into a normal
  window, so the operator watches the work and can grab the mouse;
- **The session is long-lived.** It survives between agent turns, so the agent can
  stop, ask a question, and come back to the same screen. It closes only on an
  explicit order.

## Install

The skill is distributed through the
[rpa-skills](https://github.com/EvilFreelancer/rpa-skills) catalog, a plugin
marketplace for Claude Code and Codex, and it also installs as a plain skill folder
for Cursor, Coddy and anything else that reads `SKILL.md`.

**As a plugin.** Add the catalog once, then install this skill from it:

```text
/plugin marketplace add EvilFreelancer/rpa-skills
/plugin install rdp-agent@rpa-skills
```

```bash
codex plugin marketplace add EvilFreelancer/rpa-skills
```

**In Coddy**: the rpa-skills catalog is built into [Coddy](https://coddy.dev) as a system source,
so there is nothing to connect. One sync installs this skill together with the rest of the collection
into `~/.coddy/skills/rdp-agent/` and keeps it up to date (or press **Sync all** in Settings → Skills):

```bash
coddy skills sync
```

**As a skill folder.** Clone the repository and symlink it into every agent you use,
the directory name has to match the `name` field in `SKILL.md`:

```bash
git clone https://github.com/EvilFreelancer/rdp-agent.git ~/Repository/skills/rdp-agent
```

```bash
for d in ~/.claude/skills ~/.cursor/skills ~/.codex/skills ~/.coddy/skills; do
  mkdir -p "$d" && ln -sfn ~/Repository/skills/rdp-agent "$d/rdp-agent"
done
```

The per-agent plugin manifests live in `.claude-plugin/`, `.codex-plugin/` and
`.cursor-plugin/`.

Dependencies on the operator's Linux box:

```bash
sudo apt install freerdp2-x11 xvfb x11-utils imagemagick python3-xlib x11vnc remmina
```

Dependencies on the operator's Mac:

```bash
python3 scripts/macsetup.py check     # reports what is missing, changes nothing
python3 scripts/macsetup.py fix       # installs the Python packages it named
```

`aardwolf` is the RDP client and is installed by `fix`. No system permissions are
needed: the session does not capture the local screen and does not post local input,
everything travels over the RDP protocol.

On macOS there is no Xvfb: `rdpctl.py` picks the platform on startup and hands over to
`rdpview.py`, which connects with `aardwolf` and draws the frames in a Tk window. Read
`references/macos.md` for the rules that keep the remote session untouched.

## Quick start

Create a profile with mode 600, it never goes into a repository:

```bash
install -d -m 700 ~/.config/rdp-agent && umask 177 && cat > ~/.config/rdp-agent/default.env <<'EOF'
RDP_HOST=win-host.example.com
RDP_PORT=3389
RDP_USER=user
RDP_DOMAIN=example.com
RDP_PASSWORD=change-me
RDP_DISPLAY=:77
RDP_VNC_PORT=5977
RDP_GEOMETRY=1920x1080
EOF
```

Then just talk to the agent in plain words, "over RDP on the build machine, open
the log viewer and tell me what the last error is". The skill triggers on its own
description. By hand it looks like this:

```bash
python3 ~/Repository/skills/rdp-agent/scripts/rdpctl.py start
```

```bash
python3 ~/Repository/skills/rdp-agent/scripts/rdpctl.py shot --crop 0 1040 1920 40 --zoom 2
```

```bash
python3 ~/Repository/skills/rdp-agent/scripts/rdpctl.py stop
```

## What is inside

| Path | Role |
|------|------|
| `SKILL.md` | The skill itself: architecture, the work loop, the command table, field notes. |
| `scripts/rdpctl.py` | Session lifecycle and every action: start, shot, click, type, unlock, keepalive, stop. |
| `scripts/xctl.py` | XTEST driver for one display (mouse, keyboard, capture), reused from the `screenshotting-gui` skill. |
| `scripts/xpaste.py` | Serves UTF-8 text on the X clipboard for paste-based input. |
| `scripts/jiggle.py` | One-pixel mouse jiggler against the idle lock. |
| `references/troubleshooting.md` | Symptoms and fixes collected from real runs. |

## Things worth knowing before you use it

- **Lock screen.** A locked remote session is recovered by reconnecting the client,
  Windows clears the lock itself over NLA and every open window survives. Typing
  the password into the logon form exists as a fallback and requires an explicit
  operator confirmation flag;
- **Account lockout.** After an authentication failure the skill records it and
  refuses to try again until a human checks the password. Domain accounts lock out,
  and an agent in a retry loop is the fastest way to get there;
- **Cyrillic input.** Where clipboard redirection is disabled by policy, Cyrillic is
  typed by switching the Windows layout to Russian and sending US keys through the
  YCUKEN mapping;
- **Session takeover.** Connecting occupies the same Windows session the operator
  uses. If they are sitting at that machine, they get disconnected;
- **Pixel automation is slow and brittle by nature.** If the target offers WinRM,
  SSH or an API, use those instead and keep this skill for what only the desktop
  can do.

## Security

The password lives in one file with mode 600, is handed to the client over stdin,
never appears in `ps`, and is redacted from the command line written to the logs.
The virtual display is started without `-ac`, the VNC server listens on localhost
only, and the generated viewer profile stores no credentials. Screenshots of the
remote desktop accumulate under `~/.local/state/rdp-agent/<profile>/shots/`, treat
that directory as sensitive and clean it out when the work is done.

## How to invoke

- **Slash command**, type `/rdp-agent` in agent chat;
- **`@` context**, attach the skill folder or `SKILL.md` to the message;
- **Automatic**, the agent loads the skill on its own when the request matches the
  `description` in `SKILL.md`, for example "look at what is open on the remote
  desktop".

## Source and attribution

Part of [rpa-skills](https://github.com/EvilFreelancer/rpa-skills),
[Pavel Rykov](https://t.me/evilfreelancer)'s agent-skills collection (see the
[notes on vibe coding](https://t.me/evilfreelancer/1485) and
[cursor-vibe-prompts](https://github.com/EvilFreelancer/cursor-vibe-prompts)).

`scripts/xctl.py` and `scripts/xpaste.py` are reused from the
[screenshotting-gui](https://github.com/EvilFreelancer/screenshotting-gui) skill,
which is under the same license.

## License

MIT, see [LICENSE](LICENSE). Author: Pavel Rykov, [@evilfreelancer](https://t.me/evilfreelancer).

## macOS

The skill works on macOS through the RDP protocol instead of a GUI client. There is
no Xvfb and no per-display VNC server on macOS, so the client runs headless inside the
skill and the operator watches it in a preview window. Input travels over the RDP
protocol, which means the macOS cursor and keyboard focus are never touched: the
operator keeps working in any other window while the agent drives the remote machine.

### Requirements

- macOS 12 or newer
- Python 3.11 or newer (3.13 tested)
- Windows App or Microsoft Remote Desktop for the first logon

No macOS permissions are required: the session is driven over the RDP protocol, the
local screen and the local input are not touched.

### Install

Run the pre-flight check first, it changes nothing:

```bash
python3 scripts/macsetup.py check
```

It reports the platform, the architecture, the Python packages, the permissions and
the RDP client, and exits with `0` when everything is in place.

Install the missing Python packages with:

```bash
python3 scripts/macsetup.py fix
```

The `aardwolf` client is installed by the same command, and the way it installs
depends on the CPU:

| CPU | What happens |
|---|---|
| Apple Silicon (`arm64`) | a prebuilt wheel is downloaded, about a minute |
| Intel (`x86_64`) with Python 3.13 | a prebuilt `universal2` wheel exists, no compiler is needed |
| Intel (`x86_64`) with an older Python | no wheel matches, the package is built from source and needs Rust |

Check what `macsetup.py check` says before installing Rust: if `aardwolf` installs
from a wheel, `cargo` is never used.

On Intel, install Rust once before running `fix`:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
export PATH="$HOME/.cargo/bin:$PATH"
```

Rust installs into `~/.cargo`, needs no `sudo` and touches no system files. The build
takes a few minutes.

### First run

1. Open the RDP client you normally use for this machine (for example Windows App,
   Microsoft Remote Desktop or any other client that can reach it), connect yourself,
   enter the password if the lock screen appears, and wait for the desktop. This warms the session up: after it
   the protocol client connects to an already authorised session and no password is
   typed into the logon form.
2. Start the skill. It connects, sends `Ctrl+Alt+Del` if the screen is still locked,
   and opens the preview window.

If the agent asks you to prove the warm-up with a network check, you can decline: on
macOS a live RDP session often shows no established connection on port 3389, so the
check is unreliable. Your word is enough, and a failed connection is reported by the
skill itself within 20 seconds.

Without the warm-up the session stays at the logon screen and the protocol client
cannot get past it.

### Working on the side

The preview window is a normal window: keep it visible, move it to another Space or
minimise it. The session survives all of that.

- **Click inside the window** takes over: from that moment input is yours and the
  agent stops sending.
- **Fn+F5** hands control back to the agent. On a MacBook the top row sends media
  keys by default, so plain F5 goes to dictation; holding Fn sends the real function key.
- **Escape** closes the preview, which ends the session.

While the take-over is active the agent accepts instructions from the chat and
executes them in the same session, without reconnecting.

### Limitations

- The remote resolution is never changed. On a 1024x768 session the picture is
  stretched to the window, so a full screen view costs more than a windowed one.
- macOS has a single cursor and a single keyboard focus for the whole session, so a
  second independent cursor is impossible. The protocol route is what makes parallel
  work possible instead.
- A second connection takes the session over. Your own RDP client and the skill
  cannot be connected at the same time.
- Corporate environments may require a gateway or a client certificate the built-in
  client does not support. Check the logon before relying on it.

## When the skill is used

The skill turns on when the request names the remote work explicitly, in either
language:

| Language | Say |
|---|---|
| English | "set up the RDP connection", "set up the remote desktop skill", "connect to the remote machine", "look at the remote desktop", "unlock the RDP session" |
| Russian | «настрой подключение к RDP», «настрой RDP», «настрой удалёнку», «RDP скилл», «RDP навык», «подключись к удалённой машине» |

A bare "настрой" or "setup" is deliberately not a trigger: those words belong to any
skill, and the agent would guess wrong. Name the remote machine or RDP itself.

### Credentials

The login and password you type in the terminal are the same corporate credentials you
use for the company VPN client, for example NGate, Cisco AnyConnect or OpenVPN. There is
no separate account for the skill. The password is used to authorise the connection only
and is never written to disk.

You can leave your own RDP client open while the skill connects: the session ends
automatically and moves to the new window. If you prefer, close it yourself after the
warm-up.

### A note about the system Python

macOS ships with Python 3.9 and it is not usable here: the client needs 3.11 or newer.
On macOS 14 the system interpreter can even abort with SIGABRT before a script starts,
with the message "macOS 14 (1408) or later required, have instead 14 (1406)". That is
an Apple build problem, not a problem with the skill.

Install a newer one and call it explicitly:

```bash
the python.org installer for macOS (it includes Tcl/Tk)
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 scripts/macsetup.py check
```

### Do not use Homebrew for Python here

On some macOS 14 machines Homebrew has no prebuilt bottle and starts compiling the
interpreter from source. That pulls a downgrade of `openssl@3` and touches packages
other projects depend on. Install Python from python.org instead: the macOS installer
is a signed universal2 build and already contains Tcl/Tk, which the preview window
needs.

```bash
# after installing from python.org
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 scripts/macsetup.py check
```

If the operator prefers Homebrew, check first that it will use a bottle:

```bash
brew info python@3.12 | head -5   # "bottle" must be present for this OS
```
