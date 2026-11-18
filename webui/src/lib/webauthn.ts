import type {
  SecurityKeyAssertion,
  SecurityKeyAttestation,
  SecurityKeyCreation,
  SecurityKeyRequest,
} from '$lib/api/auth';

/**
 * The browser's side of security keys (ADR-0406): the server's options as the arguments of
 * navigator.credentials.create and .get, and their answers in base64url, as the API takes them.
 * The server checks everything; nothing here decides whether a key is accepted.
 */

/** Whether this browser can use security keys at all. */
export function securityKeysSupported(): boolean {
  return typeof window !== 'undefined' && typeof window.PublicKeyCredential === 'function'
    && !!navigator.credentials;
}

/**
 * Whether the page is open at an IP address, where WebAuthn does not work: a host of numbers and
 * dots (as the URL Standard reads one ending in a number) or an IPv6 address in brackets.
 */
export function openedByIpAddress(hostname: string): boolean {
  return hostname.startsWith('[') || /(^|\.)(\d+|0x[0-9a-f]*)$/i.test(hostname);
}

function toBase64url(buffer: ArrayBuffer): string {
  let text = '';
  for (const byte of new Uint8Array(buffer)) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function fromBase64url(value: string): ArrayBuffer {
  const text = atob(value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (value.length % 4)) % 4));
  const bytes = new Uint8Array(text.length);
  for (let i = 0; i < text.length; i += 1) bytes[i] = text.charCodeAt(i);
  return bytes.buffer;
}

/** Register a key: the browser asks the user to touch it. */
export async function createCredential(options: SecurityKeyCreation): Promise<SecurityKeyAttestation> {
  const credential = (await navigator.credentials.create({
    publicKey: {
      challenge: fromBase64url(options.challenge),
      rp: { id: options.rp_id, name: options.rp_name },
      user: { id: fromBase64url(options.user_id), name: options.user_name, displayName: options.user_name },
      pubKeyCredParams: options.algorithms.map((alg) => ({ type: 'public-key' as const, alg })),
      timeout: options.timeout_ms,
      excludeCredentials: options.exclude_credentials.map((id) => ({
        type: 'public-key' as const,
        id: fromBase64url(id),
      })),
      authenticatorSelection: {
        residentKey: options.resident_key,
        requireResidentKey: false,
        userVerification: options.user_verification,
      },
      attestation: options.attestation,
    },
  })) as PublicKeyCredential | null;
  if (!credential) throw new DOMException('No credential', 'NotAllowedError');
  const response = credential.response as AuthenticatorAttestationResponse;
  return {
    credential_id: toBase64url(credential.rawId),
    client_data_json: toBase64url(response.clientDataJSON),
    attestation_object: toBase64url(response.attestationObject),
  };
}

/** Answer the challenge of the code step: the browser asks the user to touch one of the keys. */
export async function getAssertion(request: SecurityKeyRequest): Promise<SecurityKeyAssertion> {
  const credential = (await navigator.credentials.get({
    publicKey: {
      challenge: fromBase64url(request.challenge),
      rpId: request.rp_id,
      allowCredentials: request.credential_ids.map((id) => ({ type: 'public-key' as const, id: fromBase64url(id) })),
      timeout: request.timeout_ms,
      userVerification: request.user_verification,
    },
  })) as PublicKeyCredential | null;
  if (!credential) throw new DOMException('No credential', 'NotAllowedError');
  const response = credential.response as AuthenticatorAssertionResponse;
  return {
    credential_id: toBase64url(credential.rawId),
    client_data_json: toBase64url(response.clientDataJSON),
    authenticator_data: toBase64url(response.authenticatorData),
    signature: toBase64url(response.signature),
  };
}

/**
 * The catalog key of what went wrong in the browser: the user cancelled or the time ran out, the key
 * is already registered, or the browser refused this address. undefined for an error of another
 * kind, such as the server's ApiError, whose message the page shows.
 */
export function browserErrorKey(error: unknown): string | undefined {
  if (!(error instanceof DOMException)) return undefined;
  if (error.name === 'InvalidStateError') return 'securitykeys.error.alreadyRegistered';
  if (error.name === 'SecurityError') return 'securitykeys.error.address';
  if (error.name === 'NotSupportedError') return 'securitykeys.error.unsupported';
  return 'securitykeys.error.cancelled';
}
