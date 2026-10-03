import { defineRpc } from "@getpaseo/plugin";
import { z } from "zod";

// One row of `paseo-machine0 ls --json`.
export const spokeSchema = z.object({
  name: z.string(),
  vm: z.string(),
  machine: z.string(),
  size: z.string(),
  price_per_hour: z.number().nullable(),
  ip: z.string().nullable().optional(),
  keep_awake: z.boolean(),
  idle_minutes: z.number().nullable(),
  idle_reason: z.string().nullable().optional(),
  suspended: z.object({ reason: z.string(), at: z.number() }).nullable().optional(),
  operation: z.string().nullable().optional(),
  reachable: z.boolean().nullable().optional(),
  daemon: z.string().nullable().optional(),
  agents_busy: z.number(),
  agents_idle: z.number(),
  agents_error: z.number(),
  permissions: z.number(),
  oldest_permission_hours: z.number().nullable(),
  schedules_active: z.number(),
  restart_pending: z.boolean(),
  paseo_version: z.string().nullable().optional(),
  last_push: z.number().nullable().optional(),
  paired: z.boolean(),
  polled_at: z.number().nullable().optional(),
});
export type Spoke = z.infer<typeof spokeSchema>;

export const jobSchema = z.object({
  id: z.string(),
  action: z.string(),
  name: z.string(),
  state: z.enum(["running", "succeeded", "failed"]),
  started: z.number(),
  finished: z.number().nullable(),
  log: z.string(),
});
export type Job = z.infer<typeof jobSchema>;

export const listRpc = defineRpc({
  name: "machine0.list",
  input: z.object({ live: z.boolean() }),
  output: z.object({ spokes: z.array(spokeSchema), jobs: z.array(jobSchema), error: z.string().nullable() }),
});

export const SIZES = ["large", "xl", "xxl", "large-nvme", "xl-nvme", "xxl-nvme", "xl-premium", "xxl-premium", "xxxl"] as const;

export const actionRpc = defineRpc({
  name: "machine0.action",
  input: z.discriminatedUnion("action", [
    z.object({
      action: z.literal("new"),
      name: z.string().regex(/^[a-z][a-z0-9-]{0,30}$/),
      size: z.string().regex(/^[a-z0-9-]+$/).optional(),
      repos: z.array(z.string().regex(/^[\w.-]+\/[\w.-]+$/)).max(10),
    }),
    z.object({ action: z.enum(["wake", "suspend", "push-creds"]), name: z.string() }),
    z.object({ action: z.literal("rm"), name: z.string(), force: z.boolean() }),
    z.object({ action: z.literal("keep-awake"), name: z.string(), on: z.boolean() }),
  ]),
  output: z.object({ job: jobSchema.nullable(), message: z.string() }),
});

export const pairLinkRpc = defineRpc({
  name: "machine0.pair-link",
  input: z.object({ name: z.string(), refresh: z.boolean() }),
  output: z.object({ url: z.string(), app_url: z.string(), qr: z.string() }),
});
