import { api } from "./client";

export interface SmartAttribute {
  id: number;
  name: string;
  value: number;
  worst: number;
  thresh: number;
  raw_value: string;
  status: "ok" | "warning" | "critical";
}

export interface SmartInfo {
  device: string;
  model: string;
  serial: string;
  firmware: string;
  capacity: string;
  temperature: number | null;
  power_on_hours: number | null;
  power_cycle_count: number | null;
  health_status: "PASSED" | "FAILED" | "UNKNOWN";
  attributes: SmartAttribute[];
  self_test_status: string | null;
  last_test_result: string | null;
}

export interface HealthOverview {
  total_disks: number;
  healthy: number;
  warning: number;
  failed: number;
  unknown: number;
  average_temperature: number | null;
}

export async function getDiskHealth(): Promise<{ disks: SmartInfo[] }> {
  return api.get<{ disks: SmartInfo[] }>("/health/disks");
}

export async function getDiskSmart(device: string): Promise<SmartInfo> {
  const encodedDevice = encodeURIComponent(device);
  return api.get<SmartInfo>("/health/disk/" + encodedDevice);
}

export async function startSmartTest(device: string, testType: string = "short"): Promise<{ status: string; message: string }> {
  const encodedDevice = encodeURIComponent(device);
  return api.post<{ status: string; message: string }>("/health/disk/" + encodedDevice + "/test?test_type=" + testType, {});
}

export async function getHealthOverview(): Promise<HealthOverview> {
  return api.get<HealthOverview>("/health/overview");
}

export function getHealthColor(status: string): string {
  switch (status) {
    case "PASSED":
      return "text-green-500";
    case "FAILED":
      return "text-red-500";
    default:
      return "text-yellow-500";
  }
}

export function getHealthBgColor(status: string): string {
  switch (status) {
    case "PASSED":
      return "bg-green-500";
    case "FAILED":
      return "bg-red-500";
    default:
      return "bg-yellow-500";
  }
}

export function getAttributeStatusColor(status: string): string {
  switch (status) {
    case "ok":
      return "text-green-400";
    case "warning":
      return "text-yellow-400";
    case "critical":
      return "text-red-400";
    default:
      return "text-slate-400";
  }
}

export function formatPowerOnHours(hours: number | null): string {
  if (hours === null) return "N/A";
  const days = Math.floor(hours / 24);
  const years = Math.floor(days / 365);
  if (years > 0) {
    return years + " years, " + (days % 365) + " days";
  }
  return days + " days, " + (hours % 24) + " hours";
}
