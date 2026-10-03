import type { RpcInput, RpcOutput } from "@getpaseo/plugin";
import type { actionRpc, listRpc, pairLinkRpc, Spoke } from "../shared/spokes";
import { listJobs, runJson, startJob } from "./cli";

export async function list({ live }: RpcInput<typeof listRpc>): Promise<RpcOutput<typeof listRpc>> {
  try {
    const spokes = await runJson<Spoke[]>(live ? ["ls"] : ["ls", "--cached"], live ? 60_000 : 20_000);
    return { spokes, jobs: listJobs(), error: null };
  } catch (error) {
    return { spokes: [], jobs: listJobs(), error: (error as Error).message };
  }
}

export async function action(input: RpcInput<typeof actionRpc>): Promise<RpcOutput<typeof actionRpc>> {
  switch (input.action) {
    case "keep-awake": {
      await runJson(["keep-awake", input.name, input.on ? "on" : "off"]);
      return { job: null, message: `${input.name}: keep-awake ${input.on ? "on" : "off"}` };
    }
    case "new": {
      const args = ["new", input.name];
      if (input.size) args.push("--size", input.size);
      for (const repo of input.repos) args.push("--repo", repo);
      return { job: startJob("new", input.name, args), message: `creating paseo-${input.name}` };
    }
    case "rm": {
      const args = ["rm", input.name, ...(input.force ? ["--force"] : [])];
      return { job: startJob("rm", input.name, args), message: `removing paseo-${input.name}` };
    }
    default: {
      const args = input.action === "push-creds" ? ["push-creds", input.name] : [input.action, input.name];
      return { job: startJob(input.action, input.name, args), message: `${input.action} ${input.name}` };
    }
  }
}

export async function pairLink({ name, refresh }: RpcInput<typeof pairLinkRpc>): Promise<RpcOutput<typeof pairLinkRpc>> {
  const out = await runJson<{ url: string; app_url: string; qr: string }>(
    ["pair-link", name, ...(refresh ? ["--refresh"] : [])],
    refresh ? 180_000 : 20_000,
  );
  return { url: out.url, app_url: out.app_url, qr: out.qr ?? "" };
}
