// The form of the off-device copy (ADR-0337): the copy target with its password (a disk, an S3
// store, a REST server or an SFTP server with the host key pinned at its first test, ADR-0422),
// the folders to copy and the retention, with the server's rules for what an edit keeps.
import type { OffsiteKind, OffsiteSettingsAnswer, OffsiteSettingsRequest, OffsiteTls } from "$lib/api/offsite";
import { MIN_PASSWORD_LENGTH, normalized } from "./offsite";

export class OffsiteForm {
  // The stored settings and keys that the form edits: the section's last answer of the server.
  #answer: () => OffsiteSettingsAnswer | null;

  kind = $state<OffsiteKind>("disk");
  path = $state("");
  endpoint = $state("");
  bucket = $state("");
  prefix = $state("");
  region = $state("");
  accessKeyId = $state("");
  secretKey = $state("");
  // A REST server (endpoint and prefix are the S3 fields') and an SFTP server.
  username = $state("");
  restPassword = $state("");
  tls = $state<OffsiteTls>("system");
  certificate = $state("");
  certificateSha256 = $state("");
  sftpHost = $state("");
  sftpPort = $state("22");
  remotePath = $state("");
  hostKeyFingerprint = $state("");
  trustNewHostKey = $state(false);
  poolSources = $state<string[]>([]);
  extraSources = $state<string[]>([]);
  newSource = $state("");
  password = $state("");
  passwordAgain = $state("");
  limitUpload = $state("0");
  limitDownload = $state("0");
  keepDaily = $state("7");
  keepWeekly = $state("4");
  keepMonthly = $state("12");
  checkPercent = $state("2");

  constructor(answer: () => OffsiteSettingsAnswer | null) {
    this.#answer = answer;
  }

