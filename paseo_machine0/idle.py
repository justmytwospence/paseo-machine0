"""The auto-suspend rule, kept pure for tests.

A spoke is idle when none of its Paseo agents is running or initializing, no
schedule or heartbeat is active, its 15-minute load is under the threshold (no
build, dev server or hand-run agent burning CPU), keep-awake is off, and every
pending permission request is older than the grace period (a forgotten prompt
must not keep a spoke awake forever). It is suspended once it has been idle for
idle_minutes, counted from the later of the first idle poll and the newest
agent activity.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Tuple

BUSY_STATES = frozenset({"running", "initializing"})


def track_permissions(seen: Dict[str, float], current: Iterable[str], now: float) -> Dict[str, float]:
    """First-seen time of every pending permission id; resolved ones drop out."""
    return {pid: float(seen.get(pid, now)) for pid in current}


def idle_now(status: Optional[Dict[str, Any]], keep_awake: bool, load_threshold: float,
             permissions_seen: Dict[str, float], grace_s: float, now: float) -> Tuple[bool, str]:
    if keep_awake:
        return False, "keep-awake"
    if not status:
        return False, "status unknown"
    busy = int((status.get("agents") or {}).get("busy") or 0)
    if busy:
        return False, "%d agent%s running" % (busy, "" if busy == 1 else "s")
    schedules = int(status.get("schedules_active") or 0)
    if schedules:
        return False, "%d schedule%s active" % (schedules, "" if schedules == 1 else "s")
    load15 = status.get("load15")
    if load15 is None:
        return False, "load unknown"
    if float(load15) >= load_threshold:
        return False, "load %.2f" % float(load15)
    fresh = [t for t in permissions_seen.values() if now - t < grace_s]
    if fresh:
        return False, "permission pending"
    return True, "idle"


def decide(idle: bool, idle_since: Optional[float], last_activity: Optional[float], now: float,
           idle_minutes: float) -> Tuple[bool, Optional[float]]:
    """(suspend?, new idle_since)."""
    if not idle:
        return False, None
    since = idle_since if idle_since is not None else now
    start = max(since, last_activity or 0.0)
    return now - start >= idle_minutes * 60, since
