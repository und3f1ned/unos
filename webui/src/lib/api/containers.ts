import { api } from './client';

export type ContainerState = 'running' | 'exited' | 'paused' | 'restarting' | 'created' | 'dead';

export interface PortMapping {
  host_port: number | null;
  container_port: number;
  protocol: string;
}

export interface VolumeMount {
  host_path: string;
  container_path: string;
  mode: string;
}

export interface Container {
  id: string;
  name: string;
  image: string;
  status: string;
  state: ContainerState;
  created: string;
  ports: PortMapping[];
  labels: Record<string, string>;
}

export interface ContainerCreate {
  name: string;
  image: string;
  ports?: PortMapping[];
  volumes?: VolumeMount[];
  environment?: Record<string, string>;
  restart_policy?: string;
  labels?: Record<string, string>;
}

export interface ContainerLogs {
  container_id: string;
  logs: string;
}

export interface Image {
  id: string;
  tags: string[];
  size: number;
  created: string;
}

export async function getContainers(all = true): Promise<Container[]> {
  return api.get<Container[]>(`/containers?all=${all}`);
}

export async function getContainer(id: string): Promise<Container> {
  return api.get<Container>(`/containers/${id}`);
}

export async function createContainer(config: ContainerCreate): Promise<Container> {
  return api.post<Container>('/containers', config);
}

export async function startContainer(id: string): Promise<Container> {
  return api.post<Container>(`/containers/${id}/start`);
}

export async function stopContainer(id: string): Promise<Container> {
  return api.post<Container>(`/containers/${id}/stop`);
}

export async function restartContainer(id: string): Promise<Container> {
  return api.post<Container>(`/containers/${id}/restart`);
}

export async function removeContainer(id: string, force = false): Promise<void> {
  return api.delete(`/containers/${id}?force=${force}`);
}

export async function getContainerLogs(id: string, tail = 100): Promise<ContainerLogs> {
  return api.get<ContainerLogs>(`/containers/${id}/logs?tail=${tail}`);
}

export async function getImages(): Promise<Image[]> {
  return api.get<Image[]>('/containers/images');
}

export async function pullImage(image: string): Promise<Image> {
  return api.post<Image>(`/containers/images/pull?image=${encodeURIComponent(image)}`);
}

export async function removeImage(id: string, force = false): Promise<void> {
  return api.delete(`/containers/images/${id}?force=${force}`);
}
