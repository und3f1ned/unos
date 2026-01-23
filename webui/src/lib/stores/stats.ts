import { writable } from 'svelte/store';
import type { SystemStats, TemperatureGroup } from '$lib/api/system';

export const stats = writable<SystemStats | null>(null);
export const wsConnected = writable(false);
export const temperatures = writable<TemperatureGroup[]>([]);

let ws: WebSocket | null = null;

export function connectWebSocket() {
  const token = localStorage.getItem('token');
  if (!token) return;

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/v1/events?token=${token}`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    wsConnected.set(true);
  };

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === 'stats') {
      stats.set(data.data);
    }
  };

  ws.onclose = () => {
    wsConnected.set(false);
    // Reconnect after 3 seconds
    setTimeout(connectWebSocket, 3000);
  };

  ws.onerror = () => {
    ws?.close();
  };
}

export function disconnectWebSocket() {
  ws?.close();
  ws = null;
}
