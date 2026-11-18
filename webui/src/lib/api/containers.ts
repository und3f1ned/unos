import { api, currentSession, currentSessionKey, handleUnauthorized } from './client';
import { WS_CLOSE_FORBIDDEN, WS_CLOSE_UNAUTHORIZED } from '$lib/stores/stats';

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

export async function getImages(): Promise<Image[]> {
  return api.get<Image[]>('/containers/images');
}

export async function pullImage(image: string): Promise<Image> {
  return api.post<Image>(`/containers/images/pull?image=${encodeURIComponent(image)}`);
}

export async function removeImage(id: string, force = false): Promise<void> {
  return api.delete(`/containers/images/${id}?force=${force}`);
}

/** How a container's log stream ended: the log ended (for example, the container stopped),
 * the server refused it (close code 4403), or the connection was lost. */
export type LogStreamEnd = 'ended' | 'forbidden' | 'lost';

export interface ContainerLogHandlers {
  /** The server took the session: the last lines of the log follow, then each new one. */
  onLive(): void;
  /** A piece of the log: one or more lines. */
  onLog(data: string): void;
  /** The server could not read the log, for example of a container that is gone. */
  onError(message: string): void;
  /** The stream is over. Not called after close(). */
  onEnd(reason: LogStreamEnd): void;
}

export interface ContainerLogStream {
  /** Close the stream; no handler is called after this. */
  close(): void;
}

/**
 * Follow a container's log over /ws/v1/logs/{id} (supervisor/src/websocket/events.py):
 * the first message authenticates it with the session cookie (ADR-0031, ADR-0397), and a close
 * with 4401 ends the session like an HTTP 401 does.
 */
export function streamContainerLogs(id: string, handlers: ContainerLogHandlers): ContainerLogStream {
  const session = currentSession();
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(
    `${protocol}//${window.location.host}/ws/v1/logs/${encodeURIComponent(id)}`
  );
  let done = false;

  socket.onopen = () => {
    // No token: the server takes the session cookie of the handshake, with the session key (ADR-0397).
    socket.send(JSON.stringify({ type: 'auth', key: currentSessionKey() }));
  };

  socket.onmessage = (event) => {
    let message: { type?: unknown; data?: unknown; message?: unknown };
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    if (message.type === 'auth_ok') handlers.onLive();
    else if (message.type === 'log' && typeof message.data === 'string') handlers.onLog(message.data);
    else if (message.type === 'error' && typeof message.message === 'string') {
      handlers.onError(message.message);
    }
  };

  // No onerror handler: an error event is always followed by this close event.
  socket.onclose = (event) => {
    if (done) return;
    done = true;
    if (event.code === WS_CLOSE_UNAUTHORIZED) {
      // The session ended or the account is gone: same as an HTTP 401.
      handleUnauthorized(session);
      handlers.onEnd('lost');
    } else if (event.code === WS_CLOSE_FORBIDDEN) {
      handlers.onEnd('forbidden');
    } else {
      // 1000: the server finished the stream: the log ended (the container stopped) or could
      // not be read. Any other code, 1006 of a dropped connection included, is a lost one.
      handlers.onEnd(event.code === 1000 ? 'ended' : 'lost');
    }
  };

  return {
    close() {
      if (done) return;
      done = true;
      socket.onopen = null;
      socket.onmessage = null;
      socket.onclose = null;
      if (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN) {
        socket.close(1000);
      }
    }
  };
}
