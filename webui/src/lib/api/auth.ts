import { api, clearToken, getToken, setToken } from "./client";
import type { ShareAccessLevel, SmbAccountState } from "./shares";

/**
 * Names of new users (they are also SMB account names). The server checks it
 * again and also refuses names reserved for the system, such as "root".
 */
export const USERNAME_PATTERN = /^[a-z][a-z0-9_-]{0,31}$/;

/** SMB state of a UNOS user, derived by the server (never stored). */
export interface SmbAccountStatus {
  state: SmbAccountState;
  /** An administrator allowed SMB access. */
  allowed: boolean;
  /** The SMB sign-in name; set once SMB is allowed. */
  linux_name: string | null;
  uid: number | null;
  password_set_at: string | null;
  /** Why SMB is not available (administrator, or a username SMB cannot use). */
  reason: string | null;
}

export interface User {
  id: number;
  username: string;
  is_admin: boolean;
  totp_enabled: boolean;
  created_at: string | null;
  /** In GET /auth/users and the answers of the user endpoints; null in /auth/me. */
  smb?: SmbAccountStatus | null;
  /** Problems after a change that went through (e.g. smb.conf not updated). */
  warnings?: string[];
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
  /** Turn SMB on with the same password right away. */
  smb_enabled?: boolean;
}

export interface UserUpdate {
  is_admin?: boolean;
  password?: string;
  /** The username the change was prepared for: 409 when the id now belongs to another user. */
  username?: string;
  /**
   * A new name (ADR-0363), sent with `username`. A user with SMB on also needs `password`: Samba's
   * account of the new name is made with it.
   */
  new_username?: string;
}

/** A share the signed-in user can open over SMB (GET /auth/me/smb). */
export interface MySmbShare {
  name: string;
  comment: string;
  level: ShareAccessLevel;
  guest_ok: boolean;
  /** The account is active, or it is a guest share. */
  usable: boolean;
}

export interface MySmbStatus {
  account: SmbAccountStatus;
  /** The user has 2FA: turning SMB on needs the two-factor code. */
  totp_required: boolean;
  /** Sorted by name; no paths. */
  shares: MySmbShare[];
}

/** Answer of POST /auth/me/smb/enable and /disable: only `warnings` is read. */
export interface SmbChangeResult {
  warnings?: string[];
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

/** GET /auth/recovery-codes/status. */
export interface RecoveryCodesStatus {
  /** Codes not used yet. */
  remaining: number;
  /** Codes were generated for the current two-factor setup (also when all are used). */
  generated: boolean;
}

/**
 * Sign in. At the code step of an account with 2FA, send either the code of
 * the authenticator app or a recovery code, never both.
 */
export async function login(
  username: string,
  password: string,
  totp_code?: string,
  recovery_code?: string
): Promise<Token> {
  const token = await api.post<Token>("/auth/login", {
    username,
    password,
    totp_code,
    recovery_code,
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

/**
 * Sign out: forget the token, then end its session on the server (ADR-0329). The request carries
 * the token it ends and skips the API client's handling of 401, so a session that has already
 * ended signs out all the same; when the server cannot be reached, the session ends at its expiry.
 */
export async function logout(): Promise<void> {
  const token = getToken();
  clearToken();
  if (!token) return;
  try {
    await fetch("/api/v1/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${token}` } });
  } catch {
    // Unreachable: nothing more to do here.
  }
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

/** End every session of a user (ADR-0329); `username` is the name the request was prepared for. */
export async function endSessions(userId: number, username: string): Promise<{ status: string; ended: number }> {
  return api.post<{ status: string; ended: number }>(
    `/auth/users/${userId}/end-sessions?username=${encodeURIComponent(username)}`,
    {}
  );
}

/**
 * Delete a user. `username` is the name the request was prepared for: the
 * server answers 409 when the id now belongs to someone else.
 */
export async function deleteUser(
  userId: number,
  username: string
): Promise<{ status: string; message: string; warnings?: string[] }> {
  return api.delete<{ status: string; message: string; warnings?: string[] }>(
    `/auth/users/${userId}?username=${encodeURIComponent(username)}`
  );
}

// SMB access (administrators). `username` is the name the request was
// prepared for: the server answers 409 when the id now belongs to someone else.

/**
 * Allow SMB for a user. With `password`, also set it as the user's UNOS and
 * SMB password and turn SMB on; without, the user turns SMB on in the
 * Account page.
 */
export async function enableUserSmb(
  userId: number,
  username: string,
  password?: string
): Promise<User> {
  const body: { username: string; password?: string } = { username };
  if (password !== undefined) body.password = password;
  return api.post<User>(`/auth/users/${userId}/smb/enable`, body);
}

/** Disallow SMB for a user; their open SMB connections are ended. */
export async function disableUserSmb(userId: number, username: string): Promise<User> {
  return api.post<User>(`/auth/users/${userId}/smb/disable`, { username });
}

// SMB access of the signed-in user (Account page).

export async function getMySmb(): Promise<MySmbStatus> {
  return api.get<MySmbStatus>("/auth/me/smb");
}

/** Turn SMB on with the current UNOS password (and the 2FA code, if the user has 2FA). */
export async function enableMySmb(
  currentPassword: string,
  totpCode?: string
): Promise<SmbChangeResult> {
  const body: { current_password: string; totp_code?: string } = {
    current_password: currentPassword,
  };
  if (totpCode) body.totp_code = totpCode;
  return api.post<SmbChangeResult>("/auth/me/smb/enable", body);
}

/** Turn SMB off; open SMB connections are ended. */
export async function disableMySmb(): Promise<SmbChangeResult> {
  return api.post<SmbChangeResult>("/auth/me/smb/disable");
}

/** Answer of POST /auth/change-password. */
export interface PasswordChangeResult {
  status: string;
  message: string;
  /** The password changed, but e.g. open SMB connections could not be ended. */
  warnings?: string[];
}

export async function changePassword(data: PasswordChange): Promise<PasswordChangeResult> {
  return api.post<PasswordChangeResult>("/auth/change-password", data);
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

// Recovery codes (only while 2FA is on): each one signs in once in place of a
// code of the authenticator app.

/** Ten new codes that replace the earlier ones; the server answers them only this once. */
export async function generateRecoveryCodes(): Promise<string[]> {
  const result = await api.post<{ codes: string[] }>("/auth/recovery-codes/generate");
  return result.codes;
}

export async function getRecoveryCodesStatus(): Promise<RecoveryCodesStatus> {
  return api.get<RecoveryCodesStatus>("/auth/recovery-codes/status");
}
