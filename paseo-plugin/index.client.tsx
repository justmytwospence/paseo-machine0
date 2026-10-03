import type { PluginClientContext } from "@getpaseo/plugin/client";
import { SpokesSurface } from "./client/spokes";

export default function contribute(client: PluginClientContext) {
  const cleanups = [
    client.addSurface("spokes", SpokesSurface),
    client.addSidebarItem({ id: "spokes", title: "Spokes", icon: "Server", surface: "spokes" }),
    client.addCommandCenterItem({
      id: "new-spoke",
      title: "New spoke",
      icon: "Server",
      keywords: ["machine0", "vm", "spokes"],
      context: "global",
      onSelect({ openSurface }) {
        openSurface("spokes");
      },
    }),
  ];
  return () => cleanups.forEach((cleanup) => cleanup());
}
