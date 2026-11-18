import { api } from "./client";

export interface PowerStatus {
  uptime: string;
  uptime_seconds: number;
  last_boot: string;
  load_average: number[];
}

/** The power settings; the times are on the clock of the host's time zone (ADR-0406). */
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

/** One reason why the host is not idle, or why a scheduled shutdown waits (ADR-0406). */
export interface PowerFinding {
  kind:
    | "web_session"
    | "login_session"
    | "smb_client"
    | "nfs_client"
    | "disk_activity"
    | "task"
    | "task_due"
    | "backup"
    | "restore"
    | "scrub"
    | "balance"
    | "disk_replace"
    | "app_install"
    | "offsite"
    | "share_repair"
    | "update"
    | "wake_alarm"
    | "unreadable";
  /** A task's or an app's name, a pool's path, a client's address, a user, a disk. */
  subject: string | null;
  /** The pages open (web_session), the bytes moved in the last minute (disk_activity). */
  amount: number | null;
  /** The server's English text: what could not be read, the RTC's problem. */
  detail: string | null;
}

/** GET /power/schedule: what the power settings will do, and what holds them (ADR-0406). */
export interface PowerSchedule {
  /** The host's time zone, whose clock the times of the settings follow. */
  timezone: string;
  shutdown: {
    enabled: boolean;
    time: string;
    next: string | null;
    state: "off" | "planned" | "announced" | "postponed" | "issued";
    held_by: PowerFinding[];
  };
  wake: {
    enabled: boolean;
    time: string;
    /** null while the wake is off or the RTC cannot wake the host: no wake is promised. */
    next: string | null;
    rtc: { available: boolean; reason: string | null; alarm: string | null };
  };
  idle: {
    enabled: boolean;
    minutes: number;
    idle: boolean;
    since: string | null;
    shutdown_at: string | null;
    found: PowerFinding[];
  };
}

/** An Ethernet interface of the host and its Wake-on-LAN state (ADR-0233). */
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

export async function getPowerSchedule(): Promise<PowerSchedule> {
  return api.get<PowerSchedule>("/power/schedule");
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
