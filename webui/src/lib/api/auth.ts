import { api } from './client';

export interface User {
  id: number;
  username: string;
  is_admin: boolean;
  created_at: string | null;
}

export interface Token {
  access_token: string;
  token_type: string;
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

export async function login(username: string, password: string): Promise<Token> {
  const token = await api.post<Token>('/auth/login', { username, password });
  localStorage.setItem('token', token.access_token);
  return token;
}

export async function setup(username: string, password: string): Promise<User> {
  return api.post<User>('/auth/setup', { username, password });
}

export async function getMe(): Promise<User> {
  return api.get<User>('/auth/me');
}

export async function getAuthStatus(): Promise<AuthStatus> {
  return api.get<AuthStatus>('/auth/status');
}

export function logout(): void {
  localStorage.removeItem('token');
}

export function isAuthenticated(): boolean {
  return !!localStorage.getItem('token');
}

// User management functions (admin only)
export async function getUsers(): Promise<User[]> {
  return api.get<User[]>('/auth/users');
}

export async function createUser(data: UserCreate): Promise<User> {
  return api.post<User>('/auth/users', data);
}

export async function updateUser(userId: number, data: UserUpdate): Promise<User> {
  return api.put<User>(`/auth/users/${userId}`, data);
}

export async function deleteUser(userId: number): Promise<void> {
  return api.delete(`/auth/users/${userId}`);
}

export async function changePassword(data: PasswordChange): Promise<{ status: string; message: string }> {
  return api.post<{ status: string; message: string }>('/auth/change-password', data);
}
