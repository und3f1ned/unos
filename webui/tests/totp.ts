import { createHmac } from 'node:crypto';

/**
 * TOTP codes (RFC 6238) for the specs that sign in with two-factor authentication, computed as
 * authenticator apps compute them: SHA-1, 6 digits, 30-second steps. The server accepts a code
 * only in its own step (valid_window 0) and each step only once per user (ADR-0067).
 */

/** The length of a time step, in milliseconds. */
export const TOTP_STEP_MS = 30_000;

function base32Bytes(text: string): Buffer {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  const bytes: number[] = [];
  let bits = 0;
  let value = 0;
  for (const char of text.replace(/=+$/, '').toUpperCase()) {
    const index = alphabet.indexOf(char);
    if (index < 0) throw new Error(`not base32: ${char}`);
    value = ((value << 5) | index) & 0xffff;
    bits += 5;
    if (bits >= 8) {
      bytes.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return Buffer.from(bytes);
}

/** The time step of `time` (milliseconds since the epoch). */
export function totpStep(time: number): number {
  return Math.floor(time / TOTP_STEP_MS);
}

/** The 6-digit TOTP code of `secret` (base32) at `time` (milliseconds since the epoch). */
export function totpCode(secret: string, time: number): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(totpStep(time)));
  const digest = createHmac('sha1', base32Bytes(secret)).update(counter).digest();
  const offset = digest[digest.length - 1] & 0xf;
  return ((digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000).toString().padStart(6, '0');
}

/** A code of the current step, not of its last seconds: the server checks one step only. */
export async function currentTotpCode(secret: string): Promise<string> {
  const left = TOTP_STEP_MS - (Date.now() % TOTP_STEP_MS);
  if (left < 3_000) await new Promise((resolve) => setTimeout(resolve, left + 200));
  return totpCode(secret, Date.now());
}

/**
 * Wait for the start of a step after `step`, and return it: a code of that step is valid for
 * almost all of its 30 seconds. A step that began more than a second ago is not taken.
 */
export async function startOfStepAfter(step: number): Promise<number> {
  let next = step + 1;
  if (Date.now() > next * TOTP_STEP_MS + 1_000) next = totpStep(Date.now()) + 1;
  const wait = next * TOTP_STEP_MS + 300 - Date.now();
  if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
  return next;
}
