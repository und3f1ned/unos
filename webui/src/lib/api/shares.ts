import { api } from "./client";

export interface SmbShare {
  name: string;
  path: string;
  comment: string;
  browseable: boolean;
  readonly: boolean;
  guest_ok: boolean;
  valid_users: string[];
}

export interface SmbShareCreate {
  name: string;
  path: string;
  comment?: string;
  browseable?: boolean;
  readonly?: boolean;
  guest_ok?: boolean;
  valid_users?: string[];
}

export interface NfsExport {
  path: string;
  clients: string;
  options: string;
}

export interface NfsExportCreate {
  path: string;
  clients?: string;
  options?: string;
}

export interface SharePath {
  path: string;
  label: string;
  type: "pool" | "volume";
}

export async function getSharePaths(): Promise<SharePath[]> {
  return api.get<SharePath[]>("/shares/paths");
}

export async function getSmbShares(): Promise<SmbShare[]> {
  return api.get<SmbShare[]>("/shares/smb");
}

export async function getSmbShare(name: string): Promise<SmbShare> {
  return api.get<SmbShare>(`/shares/smb/${name}`);
}

export async function createSmbShare(share: SmbShareCreate): Promise<SmbShare> {
  return api.post<SmbShare>("/shares/smb", share);
}

export async function updateSmbShare(
  name: string,
  share: SmbShareCreate
): Promise<SmbShare> {
  return api.put<SmbShare>(`/shares/smb/${name}`, share);
}

export async function deleteSmbShare(name: string): Promise<void> {
  return api.delete(`/shares/smb/${name}`);
}

export async function getNfsExports(): Promise<NfsExport[]> {
  return api.get<NfsExport[]>("/shares/nfs");
}

export async function createNfsExport(exp: NfsExportCreate): Promise<NfsExport> {
  return api.post<NfsExport>("/shares/nfs", exp);
}

export async function deleteNfsExport(path: string): Promise<void> {
  return api.delete(`/shares/nfs?path=${encodeURIComponent(path)}`);
}
