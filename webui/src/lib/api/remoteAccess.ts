import { api, apiFetch } from "./client";

// System > Remote access (ADR-0420): the WireGuard tunnel on the host and the local names of apps.

export interface RemotePeer {
  id: string;
  name: string;
  public_key: string;
  /** The peer's IPv4 address in the tunnel. */
  address: string;
  /** The last handshake while the tunnel is up, ISO 8601 in UTC; null before the first. */
  latest_handshake: string | null;
  received: number | null;
  sent: number | null;
}

export interface RemoteAccessStatus {
  /** Whether the host has wireguard-tools. */
  installed: boolean;
  /** Whether the tunnel comes up at boot. */
  enabled: boolean;
  /** Whether the tunnel is up now. */
  active: boolean;
  configured: boolean;
  port: number | null;
  network: string | null;
  /** The NAS's own address in the tunnel. */
  address: string | null;
  endpoint: string | null;
  /** The server's public key; its private key never leaves the host. */
  public_key: string | null;
  peers: RemotePeer[];
  /** Whether the host's ufw lets the tunnel's port in; null without ufw. */
  firewall_rule: boolean | null;
  defaults: { port: number; network: string };
}

export interface RemoteAccessSettings {
  port: number;
  network: string;
  endpoint: string;
}

export interface RemotePeerCreated {
  peer: RemotePeer;
  /** The device's WireGuard configuration: shown once, kept nowhere. */
  config: string;
}

export interface AppName {
  label: string;
  /** The whole name: <label>-<host name>.local. */
  name: string;
  app: string;
  port: number;
  /** Whether the app is installed and publishes that port now. */
  installed: boolean;
}

export interface AppNamesStatus {
  volume: boolean;
  /** Whether the host has avahi-publish (avahi-utils), which announces the names. */
  announcer: boolean;
  host_name: string;
  /** The host's IPv4 address on its default route now. */
  address: string | null;
  /** The address that avahi announces the names with now. */
  announced: string | null;
  names: AppName[];
  /** The published TCP ports of the installed apps, each app's web port first. */
  apps: { name: string; port: number }[];
}

export interface RemoteAccessAnswer {
  status: RemoteAccessStatus;
  /**
   * How far the NAS's clock is ahead of this browser's, in milliseconds, from the answer's Date
   * header (0 without one): the age of a handshake is the NAS's now minus its time, whatever the
   * browser's clock says.
   */
  clockAhead: number;
}

export async function getRemoteAccess(): Promise<RemoteAccessAnswer> {
  const response = await apiFetch("/network/remote-access");
  const status = (await response.json()) as RemoteAccessStatus;
  const sent = Date.parse(response.headers.get("date") ?? "");
  return { status, clockAhead: Number.isNaN(sent) ? 0 : sent - Date.now() };
}

export async function enableRemoteAccess(settings: RemoteAccessSettings): Promise<{ changed: boolean }> {
  return api.post<{ changed: boolean }>("/network/remote-access/enable", settings);
}

export async function disableRemoteAccess(): Promise<{ changed: boolean }> {
  return api.post<{ changed: boolean }>("/network/remote-access/disable");
}

export async function setRemoteEndpoint(endpoint: string): Promise<{ changed: boolean }> {
  return api.put<{ changed: boolean }>("/network/remote-access/endpoint", { endpoint });
}

/** A new peer: its configuration comes in this answer only. */
export async function addRemotePeer(name: string, publicKey: string | null): Promise<RemotePeerCreated> {
  return api.post<RemotePeerCreated>("/network/remote-access/peers", { name, public_key: publicKey });
}

export async function revokeRemotePeer(id: string): Promise<{ changed: boolean }> {
  return api.delete<{ changed: boolean }>(`/network/remote-access/peers/${encodeURIComponent(id)}`);
}

export async function getAppNames(): Promise<AppNamesStatus> {
  return api.get<AppNamesStatus>("/network/app-names");
}

export async function addAppName(label: string, app: string, port: number): Promise<AppNamesStatus> {
  return api.post<AppNamesStatus>("/network/app-names", { label, app, port });
}

export async function removeAppName(label: string): Promise<AppNamesStatus> {
  return api.delete<AppNamesStatus>(`/network/app-names/${encodeURIComponent(label)}`);
}

export async function applyAppNames(): Promise<AppNamesStatus> {
  return api.post<AppNamesStatus>("/network/app-names/apply");
}
