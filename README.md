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
X input.

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
