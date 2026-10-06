"""`paseo-machine0`: command-line entry point for the hub and spokes."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from . import config


class UsageError(Exception):
    pass


def need(role: str) -> None:
    if config.role() != role:
        raise UsageError("this command runs on the %s (PASEO_MACHINE0_ROLE here: %s)" % (role, config.role()))


def emit(a: argparse.Namespace, data: Any, text: Optional[str] = None) -> int:
    if getattr(a, "json", False):
        print(json.dumps(data, indent=2, sort_keys=True))
    elif text is not None:
        print(text)
    elif isinstance(data, (dict, list)):
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(data)
    return 0


# ---- hub: spokes --------------------------------------------------------------


def rows(live: bool) -> List[Dict[str, Any]]:
    from . import machine0, registry
    reg = registry.load()["spokes"]
    status = registry.load_status()
    machines: Dict[str, Any] = {}
    if live:
        machines = {m.get("name"): m for m in machine0.machines()}
    now = time.time()
    out = []
    for name, info in sorted(reg.items()):
        st = status.get(name) or {}
        vm = config.vm_name(name)
        m = machines.get(vm) if live else None
        state = machine0.status(m) if live else (st.get("machine") or "UNKNOWN")
        size = (m or {}).get("size") or info.get("size") or ""
        seen = list((info.get("permissions_seen") or {}).values())
        agents = st.get("agents") or {}
        out.append({
            "name": name,
            "vm": vm,
            "machine": state,
            "size": size,
            "price_per_hour": config.PRICES.get(size),
            "ip": machine0.ip(m) if live else st.get("ip"),
            "keep_awake": bool(info.get("keep_awake")),
            "idle_minutes": int((now - info["idle_since"]) / 60) if info.get("idle_since") else None,
            "idle_reason": info.get("idle_reason"),
            "suspended": info.get("suspended"),
            "operation": st.get("operation"),
            "reachable": st.get("reachable"),
            "daemon": st.get("daemon"),
            "agents_busy": agents.get("busy", 0),
            "agents_idle": agents.get("idle", 0),
            "agents_error": agents.get("error", 0),
            "permissions": len(st.get("permissions") or []),
            "oldest_permission_hours": round((now - min(seen)) / 3600, 1) if seen else None,
            "schedules_active": st.get("schedules_active") or 0,
            "restart_pending": bool(st.get("restart_pending")),
            "paseo_version": st.get("paseo_version"),
            "last_push": info.get("last_push"),
            "paired": bool(info.get("pair_url")),
            "polled_at": st.get("polled_at"),
        })
    return out


def cmd_ls(a: argparse.Namespace) -> int:
    need("hub")
    data = rows(live=not a.cached)
    if a.json:
        return emit(a, data)
    fmt = "%-18s %-11s %-10s %-8s %-6s %-10s %s"
    print(fmt % ("NAME", "STATE", "SIZE", "$/H", "AWAKE", "AGENTS", "NOTES"))
    for r in data:
        notes = []
        if r["operation"]:
            notes.append(r["operation"])
        if r["idle_minutes"] is not None:
            notes.append("idle %dm" % r["idle_minutes"])
        if r["permissions"]:
            notes.append("%d permission(s) pending" % r["permissions"])
        if r["restart_pending"]:
            notes.append("restart pending")
        if r["suspended"] and r["machine"] != "RUNNING":
            notes.append("suspended (%s)" % r["suspended"].get("reason"))
        if r["reachable"] is False:
            notes.append("unreachable")
        print(fmt % (r["name"], r["machine"], r["size"] or "-",
                     "%.3f" % r["price_per_hour"] if r["price_per_hour"] else "-",
                     "pin" if r["keep_awake"] else "-",
                     "%d run/%d idle" % (r["agents_busy"], r["agents_idle"]), ", ".join(notes)))
    return 0


def cmd_new(a: argparse.Namespace) -> int:
    need("hub")
    from . import lifecycle
    return emit(a, lifecycle.new(a.name, a.size, a.repo or []), "created %s" % config.vm_name(a.name))


def cmd_wake(a: argparse.Namespace) -> int:
    need("hub")
    from . import lifecycle
    return emit(a, lifecycle.wake(a.name), "%s is awake" % config.vm_name(a.name))


def cmd_suspend(a: argparse.Namespace) -> int:
    need("hub")
    from . import lifecycle
    return emit(a, lifecycle.suspend(a.name), "%s suspended" % config.vm_name(a.name))


def cmd_rm(a: argparse.Namespace) -> int:
    need("hub")
    from . import lifecycle
    return emit(a, lifecycle.rm(a.name, a.force), "%s removed" % config.vm_name(a.name))


def cmd_keep_awake(a: argparse.Namespace) -> int:
    need("hub")
    from . import registry
    if not registry.get(a.name):
        raise UsageError("%s is not a paseo-machine0 spoke" % a.name)
    on = a.state != "off"
    registry.put(a.name, keep_awake=on, idle_since=None)
    return emit(a, {"name": a.name, "keep_awake": on}, "%s keep-awake %s" % (a.name, "on" if on else "off"))


def running_names(names: List[str], everything: bool) -> List[str]:
    from . import machine0, registry, sshconf
    if everything:
        names = list(registry.load()["spokes"])
    out = []
    for name in names:
        m = machine0.get(config.vm_name(name))
        if machine0.status(m) != machine0.RUNNING:
            print("%s is %s; skipped" % (name, machine0.status(m).lower()), file=sys.stderr)
            continue
        sshconf.update(config.vm_name(name), machine0.ip(m) or "")
        out.append(name)
    return out


def cmd_sync(a: argparse.Namespace) -> int:
    need("hub")
    from . import lifecycle
    if not a.name and not a.running:
        raise UsageError("give a spoke name or --running")
    rc = 0
    for name in running_names([a.name] if a.name else [], a.running):
        try:
            lifecycle.sync(name)
        except Exception as e:
            print("%s: %s" % (name, e), file=sys.stderr)
            rc = 1
    return rc


def cmd_push_creds(a: argparse.Namespace) -> int:
    need("hub")
    from . import creds, lifecycle
    if not a.name and not a.running:
        raise UsageError("give a spoke name or --running")
    errors: List[str] = []
    bundle = creds.build_bundle(errors)
    for e in errors:
        print("broker: %s" % e, file=sys.stderr)
    creds.apply(bundle, write_secrets=False)
    results = {}
    for name in running_names([a.name] if a.name else [], a.running):
        results[name] = lifecycle.push(name, bundle)
    return emit(a, results, "pushed to %s" % (", ".join(results) or "no running spoke"))


def cmd_pair_link(a: argparse.Namespace) -> int:
    need("hub")
    from . import lifecycle
    from . import registry
    url = lifecycle.pair_url(a.name, refresh=a.refresh)
    qr = (registry.get(a.name) or {}).get("pair_qr") or ""
    return emit(a, {"name": a.name, "url": url, "app_url": app_url(url), "qr": qr}, url)


def app_url(url: str) -> str:
    """The same offer through the app's own scheme, which the Paseo apps open as Add host."""
    _, _, fragment = url.partition("#")
    return "paseo://pair#" + fragment if fragment else url


