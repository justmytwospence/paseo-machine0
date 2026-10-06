"""Spoke lifecycle on the hub: new, wake, suspend, rm, sync, pushes, pairing, image build."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from . import config, creds, machine0, registry, sshconf


def say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---- remote execution ---------------------------------------------------------


def remote(vm: str, script: str, timeout: float = 3600, check: bool = True, capture: bool = False,
           input: Optional[bytes] = None, login_env: bool = True) -> subprocess.CompletedProcess:
    """Run a bash script on a spoke. login_env runs it under zsh first, which reads
    ~/.zshenv (PATH with nvm's node and ~/.local/bin, the role, secrets.env)."""
    inner = "bash -c " + shlex.quote(script)
    command = "zsh -c " + shlex.quote(inner) if login_env else "bash -lc " + shlex.quote(script)
    # -n and a closed stdin: no remote program can stop at an interactive prompt.
    proc = subprocess.run(
        config.ssh_base() + ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15"] + ([] if input else ["-n"]) + [vm, command],
        timeout=timeout, capture_output=capture, input=input,
        stdin=None if input else subprocess.DEVNULL,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or b"").decode(errors="replace").strip()[-400:] if capture else ""
        raise RuntimeError("%s: remote command failed (%d) %s" % (vm, proc.returncode, detail))
    return proc


def remote_json(vm: str, args: str, timeout: float = 300, input: Optional[bytes] = None) -> Any:
    proc = remote(vm, "paseo-machine0 " + args, timeout=timeout, capture=True, input=input)
    return json.loads(proc.stdout.decode())


def wait_ssh(vm: str, timeout: float = 600) -> None:
    deadline = time.time() + timeout
    while True:
        rc = subprocess.run(config.ssh_base() + ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", vm, "true"],
                            capture_output=True).returncode
        if rc == 0:
            return
        if time.time() > deadline:
            raise RuntimeError("ssh to %s did not come up" % vm)
        time.sleep(5)


def bring_up(vm: str) -> Dict[str, Any]:
    m = machine0.wait_running(vm)
    sshconf.update(vm, machine0.ip(m) or "")
    wait_ssh(vm)
    return m


# ---- credentials --------------------------------------------------------------


def push(name: str, bundle: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Push the current bundle to a running spoke and record what it now holds."""
    vm = config.vm_name(name)
    errors: List[str] = []
    bundle = bundle if bundle is not None else creds.build_bundle(errors)
    for e in errors:
        say("broker: %s" % e)
    result = remote_json(vm, "spoke apply-creds", timeout=180, input=json.dumps(bundle).encode())
    registry.put(name, last_push=time.time(), pushed=creds.expiries(bundle),
                 secrets_digest=config.digest(bundle.get("secrets") or {}))
    return result


# ---- dotfiles -----------------------------------------------------------------

# The dotfiles' chezmoi setup for host paseo-spoke (docs/machine0-paseo.md there),
# run on image builds and on every spoke sync. A spoke's dotfiles checkout is
# disposable, so it is reset and cleaned to origin/main; chezmoi is installed if
# missing (pinned like on every Linux host); init --apply is idempotent, so the
# same script serves a fresh builder, an image built from the previous one, and a
# running spoke.
DOTFILES_SCRIPT = r"""
set -e
test -d ~/dotfiles || git clone -q {url} ~/dotfiles
git -C ~/dotfiles fetch -q origin
git -C ~/dotfiles reset -q --hard origin/main
git -C ~/dotfiles clean -ffdq
test -x ~/.local/bin/chezmoi || sh -c "$(curl -fsLS get.chezmoi.io)" -- -b ~/.local/bin -t v2.73.0
~/.local/bin/chezmoi init --source ~/dotfiles --apply --force --no-tty \
    --promptChoice host=paseo-spoke --promptString extras=
"""

# Restarts the daemon only when Paseo's version or service changed. Through zsh, whose
# ~/.zshenv (now chezmoi's) puts nvm's node on PATH for paseo-setup's npm.
SYNC_SCRIPT = DOTFILES_SCRIPT + "zsh -c '~/.local/bin/paseo-setup >/dev/null'\n"


def sync(name: str) -> None:
    say("syncing dotfiles and Paseo on %s" % name)
    remote(config.vm_name(name), SYNC_SCRIPT.format(url=shlex.quote(config.settings()["dotfiles_url"])),
           timeout=1800)


def bootstrap(vm: str) -> None:
    say("bootstrapping %s (this takes a while)" % vm)
    remote(vm, DOTFILES_SCRIPT.format(url=shlex.quote(config.settings()["dotfiles_url"])),
           timeout=7200, login_env=False)


def clone(name: str, repos: List[str]) -> List[str]:
    vm = config.vm_name(name)
    paths = []
    for repo in repos:
        short = repo.rstrip("/").split("/")[-1]
        if short.endswith(".git"):
            short = short[:-4]
        dest = "$HOME/Projects/%s" % short
        # A full URL, so the plain `git clone` fallback works for owner/repo too.
        url = repo if "://" in repo or repo.startswith("git@") else "https://github.com/%s.git" % repo
        remote(vm, 'mkdir -p ~/Projects && (test -d "{d}" || gh repo clone {u} "{d}" -- -q || git clone -q {u} "{d}") '
                   '&& paseo project create "{d}" >/dev/null'.format(d=dest, u=shlex.quote(url)), timeout=1800)
        paths.append("~/Projects/%s" % short)
    return paths


CLOUD_INIT_WAIT = r"""
if command -v cloud-init >/dev/null; then
  timeout 420 sudo cloud-init status --wait >/dev/null 2>&1
  case $? in
    0|2) echo done ;;      # 2: finished with recoverable errors
    124) echo timeout ;;
    *) echo error ;;
  esac
