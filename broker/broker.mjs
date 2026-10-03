#!/usr/bin/env node
// paseo-machine0 credential broker (runs on the hub only).
//
//   node broker.mjs <pi-package-dir> <broker-dir> get <provider> <margin-ms>
//   node broker.mjs <pi-package-dir> <broker-dir> status
//
// <broker-dir>/auth.json is written by a normal pi `/login` with
// PI_CODING_AGENT_DIR=<broker-dir> (`paseo-machine0 secrets login`). It is the
// only place a subscription refresh token lives, and this script is the only
// thing that refreshes it, so rotating refresh tokens never race. Callers
// serialize invocations with a lock. Output never contains the refresh token.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const [piDir, dir, command, provider, marginMs] = process.argv.slice(2);

function fail(message) {
  process.stderr.write(`broker: ${message}\n`);
  process.exit(1);
}

if (!piDir || !dir || !command) fail("usage: broker.mjs <pi-dir> <broker-dir> get <provider> <margin-ms> | status");

const authPath = join(dir, "auth.json");
const readStore = () => {
  try {
    return JSON.parse(readFileSync(authPath, "utf8"));
  } catch {
    return {};
  }
};

if (command === "status") {
  const out = {};
  for (const [id, cred] of Object.entries(readStore())) {
    out[id] = { type: cred?.type ?? null, expires: cred?.expires ?? null };
  }
  process.stdout.write(JSON.stringify(out) + "\n");
  process.exit(0);
}

if (command !== "get" || !provider) fail("unknown command");

const sdk = await import(pathToFileURL(join(piDir, "dist", "index.js")).href);
const runtime = await sdk.ModelRuntime.create({ authPath, modelsPath: null, refreshOnCreate: false });
const margin = Number(marginMs) > 0 ? Number(marginMs) : 24 * 3600 * 1000;
// getAuth refreshes (and persists) when less than `margin` remains.
const auth = await runtime.getAuth(provider, { minOAuthValidityMs: margin });
if (!auth) fail(`no credential for ${provider}; run: paseo-machine0 secrets login`);
const cred = readStore()[provider];
if (!cred || cred.type !== "oauth") fail(`${provider} is not an OAuth login in the broker store`);
const { refresh: _refresh, ...rest } = cred;
process.stdout.write(JSON.stringify(rest) + "\n");