def cmd_ssh(a: argparse.Namespace) -> int:
    need("hub")
    names = running_names([a.name], False)
    if not names:
        return 1
    argv = config.ssh_base() + [config.vm_name(a.name)] + a.command
    os.execvp(argv[0], argv)
    return 0


def cmd_image(a: argparse.Namespace) -> int:
    need("hub")
    from . import lifecycle
    return emit(a, lifecycle.image_build(a.fresh))


def cmd_hubd(a: argparse.Namespace) -> int:
    from . import hubd
    return hubd.main(once=a.once)


def cmd_hub_status(a: argparse.Namespace) -> int:
    need("hub")
    from . import broker, registry
    out: Dict[str, Any] = {}
    try:
        with open("/proc/loadavg") as f:
            out["load"] = f.read().split()[:3]
    except OSError:
        out["load"] = [round(x, 2) for x in os.getloadavg()]
    ps = subprocess.run(["ps", "-eo", "rss=,comm="], capture_output=True, text=True).stdout
    rss: Dict[str, int] = {}
    for line in ps.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            rss[parts[1].strip()] = rss.get(parts[1].strip(), 0) + int(parts[0])
    out["rss_mb"] = {k: round(v / 1024) for k, v in sorted(rss.items(), key=lambda kv: -kv[1])[:8]}
    out["hubd"] = subprocess.run(["systemctl", "--user", "is-active", "paseo-machine0-hubd.service"],
                                 capture_output=True, text=True).stdout.strip() or "unknown"
    out["credentials"] = broker.status()
    out["spokes"] = len(registry.load()["spokes"])
    return emit(a, out)


