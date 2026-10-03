import type { PluginTheme } from "@getpaseo/plugin";
import type { PluginSurfaceProps } from "@getpaseo/plugin/client";
import { useRpc } from "@getpaseo/plugin/client";
import { Icon, Modal, ScrollView, TextInput, copyText, useToast } from "@getpaseo/plugin/client/react-native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Platform, Pressable, Text, View } from "react-native";
import { actionRpc, listRpc, pairLinkRpc, type Job, type Spoke } from "../shared/spokes";
import { canOpenAppLinks, openAppLink } from "./web";

type Styles = ReturnType<typeof makeStyles>;

function makeStyles(theme: PluginTheme, compact: boolean) {
  const c = theme.colors;
  return {
    screen: { flex: 1, backgroundColor: c.surface0 },
    content: { padding: compact ? 12 : 24, gap: 12 },
    header: { flexDirection: "row" as const, alignItems: "center" as const, gap: 8, flexWrap: "wrap" as const },
    title: { color: c.foreground, fontSize: 20, fontWeight: "600" as const, flexGrow: 1 },
    muted: { color: c.foregroundMuted, fontSize: 13 },
    text: { color: c.foreground, fontSize: 14 },
    card: { backgroundColor: c.surface1, borderColor: c.border, borderWidth: 1, borderRadius: 8, padding: 12, gap: 8 },
    row: { flexDirection: "row" as const, alignItems: "center" as const, gap: 8, flexWrap: "wrap" as const },
    name: { color: c.foreground, fontSize: 16, fontWeight: "600" as const },
    badge: (color: string) => ({ borderColor: color, borderWidth: 1, borderRadius: 4, paddingHorizontal: 6, paddingVertical: 1 }),
    badgeText: (color: string) => ({ color, fontSize: 12, fontWeight: "600" as const }),
    button: { flexDirection: "row" as const, alignItems: "center" as const, gap: 6, paddingHorizontal: 10,
      paddingVertical: 6, borderRadius: 6, borderWidth: 1, borderColor: c.border, backgroundColor: c.surface2 },
    primary: { backgroundColor: c.accent, borderColor: c.accent },
    buttonText: { color: c.foreground, fontSize: 13 },
    primaryText: { color: c.accentForeground, fontSize: 13 },
    input: { color: c.foreground, borderColor: c.border, borderWidth: 1, borderRadius: 6, padding: 8, fontSize: 14 },
    log: { color: c.foregroundMuted, fontSize: 11, fontFamily: Platform.select({ ios: "Menlo", default: "monospace" }) },
    qrBox: { backgroundColor: "#ffffff", padding: 12, alignSelf: "center" as const },
    qr: { color: "#000000", fontSize: 8, lineHeight: 8, fontFamily: Platform.select({ ios: "Menlo", default: "monospace" }) },
    error: { color: c.statusDanger, fontSize: 13 },
  };
}

function stateColor(theme: PluginTheme, spoke: Spoke): string {
  if (spoke.operation) return theme.colors.statusWarning;
  if (spoke.machine === "RUNNING") return spoke.reachable === false ? theme.colors.statusDanger : theme.colors.statusSuccess;
  if (spoke.machine === "SUSPENDED") return theme.colors.foregroundMuted;
  return theme.colors.statusWarning;
}

function notes(spoke: Spoke): string[] {
  const out: string[] = [];
  if (spoke.operation) out.push(spoke.operation);
  if (spoke.machine === "RUNNING") {
    out.push(`${spoke.agents_busy} running, ${spoke.agents_idle} idle`);
    if (spoke.idle_minutes !== null) out.push(`idle ${spoke.idle_minutes} min`);
    else if (spoke.idle_reason && spoke.idle_reason !== "idle") out.push(`awake: ${spoke.idle_reason}`);
    if (spoke.daemon === "down") out.push("Paseo daemon down");
    if (spoke.reachable === false) out.push("unreachable");
  }
  if (spoke.permissions) {
    const age = spoke.oldest_permission_hours;
    out.push(`${spoke.permissions} permission${spoke.permissions === 1 ? "" : "s"} pending${age !== null ? ` (${age}h)` : ""}`);
  }
  if (spoke.schedules_active) out.push(`${spoke.schedules_active} schedule${spoke.schedules_active === 1 ? "" : "s"}`);
  if (spoke.restart_pending) out.push("Paseo restart pending");
  if (spoke.keep_awake) out.push("keep-awake");
  if (spoke.machine !== "RUNNING" && spoke.suspended) out.push(`suspended (${spoke.suspended.reason})`);
  return out;
}