else
  echo none
fi
"""


def settle_cloud_init(vm: str) -> None:
    """machine0 injects the profile (gh's GitHub login, env) through cloud-init;
    clone nothing before it is done."""
    proc = remote(vm, CLOUD_INIT_WAIT, timeout=480, check=False, capture=True, login_env=False)
    state = (proc.stdout or b"").decode().strip().splitlines()[-1:] or ["?"]
    if state[0] == "timeout":
        say("cloud-init on %s did not finish within 7 minutes; continuing" % vm)


# ---- pairing ------------------------------------------------------------------


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Paseo's QR is half-block characters on an ANSI white background; keep the characters."""
    return ANSI.sub("", text)


def fetch_pair_url(name: str, attempts: int = 12) -> str:
    vm = config.vm_name(name)
    last = ""
    for _ in range(attempts):
        proc = remote(vm, "paseo daemon pair --relay --json", timeout=120, check=False, capture=True)
        try:
            offer = json.loads(proc.stdout.decode())
        except ValueError:
            offer = {}
        url = offer.get("url") or ""
        if url:
            registry.put(name, pair_url=url, pair_qr=strip_ansi(offer.get("qr") or ""))
            return url
        last = (proc.stderr or b"").decode(errors="replace").strip()[-300:]
        time.sleep(5)
    raise RuntimeError("no pairing link from %s: %s" % (vm, last or "daemon not ready"))


def pair_url(name: str, refresh: bool = False) -> str:
    info = registry.get(name) or {}
    if info.get("pair_url") and not refresh:
        return info["pair_url"]
    m = machine0.get(config.vm_name(name))
    if machine0.status(m) != machine0.RUNNING:
        raise RuntimeError("%s is %s; wake it first" % (name, machine0.status(m).lower()))
    sshconf.update(config.vm_name(name), machine0.ip(m) or "")
    return fetch_pair_url(name)


# ---- lifecycle ----------------------------------------------------------------


def new(name: str, size: Optional[str], repos: List[str]) -> Dict[str, Any]:
    cfg = config.settings()
    if not config.valid_name(name):
        raise ValueError("spoke names are lowercase letters, digits and dashes (max 31), not starting machine0-")
    vm = config.vm_name(name)
    if registry.get(name) or machine0.get(vm):
        raise ValueError("%s already exists" % vm)
    size = size or cfg["default_size"]
    gpu = size.startswith("gpu-")
    region = cfg["gpu_region"] if gpu else cfg["region"]
    with registry.operation(name, "new"):
        say("creating %s (%s, %s)" % (vm, size, region))
        registry.put(name, size=size, region=region, keep_awake=False, idle_since=None, suspended=None)
        try:
            machine0.new(vm, size, region, None if gpu else cfg["image"], cfg["ssh_key"], cfg["profile"])
        except machine0.Machine0Error:
            if machine0.get(vm) is None:
                registry.drop(name)
            raise
        bring_up(vm)
        settle_cloud_init(vm)
        if gpu:
            bootstrap(vm)
        say("naming the host")
        remote(vm, "paseo-machine0 spoke init %s" % shlex.quote(vm), timeout=300)
        say("pushing credentials")
        push(name)
        sync(name)
        remote(vm, "paseo-machine0 spoke restart-paseo", timeout=300)
        paths = clone(name, repos)
        url = fetch_pair_url(name)
        registry.put_status(name, machine="RUNNING", polled_at=time.time())
    say("%s is up; connect it from the Spokes screen or with: paseo-machine0 pair-link %s" % (vm, name))
    return {"name": name, "vm": vm, "size": size, "region": region, "projects": paths, "pair_url": url}


def wake(name: str) -> Dict[str, Any]:
    vm = config.vm_name(name)
    if not registry.get(name):
        raise ValueError("%s is not a paseo-machine0 spoke" % name)
    with registry.operation(name, "wake"):
        state = machine0.status(machine0.get(vm))
        if state == "SUSPENDING":
            say("waiting for %s to finish suspending" % vm)
            wait_status(vm, machine0.SUSPENDED, timeout=1800)
            state = machine0.SUSPENDED
        if state != machine0.RUNNING:
            say("starting %s" % vm)
            machine0.start(vm)
        bring_up(vm)
        say("pushing credentials")
        push(name)
        # Nothing runs right after a resume, so this is the moment to update Paseo.
        sync(name)
        registry.put(name, idle_since=None, suspended=None)
        registry.put_status(name, machine="RUNNING", polled_at=time.time())
    say("%s is awake" % vm)
    return {"name": name, "vm": vm, "status": "RUNNING"}


def suspend(name: str, reason: str = "manual") -> Dict[str, Any]:
    vm = config.vm_name(name)
    with registry.operation(name, "suspend"):
        machine0.suspend(vm)
        registry.put(name, idle_since=None, suspended={"reason": reason, "at": time.time()},
                     permissions_seen={})
        registry.put_status(name, machine="SUSPENDED", polled_at=time.time())
    return {"name": name, "vm": vm, "status": "SUSPENDED", "reason": reason}


ARCHIVE_PATHS = ".paseo/agents .pi/agent/sessions .claude/projects .codex/sessions .local/share/opencode/storage"


def archive(name: str) -> str:
    import os
    dest_dir = os.path.expanduser("~/spoke-archive/%s" % name)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "sessions-%s.tgz" % time.strftime("%Y%m%d-%H%M%S"))
    with open(dest, "wb") as out:
        subprocess.run(
            config.ssh_base() + ["-o", "BatchMode=yes", config.vm_name(name),
                                 "cd ~ && tar czf - --ignore-failed-read %s 2>/dev/null" % ARCHIVE_PATHS],
            stdout=out, timeout=1800,
        )
    return dest


