import { api } from "./client";

export interface PowerStatus {
  uptime: string;
  uptime_seconds: number;
  last_boot: string;
  load_average: number[];
}

export interface PowerConfig {
  wake_on_lan: boolean;
  auto_shutdown: boolean;
  shutdown_hour: number;
  shutdown_minute: number;
  auto_wakeup: boolean;
  wakeup_hour: number;
  wakeup_minute: number;
  idle_shutdown: boolean;
  idle_timeout_minutes: number;
}

/** An Ethernet interface of the host and its Wake-on-LAN state (ADR-0232). */
export interface WolInterface {
  name: string;
  mac_address: string | null;
  /** Whether a magic packet can wake the host through it. */
  supported: boolean;
  /** ethtool's "Wake-on" letters: "d" is off, "" when ethtool shows none. */
  wol: string;
  /** Whether a magic packet wakes the host. */
  enabled: boolean;
}

/** The reboot or shutdown pending on the host; both null when there is none. */
export interface PendingAction {
  action: "reboot" | "shutdown" | null;
  at: string | null;
}

/** The answer to a reboot, shutdown, cancel or suspend. */
export interface PowerAnswer {
  status: "scheduled" | "cancelled" | "none" | "suspending";
  message: string;
  /** What is pending afterwards; null after a suspend. */
  pending: PendingAction | null;
}

export async function getPowerStatus(): Promise<PowerStatus> {
  return api.get<PowerStatus>("/power/status");
}

export async function getPowerConfig(): Promise<PowerConfig> {
  return api.get<PowerConfig>("/power/config");
}

export async function updatePowerConfig(config: PowerConfig): Promise<PowerConfig> {
  return api.put<PowerConfig>("/power/config", config);
}

export async function getPendingAction(): Promise<PendingAction> {
  return api.get<PendingAction>("/power/pending");
}

export async function rebootSystem(): Promise<PowerAnswer> {
  return api.post<PowerAnswer>("/power/reboot");
}

export async function shutdownSystem(): Promise<PowerAnswer> {
  return api.post<PowerAnswer>("/power/shutdown");
}

export async function cancelShutdown(): Promise<PowerAnswer> {
  return api.post<PowerAnswer>("/power/cancel");
}

export async function suspendSystem(): Promise<PowerAnswer> {
  return api.post<PowerAnswer>("/power/suspend");
}

export async function getWolStatus(): Promise<{ interfaces: WolInterface[] }> {
  return api.get<{ interfaces: WolInterface[] }>("/power/wol");
}

export async function enableWol(iface: string): Promise<WolInterface> {
  return api.post<WolInterface>(`/power/wol/${encodeURIComponent(iface)}/enable`);
}

export async function disableWol(iface: string): Promise<WolInterface> {
  return api.post<WolInterface>(`/power/wol/${encodeURIComponent(iface)}/disable`);
}
