import { api } from "./client";

export interface PortMapping {
  host: number;
  container: number;
  protocol: string;
}

export interface VolumeMapping {
  host: string;
  container: string;
  readonly: boolean;
}

export interface EnvVar {
  name: string;
  value: string;
  description: string;
  required: boolean;
}

export interface AppTemplate {
  id: string;
  name: string;
  description: string;
  icon: string;
  category: string;
  image: string;
  version: string;
  website: string;
  source?: string;
  github?: string;
  documentation?: string;
  min_memory?: string;
  recommended_memory?: string;
  ports: PortMapping[];
  volumes: VolumeMapping[];
  environment: EnvVar[];
}

export interface InstalledApp {
  id: string;
  template_id: string;
  name: string;
  container_id: string;
  status: string;
  web_port: number | null;
  created_at: string;
}

export interface AppInstallRequest {
  template_id: string;
  name?: string;
  port_overrides?: Record<number, number>;
  volume_overrides?: Record<string, string>;
  env_overrides?: Record<string, string>;
}

export async function getAppCatalog(category?: string, query?: string): Promise<AppTemplate[]> {
  const params = new URLSearchParams();
  if (category) params.append("category", category);
  if (query) params.append("query", query);
  const qs = params.toString();
  return api.get<AppTemplate[]>(`/apps/catalog${qs ? "?" + qs : ""}`);
}

export async function getAppTemplate(appId: string): Promise<AppTemplate> {
  return api.get<AppTemplate>(`/apps/catalog/${appId}`);
}

export async function getInstalledApps(): Promise<InstalledApp[]> {
  return api.get<InstalledApp[]>("/apps/installed");
}

export async function installApp(req: AppInstallRequest): Promise<{ id: string; name: string; status: string; web_url: string | null }> {
  return api.post("/apps/install", req);
}

export async function uninstallApp(containerId: string): Promise<void> {
  return api.delete(`/apps/installed/${containerId}`);
}

export async function startApp(containerId: string): Promise<void> {
  return api.post(`/apps/installed/${containerId}/start`);
}

export async function stopApp(containerId: string): Promise<void> {
  return api.post(`/apps/installed/${containerId}/stop`);
}

export async function restartApp(containerId: string): Promise<void> {
  return api.post(`/apps/installed/${containerId}/restart`);
}