def rm(name: str, force: bool) -> Dict[str, Any]:
    vm = config.vm_name(name)
    with registry.operation(name, "rm"):
        m = machine0.get(vm)
        archived = None
        if m is None:
            say("%s does not exist on machine0; forgetting it" % vm)
        elif machine0.status(m) == machine0.RUNNING:
            sshconf.update(vm, machine0.ip(m) or "")
            try:
                work = remote_json(vm, "spoke work", timeout=900)
            except (RuntimeError, ValueError) as e:
                if not force:
                    raise RuntimeError("could not check %s for unpushed work (%s); use --force" % (vm, e))
                work = []
            if work and not force:
                raise RuntimeError("%s has work that is not pushed:\n  %s\nPush it, or use --force."
                                   % (vm, "\n  ".join(work)))
            archived = archive(name)
            say("archived sessions to %s" % archived)
        elif not force:
            raise RuntimeError("%s is %s; wake it so its work can be checked, or use --force"
                               % (vm, machine0.status(m).lower()))
        if m is not None:
            machine0.destroy(vm)
        sshconf.remove(vm)
        registry.drop(name)
    say("%s removed. Remove the host from the Paseo apps too (Settings, the host, Remove)." % vm)
    return {"name": name, "vm": vm, "removed": True, "archive": archived}


# ---- golden image -------------------------------------------------------------

SCRUB_SCRIPT = r"""
set -e
# A snapshot taken mid-install leaves dpkg "interrupted" in every clone, and
# DigitalOcean's first-boot agent install then retries forever, so cloud-init
# never finishes (and machine0's profile, gh's login, never lands). Stop the
# periodic apt jobs, let any running one finish, and repair.
sudo systemctl stop apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
sudo systemctl stop apt-daily.service apt-daily-upgrade.service unattended-upgrades.service 2>/dev/null || true
while sudo fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock >/dev/null 2>&1; do sleep 3; done
sudo dpkg --configure -a
sudo apt-get -o DPkg::Lock::Timeout=600 -qq -f install -y >/dev/null
sync
systemctl --user stop paseo.service 2>/dev/null || true
rm -f ~/.paseo/daemon-keypair.json ~/.paseo/server-id ~/.paseo/push-tokens.json ~/.paseo/local-credential \
      ~/.paseo/cli-client-id ~/.paseo/paseo.pid ~/.paseo/daemon.log* ~/.paseo/*-daemon.log
rm -rf ~/.paseo/agents ~/.paseo/projects ~/.paseo/schedules ~/.paseo/desktop-attachments ~/.paseo/worktrees
rm -f ~/.config/paseo-machine0/secrets.env ~/.config/paseo-machine0/brokered-auth.json \
      ~/.local/state/paseo-machine0/restart-pending
rm -f ~/.pi/agent/auth.json ~/.pi/agent/mcp-auth.json ~/.codex/auth.json ~/.claude/.credentials.json \
      ~/.local/share/opencode/auth.json ~/.config/gh/hosts.yml \
      ~/.zsh_history ~/.bash_history ~/.local/share/atuin/history.db
rm -rf ~/.pi/agent/sessions ~/.claude/projects ~/.codex/sessions ~/.local/share/opencode/storage
"""