function Button(props: { label: string; icon?: string; onPress: () => void; primary?: boolean; disabled?: boolean;
  styles: Styles; theme: PluginTheme }) {
  const { styles, theme } = props;
  const color = props.primary ? theme.colors.accentForeground : theme.colors.foreground;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={props.label}
      disabled={props.disabled}
      onPress={props.onPress}
      style={[styles.button, props.primary ? styles.primary : null, props.disabled ? { opacity: 0.5 } : null]}
    >
      {props.icon ? <Icon name={props.icon} size={14} color={color} /> : null}
      <Text style={props.primary ? styles.primaryText : styles.buttonText}>{props.label}</Text>
    </Pressable>
  );
}

export function SpokesSurface({ theme, layout }: PluginSurfaceProps) {
  const styles = useMemo(() => makeStyles(theme, layout.compact), [theme, layout.compact]);
  const toast = useToast();
  const queryClient = useQueryClient();
  const list = useRpc(listRpc);
  const act = useRpc(actionRpc);
  const pairLink = useRpc(pairLinkRpc);
  const [live, setLive] = useState(false);
  const [creating, setCreating] = useState(false);
  const [removing, setRemoving] = useState<Spoke | null>(null);
  const [qr, setQr] = useState<{ name: string; text: string } | null>(null);

  const query = useQuery({
    queryKey: ["machine0", "spokes"],
    queryFn: async () => {
      const data = await list({ live });
      setLive(false);
      return data;
    },
    refetchInterval: (q) => ((q.state.data?.jobs ?? []).some((j) => j.state === "running") ? 4000 : 15000),
  });
  const refresh = (useLive = false) => {
    setLive(useLive);
    void queryClient.invalidateQueries({ queryKey: ["machine0", "spokes"] });
  };

  const run = useMutation({
    mutationFn: act,
    onSuccess: (out) => {
      toast.show(out.message, { variant: "success" });
      refresh();
    },
    onError: (error) => toast.error(String((error as Error).message ?? error)),
  });

  async function connect(spoke: Spoke, mode: "open" | "copy" | "qr") {
    try {
      const link = await pairLink({ name: spoke.name, refresh: !spoke.paired });
      if (mode === "open") await openAppLink(link.app_url);
      else if (mode === "copy") {
        await copyText(link.url);
        toast.show("Pairing link copied. Add host, Paste pairing link.", { variant: "success", durationMs: 4000 });
      } else setQr({ name: spoke.vm, text: link.qr });
    } catch (error) {
      toast.error(`${spoke.vm}: ${(error as Error).message}`);
    }
  }

  const data = query.data;
  const spokes = data?.spokes ?? [];
  const jobs = data?.jobs ?? [];
  const props = { styles, theme };

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <Text style={styles.title}>Spokes</Text>
        <Button {...props} label="Refresh" icon="RefreshCw" onPress={() => refresh(true)} />
        <Button {...props} label="New spoke" icon="Plus" primary onPress={() => setCreating(true)} />
      </View>
      {data?.error ? <Text style={styles.error}>{data.error}</Text> : null}
      {query.isLoading ? <Text style={styles.muted}>Loading…</Text> : null}
      {!query.isLoading && spokes.length === 0 && !data?.error ? (
        <Text style={styles.muted}>No spokes yet. New spoke creates a machine0 VM from the golden image.</Text>
      ) : null}

      {spokes.map((spoke) => {
        const color = stateColor(theme, spoke);
        const running = spoke.machine === "RUNNING";
        const busy = Boolean(spoke.operation);
        return (
          <View key={spoke.name} style={styles.card}>
            <View style={styles.row}>
              <Text style={styles.name}>{spoke.vm}</Text>
              <View style={styles.badge(color)}>
                <Text style={styles.badgeText(color)}>{spoke.machine.toLowerCase()}</Text>
              </View>
              <Text style={styles.muted}>
                {spoke.size}
                {spoke.price_per_hour ? ` · $${spoke.price_per_hour.toFixed(3)}/h` : ""}
              </Text>
            </View>
            <Text style={styles.muted}>{notes(spoke).join(" · ")}</Text>
            <View style={styles.row}>
              {canOpenAppLinks ? (
                <Button {...props} label="Connect" icon="Link" primary disabled={!running && !spoke.paired}
                  onPress={() => void connect(spoke, "open")} />
              ) : null}
              <Button {...props} label="Copy link" icon="Copy" disabled={!running && !spoke.paired}
                onPress={() => void connect(spoke, "copy")} />
              {!canOpenAppLinks ? (
                <Button {...props} label="QR" icon="QrCode" disabled={!running && !spoke.paired}
                  onPress={() => void connect(spoke, "qr")} />
              ) : null}
              {running ? (
                <Button {...props} label="Suspend" icon="Moon" disabled={busy}
                  onPress={() => run.mutate({ action: "suspend", name: spoke.name })} />
              ) : (
                <Button {...props} label="Wake" icon="Sun" primary={!canOpenAppLinks} disabled={busy}
                  onPress={() => run.mutate({ action: "wake", name: spoke.name })} />
              )}
              <Button {...props} label={spoke.keep_awake ? "Allow suspend" : "Keep awake"} icon="Pin"
                onPress={() => run.mutate({ action: "keep-awake", name: spoke.name, on: !spoke.keep_awake })} />
              <Button {...props} label="Remove" icon="Trash2" disabled={busy} onPress={() => setRemoving(spoke)} />
            </View>
          </View>
        );
      })}

      {jobs.length ? <Text style={[styles.text, { marginTop: 8 }]}>Recent operations</Text> : null}
      {jobs.map((job: Job) => (
        <View key={job.id} style={styles.card}>
          <Text style={styles.text}>
            {job.action} {job.name}: {job.state}
          </Text>
          {job.log ? <Text style={styles.log} selectable>{job.log}</Text> : null}
        </View>
      ))}

      <NewSpokeModal open={creating} onClose={() => setCreating(false)} styles={styles} theme={theme}
        onCreate={(name, size, repos) => {
          setCreating(false);
          run.mutate({ action: "new", name, size: size || undefined, repos });
        }} />

      <Modal title={`Remove ${removing?.vm ?? ""}`} open={removing !== null} onOpenChange={(o) => !o && setRemoving(null)}>
        <Modal.Content>
          <Text style={styles.text}>
            This destroys the VM. It refuses when a repo has uncommitted or unpushed work; sessions are archived on the
            hub first.
          </Text>
          <View style={styles.row}>
            <Button {...props} label="Remove" icon="Trash2" primary onPress={() => {
              if (removing) run.mutate({ action: "rm", name: removing.name, force: false });
              setRemoving(null);
            }} />
            <Button {...props} label="Force remove" onPress={() => {
              if (removing) run.mutate({ action: "rm", name: removing.name, force: true });
              setRemoving(null);
            }} />
          </View>
        </Modal.Content>
      </Modal>

      <Modal title={`Pair ${qr?.name ?? ""}`} open={qr !== null} onOpenChange={(o) => !o && setQr(null)}>
        <Modal.Content>
          <Text style={styles.muted}>In the Paseo app on the phone: Add host, Scan QR code.</Text>
          <View style={styles.qrBox}>
            <Text style={styles.qr}>{qr?.text ?? ""}</Text>
          </View>
        </Modal.Content>
      </Modal>
    </ScrollView>
  );
}

