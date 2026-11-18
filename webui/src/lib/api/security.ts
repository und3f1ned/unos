import { api } from "./client";

// Login History Types
export interface LoginAttempt {
  id: number;
  username: string;
  ip_address: string | null;
  user_agent: string | null;
  success: boolean;
  failure_reason: string | null;
  timestamp: string | null;
}

export interface LoginHistoryResponse {
  attempts: LoginAttempt[];
  total: number;
  failed_count: number;
  success_count: number;
}

// Firewall Types: the host's ufw (ADR-0414)
export type FirewallAction = "allow" | "deny" | "reject";
export type FirewallProtocol = "tcp" | "udp" | "any";

/** A rule of the host's ufw, as ufw records it. */
export interface FirewallRule {
  id: string;
  /** ufw's number, as `ufw status numbered` shows it. */
  number: number;
  family: "ipv4" | "ipv6";
  /** allow, deny, reject or limit. */
  action: string;
  /** in or out. */
  direction: string;
  route: boolean;
  log: boolean;
  /** tcp, udp, any, or another protocol that ufw takes. */
  protocol: string;
  port: string | null;
  destination: string | null;
  source_port: string | null;
  /** null: anywhere. */
  source: string | null;
  interface: string | null;
  app: string | null;
  comment: string | null;
  /** One of UNOS's rules for the local networks, which UNOS does not delete. */
  unos: boolean;
}

export interface FirewallStatus {
  /** The host has ufw. */
  installed: boolean;
  enabled: boolean;
  default_incoming: string;
  default_outgoing: string;
  ipv6: boolean;
  rules: FirewallRule[];
  /** Seconds until a change that waits to be kept is undone; null when none waits. */
  revert_in: number | null;
  /** The host undoes a change now. */
  reverting: boolean;
  /** The host's last undo could not put everything back. */
  revert_failed: boolean;
  revert_seconds: number;
  /** The asking computer is in the local networks that UNOS's rules let in; null: not known. */
  client_local: boolean | null;
}

/** An incoming rule made in UNOS. */
export interface FirewallRuleCreate {
  action: FirewallAction;
  protocol: FirewallProtocol;
  port: string;
  source?: string | null;
  comment?: string | null;
}

/** The answer of a change: whether it changed the host, and the seconds it waits to be kept. */
export interface FirewallChange {
  changed: boolean;
  revert_in: number | null;
}

// Certificate Types
export type CertificateType = "self_signed" | "custom";

// What browsers get from https://<host>/, as a TLS handshake with the web UI's Caddy shows it
// (ADR-0404): its local CA's certificate, a stored one, or unknown when Caddy did not answer.
export type ServedCertificate = "caddy_local_ca" | "stored_certificate" | "unknown";

/** The id of Caddy's local CA in serveCertificate. */
export const LOCAL_CA_ID = "caddy_local_ca";

// A certificate stored by an upload or a self-signed generation.
export interface Certificate {
  id: string;
  type: CertificateType;
  domain: string;
  issuer: string | null;
  valid_from: string | null;
  valid_until: string | null;
  is_valid: boolean;
  /** Valid, and expires within 30 days. */
  expires_soon: boolean;
  /** Browsers get it. */
  served: boolean;
  /** An administrator chose it, whether Caddy loaded it or not. */
  chosen: boolean;
  /** SHA-256. */
  fingerprint: string | null;
}

export interface CertificateStatus {
  served_by: ServedCertificate;
  /** The stored certificate browsers get. */
  served_id: string | null;
  /** The stored certificate an administrator chose; null: Caddy's local CA. */
  chosen_id: string | null;
  /** SHA-256 of the certificate browsers get. */
  fingerprint: string | null;
}

export interface CertificateListResponse {
  certificates: Certificate[];
  status: CertificateStatus;
}

export interface ServeCertificateResponse {
  changed: boolean;
  expires_soon: boolean;
  status: CertificateStatus;
}

export interface CertificateUpload {
  certificate: string;
  private_key: string;
  chain?: string | null;
}

export interface SelfSignedCertRequest {
  domain: string;
  validity_days?: number;
  organization?: string | null;
  country?: string | null;
}

