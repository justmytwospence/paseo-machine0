// The daemon side of the Spokes screen: every operation is the paseo-machine0
// CLI on this (hub) machine. Long operations run as detached jobs whose output
// and exit status live in files, so they survive a plugin reload or a daemon
// restart and the screen can follow them.
import { execFile, spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { Job } from "../shared/spokes";

const STATE = process.env.PASEO_MACHINE0_STATE_DIR ?? join(homedir(), ".local", "state", "paseo-machine0");
const JOBS = join(STATE, "jobs");
const JOB_TTL_MS = 24 * 3600 * 1000;

export function bin(): string {
  if (process.env.PASEO_MACHINE0_BIN) return process.env.PASEO_MACHINE0_BIN;
  const local = join(homedir(), ".local", "bin", "paseo-machine0");
  return existsSync(local) ? local : "paseo-machine0";
}

/** Run the CLI with --json and parse its output; its {"error"} becomes an exception. */
export function runJson<T>(args: string[], timeoutMs = 120_000): Promise<T> {
  return new Promise((resolve, reject) => {
    execFile(bin(), [...args, "--json"], { timeout: timeoutMs, maxBuffer: 8 * 1024 * 1024 }, (error, stdout, stderr) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(stdout);
      } catch {
        reject(new Error((stderr || String(error ?? "no output")).trim().slice(-400)));
        return;
      }
      if (parsed && typeof parsed === "object" && "error" in parsed && typeof (parsed as any).error === "string") {
        reject(new Error((parsed as any).error));
        return;
      }
      if (error) {
        reject(new Error((stderr || error.message).trim().slice(-400)));
        return;
      }
      resolve(parsed as T);
    });
  });
}

function tail(text: string, lines = 12): string {
  return text.split("\n").filter(Boolean).slice(-lines).join("\n");
}

function readJob(id: string): Job | null {
  try {
    const meta = JSON.parse(readFileSync(join(JOBS, `${id}.json`), "utf8"));
    let log = "";
    try {
      log = readFileSync(join(JOBS, `${id}.log`), "utf8");
    } catch {}
    let exit: number | null = null;
    let finished: number | null = null;
    try {
      exit = Number(readFileSync(join(JOBS, `${id}.exit`), "utf8").trim());
      finished = statSync(join(JOBS, `${id}.exit`)).mtimeMs;
    } catch {}
    return {
      id,
      action: meta.action,
      name: meta.name,
      started: meta.started,
      finished,
      state: exit === null ? "running" : exit === 0 ? "succeeded" : "failed",
      log: tail(log),
    };
  } catch {
    return null;
  }
}

export function listJobs(): Job[] {
  if (!existsSync(JOBS)) return [];
  const now = Date.now();
  return readdirSync(JOBS)
    .filter((f) => f.endsWith(".json"))
    .map((f) => readJob(f.slice(0, -5)))
    .filter((j): j is Job => j !== null && (j.state === "running" || now - j.started < JOB_TTL_MS))
    .sort((a, b) => b.started - a.started)
    .slice(0, 20);
}

/** Start `paseo-machine0 <args>` detached; its output goes to the job's log. */
export function startJob(action: string, name: string, args: string[]): Job {
  mkdirSync(JOBS, { recursive: true, mode: 0o700 });
  const id = `${Date.now()}-${action}-${name}`;
  const base = join(JOBS, id);
  writeFileSync(`${base}.json`, JSON.stringify({ action, name, started: Date.now() }), { mode: 0o600 });
  // Arguments are positional parameters of a fixed script, never interpolated.
  const child = spawn("sh", ["-c", '"$0" "$@" >"$JOB_LOG" 2>&1; echo $? >"$JOB_EXIT"', bin(), ...args], {
    detached: true,
    stdio: "ignore",
    env: { ...process.env, JOB_LOG: `${base}.log`, JOB_EXIT: `${base}.exit` },
  });
  child.unref();
  return readJob(id)!;
}
