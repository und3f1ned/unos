import { api } from "./client";

export interface EmailConfig {
  smtp_host: string;
  smtp_port: number;
  smtp_user: string;
  smtp_password: string;
  smtp_tls: boolean;
  from_address: string;
  to_addresses: string[];
}

export interface TelegramConfig {
  bot_token: string;
  chat_id: string;
}

export interface NotificationConfig {
  id?: string;
  name: string;
  channel: "email" | "telegram";
  enabled: boolean;
  email_config?: EmailConfig;
  telegram_config?: TelegramConfig;
  events: string[];
}

export interface NotificationEvent {
  type: "info" | "warning" | "error" | "success";
  title: string;
  message: string;
  timestamp?: string;
}

export interface NotificationLog {
  id: string;
  event: NotificationEvent;
  channel: string;
  sent_at: string;
  success: boolean;
  error?: string;
}

export interface EventType {
  id: string;
  name: string;
  description: string;
}

export async function getConfigs(): Promise<NotificationConfig[]> {
  return api.get<NotificationConfig[]>("/notifications/configs");
}

export async function createConfig(config: NotificationConfig): Promise<NotificationConfig> {
  return api.post<NotificationConfig>("/notifications/configs", config);
}

export async function updateConfig(id: string, config: NotificationConfig): Promise<NotificationConfig> {
  return api.put<NotificationConfig>(`/notifications/configs/${id}`, config);
}

export async function deleteConfig(id: string): Promise<void> {
  return api.delete(`/notifications/configs/${id}`);
}

export async function testNotification(id: string): Promise<{ status: string; channel: string }> {
  return api.post<{ status: string; channel: string }>(`/notifications/test/${id}`);
}

export async function getLogs(): Promise<NotificationLog[]> {
  return api.get<NotificationLog[]>("/notifications/logs");
}

export async function getEventTypes(): Promise<EventType[]> {
  return api.get<EventType[]>("/notifications/events");
}

export async function sendNotification(event: NotificationEvent): Promise<void> {
  return api.post("/notifications/send", event);
}