def image_build(fresh: bool) -> Dict[str, Any]:
    cfg = config.settings()
    images = machine0.run_json(["images", "ls"])
    have = any(i.get("name") == cfg["image"] for i in images if isinstance(i, dict))
    base = cfg["base_image"] if fresh or not have else cfg["image"]
    if machine0.get(config.BUILDER):
        say("removing a leftover %s" % config.BUILDER)
        machine0.destroy(config.BUILDER)
    say("building %s from %s" % (cfg["image"], base))
    machine0.new(config.BUILDER, "large", cfg["region"], base, cfg["ssh_key"], None)
    version = None
    try:
        bring_up(config.BUILDER)
        bootstrap(config.BUILDER)
        remote(config.BUILDER, SCRUB_SCRIPT, timeout=300)
        # machine0 only snapshots a stopped instance, and `images save` returns
        # before the snapshot exists, so stop first and wait for it after.
        say("stopping %s" % config.BUILDER)
        machine0.run(["stop", config.BUILDER], timeout=600)
        wait_status(config.BUILDER, machine0.STOPPED)
        before = set(image_versions(cfg["image"])) if have else set()
        out = machine0.run(["images", "save", config.BUILDER, cfg["image"]], timeout=3600)
        m = re.search(r"v(\d+)\b", out)
        added = sorted(set(image_versions(cfg["image"])) - before)
        version = added[-1] if added else (int(m.group(1)) if m else None)
        say("snapshotting (this takes a while)")
        wait_image(cfg["image"], version)
        if version is None:
            # Never report success with the old version still live.
            raise RuntimeError("could not tell which version the snapshot created; promote it with "
                               "`machine0 images versions promote %s <n>`" % cfg["image"])
        # Promoting retires the previous version. machine0 only deletes drafts,
        # so retired versions are its to manage.
        machine0.run(["images", "versions", "promote", cfg["image"], str(version)])
        say("image %s ready" % cfg["image"])
    finally:
        machine0.destroy(config.BUILDER)
        sshconf.remove(config.BUILDER)
    return {"image": cfg["image"], "version": version, "base": base}


def wait_status(vm: str, want: str, timeout: float = 900) -> None:
    deadline = time.time() + timeout
    while machine0.status(machine0.get(vm)) != want:
        if time.time() > deadline:
            raise RuntimeError("%s did not reach %s" % (vm, want))
        time.sleep(10)


BUSY = ("PENDING", "CREATING", "SNAPSHOT", "PROGRESS", "SAVING", "BUILD", "VERIFY", "CLEANUP", "TRANSFER")


def wait_image(image: str, version: Optional[int], timeout: float = 3600) -> None:
    deadline = time.time() + timeout
    while True:
        if version is None:
            entry = next((i for i in machine0.run_json(["images", "ls"])
                          if isinstance(i, dict) and i.get("name") == image), {})
            state = str(entry.get("status") or "")
        else:
            versions = machine0.run_json(["images", "versions", "ls", image])
            if isinstance(versions, dict):
                versions = versions.get("versions") or []
            entry = next((v for v in versions if isinstance(v, dict) and int(v.get("version") or 0) == version), {})
            state = str(entry.get("snapshotStatus") or "")
        up = state.upper()
        if up and not any(b in up for b in BUSY):
            if "ERROR" in up or "FAIL" in up:
                raise RuntimeError("image %s: %s" % (image, state))
            return
        if time.time() > deadline:
            raise RuntimeError("image %s still %s" % (image, state or "missing"))
        time.sleep(20)


def image_versions(image: str) -> List[int]:
    try:
        versions = machine0.run_json(["images", "versions", "ls", image])
    except machine0.Machine0Error:
        return []
    if isinstance(versions, dict):
        versions = versions.get("versions") or []
    return [int(v["version"]) for v in versions if isinstance(v, dict) and v.get("version")]
