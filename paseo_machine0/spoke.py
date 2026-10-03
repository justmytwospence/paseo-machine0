"""Commands that run on a spoke, called by the hub over ssh (`paseo-machine0 spoke ...`)."""

from __future__ import annotations

import datetime
import glob
import json
import os
import socket
import subprocess
import sys
from typing import Any, Dict, List, Optional

from . import config, creds, idle

RESTART_FLAG = "restart-pending"


def paseo_json(args: List[str], timeout: float = 45) -> Any:
    try:
        proc = subprocess.run(["paseo"] + args + ["--json"], capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


def _iso_epoch(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def last_activity(home: str = "") -> Optional[float]:
    """Newest lastActivityAt/updatedAt across Paseo's stored agent records."""
    newest = None
    for path in glob.glob(os.path.join(home or config.PASEO_HOME, "agents", "*", "*.json")):
        record = config.read_json(path, {}) or {}
        for key in ("lastActivityAt", "updatedAt"):
            t = _iso_epoch(record.get(key))
            if t is not None and (newest is None or t > newest):
                newest = t
    return newest


def active_schedules(home: str = "") -> int:
    """Schedules and heartbeats Paseo will still fire (the CLI lists only new-agent schedules)."""
    count = 0
    for path in glob.glob(os.path.join(home or config.PASEO_HOME, "schedules", "*.json")):
        if (config.read_json(path, {}) or {}).get("status") == "active":
            count += 1
    return count


def load15() -> Optional[float]:
    try:
        with open("/proc/loadavg") as f:
            return float(f.read().split()[2])
    except (OSError, IndexError, ValueError):
        try:
            return float(os.getloadavg()[2])
        except OSError:
            return None


def summarize_agents(agents: Optional[List[Dict[str, Any]]]) -> Dict[str, int]:
    counts = {"busy": 0, "idle": 0, "error": 0, "total": 0}
    for agent in agents or []:
        status = str(agent.get("status") or "")
        counts["total"] += 1
        if status in idle.BUSY_STATES:
            counts["busy"] += 1
        elif status == "idle":
            counts["idle"] += 1
        elif status == "error":
            counts["error"] += 1
    return counts


def restart_flag() -> str:
    return config.state_path(RESTART_FLAG)


def restart_paseo() -> bool:
    proc = subprocess.run(["systemctl", "--user", "restart", "paseo.service"], capture_output=True, text=True)
    if proc.returncode == 0:
        try:
            os.unlink(restart_flag())
        except FileNotFoundError:
            pass
        return True
    print("paseo restart failed: %s" % proc.stderr.strip(), file=sys.stderr)
    return False


def busy_agents() -> int:
    return summarize_agents(paseo_json(["ls", "-g"])).get("busy", 0)


def collect(maintain: bool = False) -> Dict[str, Any]:
    agents = paseo_json(["ls", "-g"])
    permissions = paseo_json(["permit", "ls"]) if agents is not None else None
    counts = summarize_agents(agents)
    pending = os.path.exists(restart_flag())
    if maintain and pending and agents is not None and counts["busy"] == 0:
        pending = not restart_paseo()
    try:
        version = subprocess.run(["paseo", "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        version = ""
    return {
        "daemon": "up" if agents is not None else "down",
        "agents": counts,
        "permissions": [{"id": p.get("id"), "agent": p.get("agentShortId"), "tool": p.get("name")}
                        for p in permissions or [] if isinstance(p, dict) and p.get("id")],
        "schedules_active": active_schedules(),
        "last_activity": last_activity(),
        "load15": load15(),
        "paseo_version": version,
        "restart_pending": pending,
        "hostname": socket.gethostname(),
    }


WORK_SCRIPT = r"""
shopt -s nullglob
for d in ~/Projects/*/ ~/Projects/*/.worktrees/*/ ~/.paseo/worktrees/*/*/; do
  [ -d "$d/.git" ] || [ -f "$d/.git" ] || continue
  cd "$d" || continue
  dirty=$(git status --porcelain 2>/dev/null | head -1)
  ahead=$(git log --oneline @{u}.. 2>/dev/null | head -1)
  noup=$(git rev-parse --abbrev-ref @{u} >/dev/null 2>&1 || echo noupstream)
  if [ -n "$dirty" ] || [ -n "$ahead" ] || [ -n "$noup" ]; then echo "$d ${dirty:+dirty }${ahead:+unpushed }$noup"; fi
done
"""


def unsaved_work() -> List[str]:
    proc = subprocess.run(["bash", "-c", WORK_SCRIPT], capture_output=True, text=True, timeout=600)
    return [line for line in proc.stdout.splitlines() if line.strip()]


def init(vm: str) -> int:
    """Name the machine after its VM (the Paseo apps label hosts by hostname), then restart Paseo."""
    if not vm.startswith(config.VM_PREFIX):
        print("init expects the VM name (%s<name>)" % config.VM_PREFIX, file=sys.stderr)
        return 2
    if socket.gethostname() != vm:
        subprocess.run(["sudo", "hostnamectl", "set-hostname", vm], check=True)
        subprocess.run(["sudo", "sed", "-i", r"s/^127\.0\.1\.1\s.*/127.0.1.1 %s/" % vm, "/etc/hosts"], check=False)
        subprocess.run(["sudo", "sh", "-c", "grep -q '^127.0.1.1 ' /etc/hosts || echo '127.0.1.1 %s' >> /etc/hosts" % vm],
                       check=False)
    return 0 if restart_paseo() else 1


def apply_creds(raw: str) -> Dict[str, Any]:
    bundle = json.loads(raw)
    changed = creds.apply(bundle, write_secrets=True)
    if changed["secrets"]:
        # The daemon and every agent it spawns read secrets.env through ~/.zshenv
        # at start; restart now if nothing is running, else when it next is idle.
        if busy_agents() == 0:
            changed["restarted"] = restart_paseo()
        else:
            open(restart_flag(), "w").close()
            changed["restart_pending"] = True
    return changed
