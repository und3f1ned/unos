import { api } from "./client";

/** One widget of the dashboard in the user's order, and whether the user hid it (ADR-0395). */
export interface LayoutEntry {
  id: string;
  hidden: boolean;
}

const PATH = "/auth/me/dashboard-layout";

/** The signed-in user's layout as the server keeps it; no entries means the default layout. */
export async function getDashboardLayout(): Promise<LayoutEntry[]> {
  return (await api.get<{ widgets: LayoutEntry[] }>(PATH)).widgets;
}

export async function saveDashboardLayout(widgets: LayoutEntry[]): Promise<void> {
  await api.put(PATH, { widgets });
}

/** Back to the default layout: the server keeps nothing for the user. */
export async function resetDashboardLayout(): Promise<void> {
  await api.delete(PATH);
}
