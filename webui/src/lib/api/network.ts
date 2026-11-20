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

// Bonds, VLANs and bridges of the host (ADR-0424).

export type VirtualKind = "bond" | "vlan" | "bridge";
export type Addressing = "none" | "dhcp" | "static";

export interface VirtualInterface {
  kind: VirtualKind;
  name: string;
  /** Whether the host has it now. */
  present: boolean;
  /** UP, DOWN, ... or ABSENT. */
  state: string;
  mode: string | null;
  ports: string[];
  parent: string | null;
  vlan_id: number | null;
  mtu: number | null;
  addressing: Addressing | "copied";
  addresses: string[];
  /** The port whose network settings it took over. */
  copied_from: string | null;
  /** It carries the default route or the connection to UNOS: a change of it waits to be kept. */
  protected: boolean;
}

export interface HostInterface {
  name: string;
  /** physical, bond, bridge, vlan, or another kind of the kernel's. */
  kind: string;
  wireless: boolean;
  master: string | null;
  parent: string | null;
  managed: boolean;
  addresses: string[];
  protected: boolean;
}

export interface VirtualStatus {
  manager: "systemd-networkd" | "NetworkManager" | "ifupdown" | "none";
  supported: boolean;
  interfaces: VirtualInterface[];
  host_interfaces: HostInterface[];
  unreadable: string[];
  /** Seconds until the host undoes the change that waits to be kept. */
  revert_in: number | null;
  reverting: boolean;
  revert_failed: boolean;
  /** The change that waits to be kept. */
  change: WaitingChange | null;
  revert_seconds: number;
}

export interface WaitingChange {
  action: "create" | "delete";
  kind: VirtualKind;
  name: string;
}

export interface VirtualChange {
  status: string;
  action: "created" | "deleted";
  kind: VirtualKind;
  name: string;
  /** Seconds until the host undoes the change unless it is kept; null when it is kept already. */
  revert_in: number | null;
}

interface AddressingFields {
  mtu: number | null;
  addressing: Addressing;
  addresses: string[];
}

export interface BondCreate extends AddressingFields {
  name: string;
  mode: "802.3ad" | "balance-rr";
  ports: string[];
}

export interface VlanCreate extends AddressingFields {
  parent: string;
  vlan_id: number;
  name: string | null;
}

export interface BridgeCreate extends AddressingFields {
  name: string;
  ports: string[];
}

const VIRTUAL = "/network/virtual-interfaces";

export async function getVirtualInterfaces(): Promise<VirtualStatus> {
  return api.get<VirtualStatus>(VIRTUAL);
}

export async function createBond(spec: BondCreate): Promise<VirtualChange> {
  return api.post<VirtualChange>(`${VIRTUAL}/bonds`, spec);
}

export async function createVlan(spec: VlanCreate): Promise<VirtualChange> {
  return api.post<VirtualChange>(`${VIRTUAL}/vlans`, spec);
}

export async function createBridge(spec: BridgeCreate): Promise<VirtualChange> {
  return api.post<VirtualChange>(`${VIRTUAL}/bridges`, spec);
}

export async function deleteVirtualInterface(name: string): Promise<VirtualChange> {
  return api.delete<VirtualChange>(`${VIRTUAL}/${encodeURIComponent(name)}`);
}

/** Keep the change that waits; the server answers 409 once it was undone. */
export async function keepVirtualChange(): Promise<{ status: string; change: WaitingChange | null }> {
  return api.post<{ status: string; change: WaitingChange | null }>(`${VIRTUAL}/confirm`);
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}
