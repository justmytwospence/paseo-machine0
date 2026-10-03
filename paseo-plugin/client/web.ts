import { Linking, Platform } from "react-native";

/**
 * Whether this client can add a host by opening a `paseo://pair#offer=...`
 * link. The phone apps register the paseo:// scheme and treat an offer link as
 * Add host (with a confirmation). The desktop app routes only agent links
 * through its protocol handler, so on desktop and web the screen offers Copy
 * link (then Add host, Paste pairing link) instead.
 */
export const canOpenAppLinks = Platform.OS === "ios" || Platform.OS === "android";

export async function openAppLink(url: string): Promise<void> {
  await Linking.openURL(url);
}
