import { api, SESSION_KEY_HEADER, sessionStarted } from "./client";
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

/**
 * What the requirement of two-factor authentication for every account asks of an account without it
 * (src/routes/two-factor-setup).
 */
export interface TwoFactorRequired {
  /** The end of the account's grace period (UTC, with its offset). */
  by: string;
  /** The grace period is over: the account can only set up two-factor authentication. */
  setup_only: boolean;
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
  /** In GET /auth/me only: null unless every account must have 2FA and this one has none. */
  two_factor_required?: TwoFactorRequired | null;
}

/**
 * The answer of the web UI's sign-in (POST /auth/login with `cookie`, ADR-0397): the session is in
 * the HttpOnly session cookie, and `access_token` stays empty.
 */
export interface SignIn {
  /** The account has two-factor authentication: sign in again with a code. */
  requires_totp: boolean;
  /** What the page sends with the session cookie; empty at the code step. */
  session_key: string;
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
   * A new name (ADR-0368), sent with `username`. A user with SMB on also needs `password`: Samba's
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
  /** Every account must have two-factor authentication. */
  required: boolean;
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
 * the authenticator app or a recovery code, never both. The session goes into
 * the session cookie, which no script reads, and its key into this tab (ADR-0397);
 * the other tabs learn of it.
 */
export async function login(
  username: string,
  password: string,
  totp_code?: string,
  recovery_code?: string
): Promise<SignIn> {
  const answer = await api.post<SignIn>("/auth/login", {
    username,
    password,
    totp_code,
    recovery_code,
    cookie: true,
  });
  if (!answer.requires_totp) {
    sessionStarted(answer.session_key);
  }
  return answer;
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
 * End the session of the session cookie and of its session key `key` on the server, which also
 * deletes the cookie (ADR-0329, ADR-0397). The request skips the API client's handling of 401: a
 * 401 says that the cookie is no session that `key` goes with (ended, gone, or replaced), so that
 * nothing this browser holds is that session any more. True then; false when the server could not
 * be reached or failed, and the cookie may still be a session.
 */
export async function logout(key: string | null): Promise<boolean> {
  try {
    const response = await fetch("/api/v1/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: key ? { [SESSION_KEY_HEADER]: key } : {},
    });
    return response.ok || response.status === 401;
  } catch {
    return false;
  }
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

// API tokens (ADR-0239): only a session makes, disables and deletes them (ADR-0253). A token is
// full, or limited to reading and to areas of the API (ADR-0392).

/** The areas of the API, the first segment of a path after /api/v1/ (AREAS of supervisor/src/auth/scopes.py). */
export const TOKEN_AREAS = [
  "apps",
  "audit",
  "auth",
  "backup",
  "containers",
  "files",
  "groups",
  "health",
  "logs",
  "monitor",
  "network",
  "notifications",
  "offsite",
  "power",
  "scheduler",
  "security",
  "shares",
  "snapshots",
  "storage",
  "system",
  "updates",
] as const;
export type TokenArea = (typeof TOKEN_AREAS)[number];

/**
 * The areas whose change access reaches beyond them (WARNED_AREAS of scopes.py): programs that run
 * as root on the NAS, or the database with every account's password hash and two-factor secret.
 */
export const WARNED_AREAS: readonly TokenArea[] = ["apps", "backup", "containers", "offsite", "scheduler", "updates"];

/** GET /auth/tokens: a token of the signed-in account, never the token itself. */
export interface ApiToken {
  id: number;
  name: string;
  /** Its first 12 characters. */
  token_prefix: string;
  is_active: boolean;
  read_only: boolean;
  /** null: every area; else sorted. */
  areas: TokenArea[] | null;
  /** false: the stored scope cannot be read, and the token is refused everywhere. */
  scope_known: boolean;
  /** In UTC with their offset (ADR-0401). */
  created_at: string | null;
  last_used_at: string | null;
  expires_at: string | null;
}

export interface NewApiToken {
  name: string;
  read_only: boolean;
  /** null: every area. */
  areas: TokenArea[] | null;
  /** null: it never expires. */
  expires_in_days: number | null;
}

/** POST /auth/tokens: the token, answered only this once. */
export interface CreatedApiToken {
  id: number;
  name: string;
  token: string;
  token_prefix: string;
  read_only: boolean;
  areas: TokenArea[] | null;
  expires_at: string | null;
}

export async function listApiTokens(): Promise<ApiToken[]> {
  return api.get<ApiToken[]>("/auth/tokens");
}

export async function createApiToken(data: NewApiToken): Promise<CreatedApiToken> {
  return api.post<CreatedApiToken>("/auth/tokens", data);
}

/** Disable an active token, enable a disabled one. */
export async function toggleApiToken(id: number): Promise<{ is_active: boolean }> {
  return api.post<{ is_active: boolean }>(`/auth/tokens/${id}/toggle`);
}

export async function deleteApiToken(id: number): Promise<void> {
  await api.delete(`/auth/tokens/${id}`);
}
