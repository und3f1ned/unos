import { api } from "./client";

export interface NetworkInterface {
  name: string;
  mac: string;
  state: string;
  ip_addresses: string[];
  mtu: number;
  type: string;
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

export async function getInterfaces(): Promise<{ interfaces: NetworkInterface[] }> {
  return api.get<{ interfaces: NetworkInterface[] }>("/network/interfaces");
}

export async function getInterface(name: string): Promise<NetworkInterface> {
  return api.get<NetworkInterface>(`/network/interfaces/${name}`);
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

export async function bringInterfaceUp(name: string): Promise<{ status: string }> {
  return api.post<{ status: string }>(`/network/interfaces/${name}/up`);
}

export async function bringInterfaceDown(name: string): Promise<{ status: string }> {
  return api.post<{ status: string }>(`/network/interfaces/${name}/down`);
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
