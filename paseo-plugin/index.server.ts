import type { PluginServerContext } from "@getpaseo/plugin/server";
import { action, list, pairLink } from "./server/handlers";
import { actionRpc, listRpc, pairLinkRpc } from "./shared/spokes";

export default function contribute(server: PluginServerContext) {
  server.handle(listRpc, list);
  server.handle(actionRpc, action);
  server.handle(pairLinkRpc, pairLink);
  return () => {};
}
