import { api } from './client';

export interface User {
  id: number;
  username: string;
  is_admin: boolean;
  created_at: string | null;
}

export interface UserCreate {
  username: string;
  password: string;
}

export interface PasswordChange {
  current_password: string;
  new_password: string;
}

export async function getUsers(): Promise<User[]> {
  return api.get<User[]>('/auth/users');
}

export async function createUser(data: UserCreate): Promise<User> {
  return api.post<User>('/auth/users', data);
}

export async function updateUser(id: number, data: { is_admin?: boolean; password?: string }): Promise<User> {
  return api.put<User>(`/auth/users/${id}`, data);
}

export async function deleteUser(id: number): Promise<void> {
  return api.delete(`/auth/users/${id}`);
}

export async function changePassword(data: PasswordChange): Promise<void> {
  return api.post('/auth/change-password', data);
}