  stored = $derived.by(() => this.#answer()?.settings ?? null);
  sources = $derived([...this.poolSources, ...this.extraSources]);
  // The same SFTP server keeps its pinned host key (the server's rule).
  sameSftpServer = $derived(
    this.stored?.kind === "sftp" &&
      this.kind === "sftp" &&
      this.sftpHost.trim() === this.stored.host &&
      Number(this.sftpPort) === this.stored.port
  );
  // An edit of the same target keeps the stored password and key (the server's rule).
  sameTarget = $derived(
    this.stored !== null &&
      this.stored.kind === this.kind &&
      (this.kind === "disk"
        ? normalized(this.path) === this.stored.path
        : this.kind === "sftp"
          ? this.sameSftpServer && this.username.trim() === this.stored.username && this.remotePath.trim() === this.stored.path
          : this.endpoint.trim() === this.stored.endpoint &&
            (this.kind === "rest" || this.bucket.trim() === this.stored.bucket) &&
            (this.prefix.trim() || null) === (this.stored.prefix ?? null))
  );
  // The pin of the same server is kept; trusting a new key needs its fingerprint (the server's rule).
  pinShown = $derived.by(() => this.sameSftpServer && !!this.#answer()?.host_key_fingerprint);
  fingerprintShown = $derived(!this.pinShown || this.trustNewHostKey);
  keepsRestPassword = $derived.by(
    () =>
      !!this.#answer()?.has_rest_password &&
      this.stored?.kind === "rest" &&
      this.kind === "rest" &&
      this.endpoint.trim() === this.stored.endpoint &&
      this.username.trim() === this.stored.username
  );
  keepsPassword = $derived.by(() => !!this.#answer()?.has_password && this.sameTarget);
  keepsSecret = $derived.by(
    () =>
      !!this.#answer()?.has_s3_secret_access_key &&
      this.stored?.kind === "s3" &&
      this.kind === "s3" &&
      this.endpoint.trim() === this.stored.endpoint &&
      this.accessKeyId.trim() === this.stored.access_key_id
  );
  passwordTooShort = $derived(this.password !== "" && this.password.length < MIN_PASSWORD_LENGTH);
  passwordsDiffer = $derived(this.passwordAgain !== this.password);
  targetGiven = $derived.by(() =>
    this.kind === "disk"
      ? this.path.trim() !== ""
      : this.kind === "s3"
        ? !!(this.endpoint.trim() && this.bucket.trim() && this.accessKeyId.trim() && (this.secretKey || this.keepsSecret))
        : this.kind === "rest"
          ? !!(
              this.endpoint.trim() &&
              this.username.trim() &&
              (this.restPassword || this.keepsRestPassword) &&
              (this.tls !== "ca" || this.certificate.trim()) &&
              (this.tls !== "fingerprint" || this.certificateSha256.trim())
            )
          : !!(
              this.sftpHost.trim() &&
              this.username.trim() &&
              this.remotePath.trim() &&
              this.#answer()?.sftp_public_key &&
              (!this.pinShown || !this.trustNewHostKey || this.hostKeyFingerprint.trim())
            )
  );
  // What Save needs besides the end of a save that runs.
  complete = $derived(
    this.targetGiven &&
      this.sources.length > 0 &&
      !this.passwordTooShort &&
      !this.passwordsDiffer &&
      (this.password !== "" || this.keepsPassword)
  );

  // What Save still waits for, as keys of the catalog: said beside the disabled button.
  missing = $derived.by(() => {
    const keys: string[] = [];
    if (!this.targetGiven) {
      keys.push(this.kind === "sftp" && !this.#answer()?.sftp_public_key ? "offsite.form.need.key" : "offsite.form.need.target");
    }
    if (this.sources.length === 0) keys.push("offsite.form.need.sources");
    if ((this.password === "" && !this.keepsPassword) || this.passwordTooShort || this.passwordsDiffer) {
      keys.push("offsite.form.need.password");
    }
    return keys;
  });

  /** The form from the stored settings, or the defaults: every managed pool, the retention of restic's. */
  fill(found: OffsiteSettingsAnswer) {
    const settings = found.settings;
    const poolPaths = found.pools.map((pool) => pool.path);
    this.kind = settings?.kind ?? "disk";
    this.path = settings?.path ?? "";
    this.endpoint = settings?.endpoint ?? "";
    this.bucket = settings?.bucket ?? "";
    this.prefix = settings?.prefix ?? "";
    this.region = settings?.region ?? "";
    this.accessKeyId = settings?.access_key_id ?? "";
    this.username = settings?.username ?? "";
    this.tls = settings?.tls ?? "system";
    this.certificate = settings?.tls === "ca" ? (settings.certificate ?? "") : "";
    this.certificateSha256 = settings?.certificate_sha256 ?? "";
    this.sftpHost = settings?.host ?? "";
    this.sftpPort = String(settings?.port ?? 22);
    this.remotePath = settings?.kind === "sftp" ? (settings.path ?? "") : "";
    if (settings?.kind === "sftp") this.path = "";
    this.hostKeyFingerprint = "";
    this.trustNewHostKey = false;
    this.restPassword = "";
    this.poolSources = settings ? settings.sources.filter((source) => poolPaths.includes(source)) : poolPaths;
    this.extraSources = settings ? settings.sources.filter((source) => !poolPaths.includes(source)) : [];
    this.limitUpload = String(settings?.limit_upload_kib ?? 0);
    this.limitDownload = String(settings?.limit_download_kib ?? 0);
    this.keepDaily = String(settings?.keep_daily ?? 7);
    this.keepWeekly = String(settings?.keep_weekly ?? 4);
    this.keepMonthly = String(settings?.keep_monthly ?? 12);
    this.checkPercent = String(settings?.check_percent ?? 2);
    this.password = this.passwordAgain = this.secretKey = "";
  }

  addSource() {
    const value = this.newSource.trim();
    if (value && !this.sources.includes(value)) this.extraSources = [...this.extraSources, value];
    this.newSource = "";
  }

  removeSource(source: string) {
    this.extraSources = this.extraSources.filter((other) => other !== source);
  }

  /** The settings to save: only the fields of the kind of target. */
  request(): OffsiteSettingsRequest {
    const kind = this.kind;
    const tls = this.tls;
    const remote = kind === "s3" || kind === "rest";
    return {
      kind,
      path: kind === "disk" ? this.path.trim() : kind === "sftp" ? this.remotePath.trim() : null,
      endpoint: remote ? this.endpoint.trim() : null,
      bucket: kind === "s3" ? this.bucket.trim() : null,
      prefix: remote ? this.prefix.trim() || null : null,
      region: kind === "s3" ? this.region.trim() || null : null,
      access_key_id: kind === "s3" ? this.accessKeyId.trim() : null,
      username: kind === "rest" || kind === "sftp" ? this.username.trim() : null,
      host: kind === "sftp" ? this.sftpHost.trim() : null,
      port: kind === "sftp" ? Number(this.sftpPort) : null,
      tls: kind === "rest" ? tls : null,
      certificate: kind === "rest" && tls === "ca" ? this.certificate : null,
      certificate_sha256: kind === "rest" && tls === "fingerprint" ? this.certificateSha256.trim() : null,
      sources: this.sources,
      limit_upload_kib: Number(this.limitUpload),
      limit_download_kib: Number(this.limitDownload),
      keep_daily: Number(this.keepDaily),
      keep_weekly: Number(this.keepWeekly),
      keep_monthly: Number(this.keepMonthly),
      check_percent: Number(this.checkPercent),
      // Sent only when typed: left out, the server keeps the stored ones of the same target.
      password: this.password || undefined,
      s3_secret_access_key: kind === "s3" && this.secretKey ? this.secretKey : undefined,
      rest_password: kind === "rest" && this.restPassword ? this.restPassword : undefined,
      trust_new_host_key: kind === "sftp" && this.pinShown && this.trustNewHostKey ? true : undefined,
      host_key_fingerprint:
        kind === "sftp" && this.fingerprintShown && this.hostKeyFingerprint.trim() ? this.hostKeyFingerprint.trim() : undefined,
    };
  }
}
