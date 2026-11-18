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
  /** STANDBY: the disk sleeps, and its SMART data was not read so as not to wake it (ADR-0378). */
  health_status: "PASSED" | "FAILED" | "UNKNOWN" | "STANDBY";
  /** ATA attributes; NVMe disks have none. */
  attributes: SmartAttribute[];
  /**
   * The numbers of an NVMe disk's SMART/Health Information log, by smartctl's
   * names and in its order (critical_warning, available_spare, ...); null for
   * other disks.
   */
  nvme_health: Record<string, number> | null;
  self_test_status: string | null;
  last_test_result: string | null;
}

export type PredictionStatus = "healthy" | "watch" | "monitor_closely" | "replace_soon";

/** The failure prediction of a connected disk from its SMART history (ADR-0235). */
export interface DiskPrediction {
  /** smartctl's serial number, as SmartInfo.serial reports it. */
  serial: string;
  /** The disk's path now, as lsblk lists it. */
  device: string;
  risk_score: number;
  status: PredictionStatus;
  /** The reasons of the score. */
  risk_factors: string[];
  recommendation: string;
  data_points: number;
}

export interface HealthOverview {
  total_disks: number;
  healthy: number;
  warning: number;
  failed: number;
  unknown: number;
  /** Disks that sleep, whose SMART data was not read (ADR-0378). */
  standby: number;
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

/**
 * The failure predictions of the disks connected now. Reads the stored SMART
 * history only: no disk is woken. HTTP 500 when the host's disks cannot be listed.
 */
export async function getDiskPredictions(): Promise<{ predictions: DiskPrediction[] }> {
  return api.get<{ predictions: DiskPrediction[] }>("/health/smart/predictions");
}

/** The predictions that mark a disk "Replace soon" (ADR-0235). */
export function replaceSoon(predictions: DiskPrediction[]): DiskPrediction[] {
  return predictions.filter((prediction) => prediction.status === "replace_soon");
}

export function getHealthColor(status: string): string {
  switch (status) {
    case "PASSED":
      return "text-status-success";
    case "FAILED":
      return "text-status-error";
    default:
      return "text-status-warning";
  }
}

export function getHealthBgColor(status: string): string {
  switch (status) {
    case "PASSED":
      return "bg-status-success";
    case "FAILED":
      return "bg-status-error";
    default:
      return "bg-status-warning";
  }
}

export function getAttributeStatusColor(status: string): string {
  switch (status) {
    case "ok":
      return "text-status-success";
    case "warning":
      return "text-status-warning";
    case "critical":
      return "text-status-error";
    default:
      return "text-content-secondary";
  }
}
