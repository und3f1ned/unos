import { api, clearToken, getToken, setToken } from "./client";

export interface User {
  id: number;
  username: string;
  is_admin: boolean;
  totp_enabled: boolean;
  created_at: string | null;
}

export interface Token {
  access_token: string;
  token_type: string;
  requires_totp: boolean;
}

export interface AuthStatus {
  setup_required: boolean;
}

export interface UserCreate {
  username: string;
  password: string;
}

export interface UserUpdate {
  is_admin?: boolean;
  password?: string;
}

export interface PasswordChange {
  current_password: string;
  new_password: string;
}

export interface TOTPSetupResponse {
  secret: string;
  qr_uri: string;
}

export interface TOTPStatus {
  enabled: boolean;
}

export async function login(
  username: string,
  password: string,
  totp_code?: string
): Promise<Token> {
  const token = await api.post<Token>("/auth/login", {
    username,
    password,
    totp_code,
  });
  if (token.access_token) {
    setToken(token.access_token);
  }
  return token;
}

export async function setup(username: string, password: string): Promise<User> {
  return api.post<User>("/auth/setup", { username, password });
}

export async function getMe(): Promise<User> {
  return api.get<User>("/auth/me");
}

export async function getAuthStatus(): Promise<AuthStatus> {
  return api.get<AuthStatus>("/auth/status");
}

export function logout(): void {
  clearToken();
}

export function isAuthenticated(): boolean {
  return !!getToken();
}

// User management functions (admin only)
export async function getUsers(): Promise<User[]> {
  return api.get<User[]>("/auth/users");
}

export async function createUser(data: UserCreate): Promise<User> {
  return api.post<User>("/auth/users", data);
}

export async function updateUser(userId: number, data: UserUpdate): Promise<User> {
  return api.put<User>(`/auth/users/${userId}`, data);
}

export async function deleteUser(userId: number): Promise<void> {
  return api.delete(`/auth/users/${userId}`);
}

export async function changePassword(
  data: PasswordChange
): Promise<{ status: string; message: string }> {
  return api.post<{ status: string; message: string }>(
    "/auth/change-password",
    data
  );
}

// TOTP 2FA functions
export async function setupTOTP(): Promise<TOTPSetupResponse> {
  return api.post<TOTPSetupResponse>("/auth/totp/setup", {});
}

export async function enableTOTP(
  code: string
): Promise<{ status: string; message: string }> {
  return api.post<{ status: string; message: string }>("/auth/totp/enable", {
    code,
  });
}

export async function disableTOTP(
  code: string
): Promise<{ status: string; message: string }> {
  return api.post<{ status: string; message: string }>("/auth/totp/disable", {
    code,
  });
}

export async function getTOTPStatus(): Promise<TOTPStatus> {
  return api.get<TOTPStatus>("/auth/totp/status");
}
