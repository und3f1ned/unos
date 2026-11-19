import { api } from "./client";

// iSCSI targets of volumes (supervisor/src/shares/iscsi.py, ADR-0417): one LUN each, a sparse file
// in a volume, with CHAP for every allowed initiator. A secret is only in the answer of a create
// and of a change that sets a new one: the page shows it once.

export interface IscsiTarget {
  name: string;
  iqn: string;
  pool: string;
  volume: string;
  size_gib: number;
  /** The LUN's file on the host. */
  path: string;
  initiators: string[];
  chap_user: string;
  mutual: boolean;
  /** The kernel serves it now. */
  served: boolean;
  /** Why it is not served, in the server's words. */
  problem: string | null;
  /** The initiators logged in now. */
  sessions: string[];
  created: string | null;
}

export interface IscsiState {
  kernel_support: boolean;
  /** address:3260 of each LAN address of the host. */
  portals: string[];
  portals_problem: string | null;
  targets: IscsiTarget[];
}

export interface IscsiTargetCreate {
  name: string;
  pool: string;
  volume: string;
  size_gib: number;
  initiators: string[];
  chap_user?: string;
  /** Left out: the server generates one. */
  chap_secret?: string;
  mutual: boolean;
  mutual_secret?: string;
}

export interface IscsiTargetChange {
  initiators?: string[];
  new_secret?: boolean;
  chap_secret?: string;
  mutual?: boolean;
  new_mutual_secret?: boolean;
  mutual_secret?: string;
}

export interface IscsiSecrets {
  target: IscsiTarget;
  chap_secret: string | null;
  mutual_secret: string | null;
}

export async function getIscsi(): Promise<IscsiState> {
  return api.get<IscsiState>("/shares/iscsi");
}

export async function createIscsiTarget(target: IscsiTargetCreate): Promise<IscsiSecrets> {
  return api.post<IscsiSecrets>("/shares/iscsi/targets", target);
}

export async function changeIscsiTarget(name: string, change: IscsiTargetChange): Promise<IscsiSecrets> {
  return api.patch<IscsiSecrets>(`/shares/iscsi/targets/${encodeURIComponent(name)}`, change);
}

export async function deleteIscsiTarget(name: string, confirm: string): Promise<{ name: string; sessions: string[] }> {
  return api.post(`/shares/iscsi/targets/${encodeURIComponent(name)}/delete`, { confirm });
}