// Login History API
export async function getLoginHistory(
  limit: number = 50,
  offset: number = 0,
  username?: string,
  success?: boolean
): Promise<LoginHistoryResponse> {
  let path = `/security/login-history?limit=${limit}&offset=${offset}`;
  if (username) path += `&username=${encodeURIComponent(username)}`;
  if (success !== undefined) path += `&success=${success}`;
  return api.get<LoginHistoryResponse>(path);
}

export async function clearLoginHistory(
  olderThanDays: number = 30
): Promise<{ status: string; deleted_count: number }> {
  return api.delete(`/security/login-history?older_than_days=${olderThanDays}`);
}

// Firewall API
export async function getFirewallStatus(): Promise<FirewallStatus> {
  return api.get<FirewallStatus>("/security/firewall");
}

/** Turn the firewall on: UNOS's rules for the local networks first, and the change is undone
 *  unless it is kept (keepFirewallChange). */
export async function enableFirewall(): Promise<FirewallChange> {
  return api.post<FirewallChange>("/security/firewall/enable");
}

export async function disableFirewall(): Promise<FirewallChange> {
  return api.post<FirewallChange>("/security/firewall/disable");
}

/** Keep the change that waits: its automatic revert is called off. */
export async function keepFirewallChange(): Promise<{ status: string }> {
  return api.post<{ status: string }>("/security/firewall/confirm");
}

export async function createFirewallRule(rule: FirewallRuleCreate): Promise<FirewallChange> {
  return api.post<FirewallChange>("/security/firewall/rules", rule);
}

export async function deleteFirewallRule(ruleId: string): Promise<FirewallChange> {
  return api.delete<FirewallChange>(`/security/firewall/rules/${encodeURIComponent(ruleId)}`);
}

export async function resetFirewall(): Promise<FirewallChange> {
  return api.post<FirewallChange>("/security/firewall/reset");
}

// Certificate API
export async function getCertificates(): Promise<CertificateListResponse> {
  return api.get<CertificateListResponse>("/security/certificates");
}

export async function getCertificateStatus(): Promise<CertificateStatus> {
  return api.get<CertificateStatus>("/security/certificates/status");
}

export async function generateSelfSignedCert(
  request: SelfSignedCertRequest
): Promise<{ status: string; message: string; certificate_id: string }> {
  return api.post("/security/certificates/self-signed", request);
}

export async function uploadCertificate(
  data: CertificateUpload
): Promise<{ status: string; message: string; certificate_id: string; domain: string }> {
  return api.post("/security/certificates/upload", data);
}

/** Make the stored certificate `certId` the one the web UI's Caddy serves, or with LOCAL_CA_ID its local CA's. */
export async function serveCertificate(certId: string): Promise<ServeCertificateResponse> {
  return api.post(`/security/certificates/${encodeURIComponent(certId)}/serve`);
}

export async function deleteCertificate(
  certId: string
): Promise<{ status: string; message: string }> {
  return api.delete(`/security/certificates/${certId}`);
}

// Two-factor authentication for every account, administrators only.

/** The grace periods an administrator can choose, in days (GraceDays of the server's schemas). */
export const GRACE_DAYS_CHOICES = [0, 1, 3, 7, 14, 30] as const;
export type GraceDays = (typeof GRACE_DAYS_CHOICES)[number];
export const DEFAULT_GRACE_DAYS: GraceDays = 7;

export interface TwoFactorRequirement {
  enabled: boolean;
  grace_days: number | null;
  /** When it was turned on (UTC, with its offset). */
  enabled_at: string | null;
  enabled_by: string | null;
}

export async function getTwoFactorRequirement(): Promise<TwoFactorRequirement> {
  return api.get<TwoFactorRequirement>("/security/two-factor-requirement");
}

/** Turn it on; `code` is a current code of the administrator's own authenticator app. */
export async function enableTwoFactorRequirement(grace_days: GraceDays, code: string): Promise<TwoFactorRequirement> {
  return api.post<TwoFactorRequirement>("/security/two-factor-requirement/enable", { grace_days, code });
}

export async function disableTwoFactorRequirement(code: string): Promise<TwoFactorRequirement> {
  return api.post<TwoFactorRequirement>("/security/two-factor-requirement/disable", { code });
}
