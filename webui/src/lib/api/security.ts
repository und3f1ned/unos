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

// Firewall Types
export type FirewallAction = "allow" | "deny" | "reject";
export type FirewallProtocol = "tcp" | "udp" | "any";
export type FirewallDirection = "in" | "out";

export interface FirewallRule {
  id: string | null;
  name: string;
  action: FirewallAction;
  direction: FirewallDirection;
  protocol: FirewallProtocol;
  port: string | null;
  source: string | null;
  destination: string | null;
  enabled: boolean;
  comment: string | null;
}

export interface FirewallStatus {
  enabled: boolean;
  default_incoming: string;
  default_outgoing: string;
  rules: FirewallRule[];
}

export interface FirewallRuleCreate {
  name: string;
  action: FirewallAction;
  direction?: FirewallDirection;
  protocol?: FirewallProtocol;
  port?: string | null;
  source?: string | null;
  destination?: string | null;
  comment?: string | null;
}

// Certificate Types
export type CertificateType = "self_signed" | "lets_encrypt" | "custom";

export interface Certificate {
  id: string;
  type: CertificateType;
  domain: string;
  issuer: string | null;
  valid_from: string | null;
  valid_until: string | null;
  is_valid: boolean;
  auto_renew: boolean;
  fingerprint: string | null;
}

export interface CertificateListResponse {
  certificates: Certificate[];
  active_certificate: string | null;
}

export interface CertificateStatus {
  has_certificate: boolean;
  type: CertificateType | null;
  domain: string | null;
  valid_until: string | null;
  is_valid: boolean;
  auto_renew: boolean;
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

export async function enableFirewall(): Promise<{ status: string; message: string }> {
  return api.post("/security/firewall/enable");
}

export async function disableFirewall(): Promise<{ status: string; message: string }> {
  return api.post("/security/firewall/disable");
}

export async function createFirewallRule(
  rule: FirewallRuleCreate
): Promise<FirewallRule> {
  return api.post<FirewallRule>("/security/firewall/rules", rule);
}

export async function deleteFirewallRule(
  ruleId: string
): Promise<{ status: string; message: string }> {
  return api.delete(`/security/firewall/rules/${ruleId}`);
}

export async function resetFirewall(): Promise<{ status: string; message: string }> {
  return api.post("/security/firewall/reset");
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

export async function deleteCertificate(
  certId: string
): Promise<{ status: string; message: string }> {
  return api.delete(`/security/certificates/${certId}`);
}
