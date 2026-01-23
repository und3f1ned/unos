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

export interface WolInterface {
  name: string;
  wol: string;
  enabled: boolean;
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

export async function rebootSystem(): Promise<void> {
  return api.post("/power/reboot");
}

export async function shutdownSystem(): Promise<void> {
  return api.post("/power/shutdown");
}

export async function cancelShutdown(): Promise<void> {
  return api.post("/power/cancel");
}

export async function suspendSystem(): Promise<void> {
  return api.post("/power/suspend");
}

export async function getWolStatus(): Promise<{ interfaces: WolInterface[] }> {
  return api.get<{ interfaces: WolInterface[] }>("/power/wol");
}

export async function enableWol(iface: string): Promise<void> {
  return api.post(`/power/wol/${iface}/enable`);
}

export async function disableWol(iface: string): Promise<void> {
  return api.post(`/power/wol/${iface}/disable`);
}
