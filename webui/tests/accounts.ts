import { expect, type APIRequestContext } from '@playwright/test';
import { E2E_ADMIN } from './fixtures';
import { rememberSessionKey } from './session';

/**
 * Regular accounts that a spec creates for itself through the API, as E2E_ADMIN, and deletes
 * afterwards: the specs never change E2E_ADMIN's password or two-factor authentication, which
 * every other spec signs in with.
 */

export interface Credentials {
  username: string;
  password: string;
}

/** A regular account created by createAccount. */
export interface Account {
  id: number;
  adminToken: string;
}

/**
 * Sign in through the API with the password alone; returns the session token, for a Bearer header.
 * Its session key is remembered for useSessionCookie of tests/session.ts.
 */
export async function apiLogin(request: APIRequestContext, credentials: Credentials): Promise<string> {
  const response = await request.post('/api/v1/auth/login', { data: credentials });
  expect(response.ok(), `login as ${credentials.username}: HTTP ${response.status()}`).toBeTruthy();
  const body = await response.json();
  expect(body.requires_totp).toBe(false);
  rememberSessionKey(body.access_token, body.session_key);
  return body.access_token as string;
}

/** Create the regular account `user`, without SMB. */
export async function createAccount(request: APIRequestContext, user: Credentials): Promise<Account> {
  const adminToken = await apiLogin(request, E2E_ADMIN);
  const created = await request.post('/api/v1/auth/users', {
    data: user,
    headers: { Authorization: `Bearer ${adminToken}` },
  });
  expect(created.ok(), `create ${user.username}: HTTP ${created.status()}`).toBeTruthy();
  return { id: (await created.json()).id, adminToken };
}

/** Delete an account of createAccount, by its id and the name the request is meant for. */
export async function deleteAccount(request: APIRequestContext, account: Account | undefined, username: string) {
  if (account === undefined) return;
  // The server refuses the request (409) if the id names someone else by now.
  await request.delete(`/api/v1/auth/users/${account.id}?username=${encodeURIComponent(username)}`, {
    headers: { Authorization: `Bearer ${account.adminToken}` },
  });
}
