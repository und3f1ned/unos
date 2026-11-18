import { api } from "./client";

/** The theme of the web UI that a user chooses on the Account page (ADR-0402). */
export type ThemeChoice = "dark" | "light" | "system";

const PATH = "/auth/me/theme";

/** The signed-in user's theme as the server keeps it; system when they never chose one. */
export async function getTheme(): Promise<ThemeChoice> {
  return (await api.get<{ theme: ThemeChoice }>(PATH)).theme;
}

export async function saveTheme(theme: ThemeChoice): Promise<void> {
  await api.put(PATH, { theme });
}