function NewSpokeModal(props: { open: boolean; onClose: () => void; styles: Styles; theme: PluginTheme;
  onCreate: (name: string, size: string, repos: string[]) => void }) {
  const { styles, theme } = props;
  const [name, setName] = useState("");
  const [size, setSize] = useState("large");
  const [repos, setRepos] = useState("");
  const repoList = repos.split(/[\s,]+/).filter(Boolean);
  const valid = /^[a-z][a-z0-9-]{0,30}$/.test(name) && repoList.every((r) => /^[\w.-]+\/[\w.-]+$/.test(r));
  return (
    <Modal title="New spoke" open={props.open} onOpenChange={(o) => !o && props.onClose()}>
      <Modal.Content>
        <Text style={styles.muted}>Name (the VM is paseo-name)</Text>
        <TextInput style={styles.input} value={name} onChangeText={setName} autoCapitalize="none"
          autoCorrect={false} placeholder="my-project" placeholderTextColor={theme.colors.foregroundMuted} />
        <Text style={styles.muted}>Size</Text>
        <TextInput style={styles.input} value={size} onChangeText={setSize} autoCapitalize="none" autoCorrect={false} />
        <Text style={styles.muted}>Repos to clone (owner/repo, comma separated)</Text>
        <TextInput style={styles.input} value={repos} onChangeText={setRepos} autoCapitalize="none"
          autoCorrect={false} placeholder="justmytwospence/app" placeholderTextColor={theme.colors.foregroundMuted} />
        <Button styles={styles} theme={theme} label="Create" icon="Plus" primary disabled={!valid}
          onPress={() => {
            props.onCreate(name, size.trim(), repoList);
            setName("");
            setRepos("");
          }} />
      </Modal.Content>
    </Modal>
  );
}
