# paseo-machine0

Per-project [Paseo](https://paseo.sh) spokes on [machine0](https://machine0.io)
VMs, run from one always-on hub.

- **Spokes.** Each project gets its own machine0 VM, `paseo-<name>`, cloned from
  a golden image. It runs its own Paseo daemon (Claude Code, Codex, pi,
  opencode). The Paseo apps reach it through Paseo's end-to-end encrypted relay,
  so the new IP a spoke gets on every resume does not matter, and the hub is
  never in the path between you and an agent.
- **Hub.** One always-on machine (an exe.dev VM) that creates, wakes, suspends
  and removes spokes, builds the image, owns every subscription login, pushes
  fresh credentials to spokes, suspends idle ones, and serves a **Spokes**
  screen inside the Paseo app.

Spokes never connect to the hub: everything flows from the hub over ssh.

## Commands (`paseo-machine0`, every one takes `--json`)

| Command | What it does |
|---|---|
| `new <name> [--size S] [--repo owner/repo]...` | Create `paseo-<name>` from the image, name the host, push credentials, sync dotfiles and Paseo, clone the repos as Paseo projects, fetch the pairing link |
| `wake <name>` | Start, refresh the ssh alias and host key, push credentials, update Paseo (nothing is running yet), report ready |
| `suspend <name>` / `keep-awake <name> [on\|off]` | Suspend now / exempt from auto-suspend |
| `rm <name> [--force]` | Refuse with uncommitted or unpushed work; archive Paseo, pi, Claude, Codex and opencode sessions to `~/spoke-archive/<name>/`; destroy |
| `ls [--cached]` | Spokes with state, cost, agents, pending permissions, idle time |
| `pair-link <name> [--refresh]` | The spoke's pairing link (and `paseo://` form and QR) |
| `ssh <name> [cmd]`, `sync <name>\|--running`, `push-creds <name>\|--running` | Maintenance |
| `image build [--fresh]` | Build `paseo-machine0-spoke`: bootstrap a builder, stop Paseo, scrub identities and credentials, snapshot, promote (machine0 retires the previous version) |
| `secrets set KEY\|login\|show`, `hub-status`, `hubd [--once]` | Hub secrets, broker logins, health, the daemon |

`paseo-machine0 spoke init|apply-creds|status|work|restart-paseo` run on spokes;
the hub calls them over ssh.

## Credentials

The hub holds everything in `~/.config/paseo-machine0/secrets.env` (0600):
the `claude setup-token` under `ANTHROPIC_OAUTH_TOKEN` and
`CLAUDE_CODE_OAUTH_TOKEN`, `MODEL_API_KEY`, `TYPESAFE_API_KEY`, and
`MACHINE0_API_TOKEN` (never pushed). Subscription logins that rotate their
refresh token (openai-codex, radius) live only in the broker store
`~/.local/state/paseo-machine0/broker/auth.json`, filled by `secrets login`;
`broker/broker.mjs` (pi's `ModelRuntime`) is their only refresher and refreshes
once less than the provider's `refresh_margin_h` remains (24 h; 12 h for
Radius, whose tokens last about a day).

hubd pushes a bundle to each running spoke after `new` and `wake`, whenever a
brokered token's expiry changes, and at least every 6 hours. `spoke apply-creds`
writes `secrets.env`, `brokered-auth.json` (read by
[pi-brokered-auth](https://github.com/justmytwospence/pi-brokered-auth)), pi's
`auth.json` entries with the sentinel refresh token `brokered`, and
`~/.codex/auth.json`. A changed `secrets.env` restarts Paseo, at once when no
agent runs, otherwise at the next idle poll.

## Auto-suspend

hubd polls each running spoke every 5 minutes and suspends it after 120
minutes with: no agent running or initializing, no active schedule or
heartbeat, 15-minute load under 0.3, keep-awake off, and no permission request
younger than 24 hours. The clock starts at the later of the first idle poll and
the newest agent activity. A spoke that cannot be polled is never suspended.

## The Spokes screen

`paseo-plugin/` is a Paseo plugin (id `machine0`) installed on the hub's
daemon. It lists spokes and runs the CLI for New, Wake, Suspend, Keep awake and
Remove (as jobs whose log the screen shows). Connect opens the spoke's pairing
link as `paseo://pair#offer=...`, which the phone app treats as Add host; on
desktop, Copy link then Add host, Paste pairing link, or show the QR.

## Configuration

`~/.config/paseo-machine0/config.json` overrides the defaults in
`paseo_machine0/config.py`: `region`, `gpu_region`, `default_size`, `image`,
`profile`, `ssh_key`, `idle_minutes`, `load_threshold`,
`permission_grace_hours`, `push_interval_hours`, `brokered_providers`,
`refresh_margin_h`.

Setup, rollout and phone pairing are in the dotfiles' `docs/machine0-paseo.md`;
spokes run its chezmoi setup as host `paseo-spoke`. Open questions verified on real VMs are in `docs/spike.md`.

## Tests

```sh
python3 -B -m unittest discover -s tests -t .
(cd paseo-plugin && npm install && npm run typecheck)
```
