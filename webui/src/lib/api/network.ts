import { api } from "./client";

export interface NetworkInterface {
  name: string;
  mac: string;
  /** The operational state: UP, DOWN (also without a link), UNKNOWN, ... */
  state: string;
  /** Administratively up: what Disable and Enable change. */
  enabled: boolean;
  /** IPv4 and IPv6 addresses with their prefix lengths. */
  ip_addresses: string[];
  /** Mb/s, while the link is up and the driver knows it. */
  speed: number | null;
  mtu: number;
  type: string;
  /** Seconds until a disable that waits to be kept comes back on by itself (ADR-0244). */
  revert_in: number | null;
}

export interface InterfaceList {
  interfaces: NetworkInterface[];
  /** How long a disable waits to be kept. */
  revert_seconds: number;
}

export interface InterfaceChange {
  status: string;
  interface: string;
  state: "up" | "down";
}

export interface InterfaceDisabled extends InterfaceChange {
  /** Seconds until the interface comes back on by itself, unless the change is kept. */
  revert_in: number;
}

export interface NetworkStats {
  interface: string;
  rx_bytes: number;
  tx_bytes: number;
  rx_packets: number;
  tx_packets: number;
  rx_errors: number;
  tx_errors: number;
}

export interface DNSConfig {
  nameservers: string[];
  search_domains: string[];
}

export interface HostnameConfig {
  hostname: string;
}

export interface Route {
  dst: string;
  gateway?: string;
  dev: string;
  protocol?: string;
  scope?: string;
  prefsrc?: string;
}

export interface GatewayInfo {
  gateway: string | null;
  interface: string | null;
}

export interface Connection {
  protocol: string;
  state: string;
  local: string;
  remote: string;
}

function interfacePath(name: string): string {
  return `/network/interfaces/${encodeURIComponent(name)}`;
}

export async function getInterfaces(): Promise<InterfaceList> {
  return api.get<InterfaceList>("/network/interfaces");
}

export async function getInterface(name: string): Promise<NetworkInterface> {
  return api.get<NetworkInterface>(interfacePath(name));
}

export async function getNetworkStats(): Promise<{ stats: NetworkStats[] }> {
  return api.get<{ stats: NetworkStats[] }>("/network/stats");
}

export async function getDNSConfig(): Promise<DNSConfig> {
  return api.get<DNSConfig>("/network/dns");
}

export async function getHostname(): Promise<HostnameConfig> {
  return api.get<HostnameConfig>("/network/hostname");
}

export async function setHostname(hostname: string): Promise<{ status: string; hostname: string }> {
  return api.put<{ status: string; hostname: string }>("/network/hostname", { hostname });
}

export async function getRoutes(): Promise<{ routes: Route[] }> {
  return api.get<{ routes: Route[] }>("/network/routes");
}

export async function getDefaultGateway(): Promise<GatewayInfo> {
  return api.get<GatewayInfo>("/network/gateway");
}

/** Enable an interface, with the addresses and routes its disable saved. */
export async function bringInterfaceUp(name: string): Promise<InterfaceChange> {
  return api.post<InterfaceChange>(`${interfacePath(name)}/up`);
}

/** Disable an interface: it comes back on by itself after revert_in seconds unless the change is kept. */
export async function bringInterfaceDown(name: string): Promise<InterfaceDisabled> {
  return api.post<InterfaceDisabled>(`${interfacePath(name)}/down`);
}

/** Keep a disable that waits to be kept; the server answers 409 once it was turned back on. */
export async function keepInterfaceDown(name: string): Promise<InterfaceChange> {
  return api.post<InterfaceChange>(`${interfacePath(name)}/down/confirm`);
}

export async function getConnections(): Promise<{ connections: Connection[] }> {
  return api.get<{ connections: Connection[] }>("/network/connections");
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}
