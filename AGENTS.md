# Authoring & maintaining the `rdp-agent` skill

Agent guide for working **inside this skill repository**. Read it before editing
`SKILL.md`, the scripts, the references or any plugin manifest.

## Repository layout

The skill is packaged for **Claude Code**, **Cursor**, **Codex** and **Coddy**, and
installs as a plain skill folder for any other agent. The layout is flat, `SKILL.md`
and its assets live at the repo root and the plugin manifests point their skill
source at `./`.

```
rdp-agent/
├─ .claude-plugin/plugin.json
├─ .cursor-plugin/plugin.json
├─ .codex-plugin/plugin.json
├─ SKILL.md              # CANONICAL skill: YAML frontmatter + instructions
├─ README.md             # human-facing documentation
├─ AGENTS.md             # this file
├─ references/           # deep knowledge, loaded on demand from SKILL.md
└─ scripts/              # the executable part of the skill
```

`scripts/xctl.py` and `scripts/xpaste.py` come from the author's `screenshotting-gui`
skill (same MIT license). Keep them in sync by hand, do not fork their behaviour
without a reason, and never make `rdpctl.py` depend on that skill being installed.

## Hard rules for this skill

1. **The skill must stay vendor-neutral.** No employer names, no real hostnames,
   usernames, domains or ticket numbers anywhere in the repository, including
   comments, examples and commit messages. Profiles with real credentials live in
   `~/.config/rdp-agent/*.env`, outside of git;
2. **Never weaken the credential guards.** The password goes to the client over
   stdin, is redacted from logs, and the authentication-failure flag that blocks a
   blind retry is a safety feature, not an inconvenience. Domain accounts lock out;
3. **Never let the agent close a session on its own.** `stop` is an operator
   decision, this is the whole point of the skill;
4. **Typing a password into the lock form needs two flags**, `--type-password` and
   `--operator-confirmed`. Do not collapse them into one;
5. **Kill only tracked processes.** Every PID in `session.json` has a marker that is
   checked against `/proc/<pid>/cmdline` before a signal is sent.

## Editing SKILL.md

The frontmatter `description` is what makes the skill trigger, so it has to name
the situations in the user's own words, in English and Russian. Keep it under
roughly 120 words.

The body is instructions for an agent that has never seen this tool. Everything the
model already knows (what X11, RDP or ImageMagick are) stays out, only the parts
that are easy to get wrong go in. Deep material belongs in `references/`.

## Testing changes

There is no unit-test suite, the tool talks to a live desktop. The minimum smoke
test against a real host before publishing:

```bash
python3 scripts/rdpctl.py start && python3 scripts/rdpctl.py status
```

```bash
python3 scripts/rdpctl.py shot --crop 1540 1040 200 35 --zoom 4
```

```bash
python3 scripts/rdpctl.py key Super_L && python3 scripts/rdpctl.py key Escape
```

```bash
python3 scripts/rdpctl.py state && python3 scripts/rdpctl.py stop
```

For the lock path, lock the remote session with `key super+l`, confirm `state`
reports `locked`, run `unlock`, and confirm the desktop comes back with the same
windows open.

After `stop`, verify nothing is left behind by walking `/proc` rather than by
`pgrep -f`, whose pattern matches the checking shell itself.

## Versioning and release

`version` appears in `SKILL.md` and in all three plugin manifests, bump all four in
the same commit. Tag releases as `vX.Y.Z`.

## Style

Documentation in this repository is written in English. Russian text elsewhere in
the author's projects follows a separate style guide: no em dashes, straight
quotes only, and the adjective for "agentic" is **«агентный»**, never «агентский».