def cmd_secrets(a: argparse.Namespace) -> int:
    need("hub")
    from . import broker
    if a.action == "show":
        values = config.read_secrets()
        return emit(a, {
            "secrets.env": {k: "set (%d chars)" % len(v) for k, v in sorted(values.items())},
            "pushed_to_spokes": sorted(config.spoke_secrets(values)),
            "broker": broker.status(),
        })
    if a.action == "set":
        if not a.key:
            raise UsageError("usage: paseo-machine0 secrets set KEY  (value from stdin or a prompt)")
        value = sys.stdin.read().strip() if not sys.stdin.isatty() else getpass.getpass("%s: " % a.key).strip()
        values = config.read_secrets()
        keys = config.SETUP_TOKEN_KEYS if a.key in config.SETUP_TOKEN_KEYS else (a.key,)
        for key in keys:
            if value:
                values[key] = value
            else:
                values.pop(key, None)
        config.write_secrets(values)
        print("%s %s" % (", ".join(keys), "set" if value else "removed"))
        if a.key in config.SPOKE_SECRETS:
            print("running spokes get it on hubd's next pass, or now: paseo-machine0 push-creds --running")
        return 0
    if a.action == "login":
        os.makedirs(config.BROKER_DIR, mode=0o700, exist_ok=True)
        print("A pi session opens on the broker's credential store.\n"
              "Run /login for: %s. Use the device-code or paste-the-URL options, then /quit.\n"
              "Never use these logins anywhere else: the broker must be their only refresher.\n"
              % ", ".join(config.settings()["brokered_providers"]))
        env = dict(os.environ, PI_CODING_AGENT_DIR=config.BROKER_DIR)
        # pi-brokered-auth would otherwise take these providers over in this session too.
        env.pop("PI_BROKERED_AUTH_FILE", None)
        env.pop("PI_BROKERED_AUTH_PROVIDERS", None)
        for name in ("settings.json", "models.json"):
            src = os.path.expanduser("~/.pi/agent/" + name)
            dst = os.path.join(config.BROKER_DIR, name)
            if os.path.exists(src) and not os.path.exists(dst):
                os.symlink(src, dst)
        return subprocess.call(["pi", "--no-session"], env=env)
    return 2


# ---- spoke commands -----------------------------------------------------------


def cmd_spoke(a: argparse.Namespace) -> int:
    from . import spoke
    if a.spoke_cmd == "init":
        return spoke.init(a.vm)
    if a.spoke_cmd == "apply-creds":
        print(json.dumps(spoke.apply_creds(sys.stdin.read())))
        return 0
    if a.spoke_cmd == "status":
        print(json.dumps(spoke.collect(maintain=a.maintain)))
        return 0
    if a.spoke_cmd == "work":
        print(json.dumps(spoke.unsaved_work()))
        return 0
    if a.spoke_cmd == "restart-paseo":
        return 0 if spoke.restart_paseo() else 1
    return 2


