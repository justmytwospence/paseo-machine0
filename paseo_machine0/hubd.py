"""`paseo-machine0 hubd`: the hub daemon (systemd user unit paseo-machine0-hubd).

Every check interval it:
  1. asks the broker for each brokered provider, which refreshes a token once it
     is within its refresh margin, and applies the result to the hub itself;
  2. polls `spoke status` on every running spoke and caches it in status.json;
  3. pushes credentials to spokes that need them (creds.push_due);
  4. suspends spokes that have been idle long enough (idle.py).
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, Optional

from . import config, creds, idle, lifecycle, machine0, registry, sshconf


def log(msg: str) -> None:
    path = config.state_path("logs", "hubd.log")
    try:
        if os.path.getsize(path) > 5 * 1024 * 1024:
            os.replace(path, path + ".1")
    except OSError:
        pass
    with open(path, "a") as f:
        f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg))


def check_spoke(name: str, info: Dict[str, Any], m: Optional[Dict[str, Any]], bundle: Dict[str, Any],
                now: float) -> None:
    cfg = config.settings()
    vm = config.vm_name(name)
    state = machine0.status(m)
    busy = registry.operation_running(name)
    if busy:
        registry.put_status(name, machine=state, operation=busy, polled_at=now)
        return
    if state != machine0.RUNNING:
        registry.put_status(name, machine=state, operation=None, polled_at=now)
        if info.get("idle_since") is not None:
            registry.put(name, idle_since=None)
        return

    sshconf.update(vm, machine0.ip(m) or "")
    status: Optional[Dict[str, Any]] = None
    try:
        status = lifecycle.remote_json(vm, "spoke status --maintain", timeout=180)
        registry.put_status(name, machine=state, operation=None, reachable=True, error=None,
                            polled_at=now, ip=machine0.ip(m), **status)
    except Exception as e:  # unreachable or not yet bootstrapped; never suspend blind
        log("%s: status failed: %s" % (name, e))
        registry.put_status(name, machine=state, operation=None, reachable=False, error=str(e)[:300],
                            polled_at=now, ip=machine0.ip(m))

    if status is not None:
        due, why = creds.push_due(info, bundle, now, float(cfg["push_interval_hours"]))
        if due:
            try:
                lifecycle.push(name, bundle)
                log("%s: pushed credentials (%s)" % (name, why))
            except Exception as e:
                log("%s: push failed (%s): %s" % (name, why, e))

    current = [p["id"] for p in (status or {}).get("permissions") or []] if status else \
        list((info.get("permissions_seen") or {}).keys())
    seen = idle.track_permissions(info.get("permissions_seen") or {}, current, now)
    is_idle, reason = idle.idle_now(status, bool(info.get("keep_awake")), float(cfg["load_threshold"]),
                                    seen, float(cfg["permission_grace_hours"]) * 3600, now)
    suspend, since = idle.decide(is_idle, info.get("idle_since"), (status or {}).get("last_activity"),
                                 now, float(cfg["idle_minutes"]))
    registry.put(name, idle_since=since, permissions_seen=seen, idle_reason=reason)
    if suspend:
        log("%s: suspending (idle %d min)" % (name, (now - (since or now)) / 60))
        try:
            lifecycle.suspend(name, reason="idle")
        except Exception as e:
            log("%s: suspend failed: %s" % (name, e))


def tick() -> None:
    errors: list = []
    bundle = creds.build_bundle(errors)
    for e in errors:
        log("broker: %s" % e)
    # The hub's own pi (orchestrators) uses the same brokered file.
    creds.apply(bundle, write_secrets=False)
    spokes = registry.load()["spokes"]
    if not spokes:
        return
    machines = {m.get("name"): m for m in machine0.machines()}
    now = time.time()
    for name, info in spokes.items():
        try:
            check_spoke(name, info, machines.get(config.vm_name(name)), bundle, now)
        except Exception as e:
            log("%s: check failed: %s" % (name, e))


def main(once: bool = False) -> int:
    if config.role() != "hub":
        print("hubd runs on the hub only", file=sys.stderr)
        return 2
    log("hubd started (pid %d)" % os.getpid())
    interval = float(config.settings()["check_interval_s"])
    while True:
        try:
            tick()
        except Exception as e:  # keep the daemon alive; the log says why
            log("tick failed: %s" % e)
        if once:
            return 0
        time.sleep(interval)
