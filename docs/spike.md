# Spikes

Questions only real VMs and real apps can answer. Each records its result and,
on failure, the fallback taken. Status: **pending** unless noted.

| # | Question | Pass criteria | Fallback | Result |
|---|---|---|---|---|
| S0 | Can the exe hub reach machine0 and carry the load? | Outbound ssh from the hub to a machine0 IP works. Hub average CPU < 50 %, RSS < 3 GB, no input lag with two orchestrator agents and hubd while the exe agent VM runs a heavy build | Move the hub to machine0 `large` (`bootstrap-paseo-machine0 --role hub --host machine0`) | partial: hub provisioned on exe (2 vCPU / 4 GB); idle with Paseo + hubd it uses about 0.9 GB. Load test pending |
| S1 | Identity and pairing | `paseo-setup` works on machine0 Ubuntu; `paseo daemon pair --relay --json` works over ssh; two clones of the scrubbed image get distinct serverIds; `spoke init` hostname shows in the app | Regenerate identity in `spoke init` | pending |
| S2 | Connect button | `Linking.openURL("paseo://pair#offer=...")` from the plugin opens the confirm sheet and adds the host on iOS | Copy link, then Add host, Paste pairing link, or QR | pending. Desktop routes only agent links through `open-url` (packages/desktop/src/main.ts), so desktop uses Copy link / QR by design |
| S3 | Suspend and resume | After resume the relay reconnects without re-pairing; Claude, Codex, pi and opencode conversations continue | - | pending |
| S4 | Credentials | Push plus pi-brokered-auth under Paseo's pi RPC mode: forced expiry twice, no `refresh_token_reused`; Radius lifetime suits `refresh_margin_h.radius = 12`; setup-token-only Claude Code and pi stay on `five_hour`/`seven_day`; Codex app-server runs on the pushed `auth.json` | Per-spoke device login for the failing provider | pending |
| S5 | Plugin | A server RPC spawns the CLI; the screen renders on iOS; both survive a daemon restart | - | partial: verified on the Mac against a 0.10.3 test daemon with a stub CLI (RPCs, jobs, web UI rendering). iOS pending |
| S6 | Status JSON | `paseo ls -g --json`, `paseo permit ls --json` and the schedule files give the fields hubd uses, locally and over `--host ssh://` | - | partial: shapes checked against the 0.10.3 source and a local daemon |
| S7 | Hooks and pi UI | Paseo-run Claude and Codex show no hook errors with the moshi-hook and herdr-agent-state stubs; record how pi-plan-mode and ask-user render in Paseo | More stubs | partial: on exe.dev, pi's bundled exe-dev extension asks "Use exe.dev LLM integrations?" once; Paseo's RPC mode cannot answer, so pi exited and Paseo showed the provider as `error`. The bootstrap now writes `~/.pi/agent/exe-dev-llm-integration.json` (`useExeIntegration: false`) on exe hosts; the provider is Ready. The existing exe agent VM shows the same error for its own reason (not changed here) |
| S8 | Image | Image built with no profile; `new` with profile `paseo-machine0` gets gh credentials; IP and host-key change on resume handled | - | pending |