# ---- parser -------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="paseo-machine0",
                                description="Per-project Paseo spokes on machine0, run from a hub")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name: str, fn, help: str, json_flag: bool = True) -> argparse.ArgumentParser:
        s = sub.add_parser(name, help=help)
        if json_flag:
            s.add_argument("--json", action="store_true", help="machine-readable output")
        s.set_defaults(fn=fn)
        return s

    s = add("ls", cmd_ls, "list spokes")
    s.add_argument("--cached", action="store_true", help="use hubd's last poll instead of asking machine0")

    s = add("new", cmd_new, "create a spoke from the golden image")
    s.add_argument("name")
    s.add_argument("--size")
    s.add_argument("--repo", action="append", help="owner/repo to clone into ~/Projects (repeatable)")

    for name, fn, help in (("wake", cmd_wake, "resume a suspended spoke, push credentials, update it"),
                           ("suspend", cmd_suspend, "suspend a spoke now")):
        s = add(name, fn, help)
        s.add_argument("name")

    s = add("rm", cmd_rm, "destroy a spoke (refuses with unpushed work)")
    s.add_argument("name")
    s.add_argument("--force", action="store_true")

    s = add("keep-awake", cmd_keep_awake, "exempt a spoke from auto-suspend")
    s.add_argument("name")
    s.add_argument("state", nargs="?", choices=("on", "off"), default="on")

    s = add("ssh", cmd_ssh, "ssh to a running spoke", json_flag=False)
    s.add_argument("name")
    s.add_argument("command", nargs=argparse.REMAINDER)

    s = add("sync", cmd_sync, "chezmoi update the dotfiles and update Paseo on spokes", json_flag=False)
    s.add_argument("name", nargs="?")
    s.add_argument("--running", action="store_true")

    s = add("push-creds", cmd_push_creds, "push credentials to spokes now")
    s.add_argument("name", nargs="?")
    s.add_argument("--running", action="store_true")

    s = add("pair-link", cmd_pair_link, "a spoke's Paseo pairing link")
    s.add_argument("name")
    s.add_argument("--refresh", action="store_true", help="ask the spoke again")

    s = add("image", cmd_image, "build the golden image")
    s.add_argument("image_cmd", choices=("build",))
    s.add_argument("--fresh", action="store_true", help="start from the machine0 base image")

    s = add("hubd", cmd_hubd, "hub daemon (pushes, idle suspend, status polling)", json_flag=False)
    s.add_argument("--once", action="store_true", help="one pass, then exit")

    add("hub-status", cmd_hub_status, "hub load, memory and credential state")

    s = add("secrets", cmd_secrets, "hub secrets and broker logins")
    s.add_argument("action", choices=("show", "set", "login"))
    s.add_argument("key", nargs="?")

    s = add("spoke", cmd_spoke, "(spoke) commands the hub runs over ssh", json_flag=False)
    ssub = s.add_subparsers(dest="spoke_cmd", required=True)
    i = ssub.add_parser("init", help="name the host after its VM and restart Paseo")
    i.add_argument("vm")
    ssub.add_parser("apply-creds", help="write a credential bundle read from stdin")
    st = ssub.add_parser("status", help="agents, schedules, permissions, load (JSON)")
    st.add_argument("--maintain", action="store_true", help="also apply a pending Paseo restart when idle")
    ssub.add_parser("work", help="repos with uncommitted or unpushed work (JSON)")
    ssub.add_parser("restart-paseo", help="restart the Paseo daemon")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    from . import broker, machine0, registry
    args = parser().parse_args(argv)
    try:
        return int(args.fn(args) or 0)
    except (UsageError, ValueError, RuntimeError, machine0.Machine0Error, registry.Busy, broker.BrokerError,
            subprocess.TimeoutExpired) as e:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(e)}))
        else:
            print("paseo-machine0: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
