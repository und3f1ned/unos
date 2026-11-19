<script lang="ts">
  import { onMount } from "svelte";
  import { toDataURL } from "qrcode";
  import { loadCurrentUser, user } from "$lib/stores/auth";
  import SmbAccessCard from "$lib/components/account/SmbAccessCard.svelte";
  import TwoFactorRequirementCard from "$lib/components/account/TwoFactorRequirementCard.svelte";
  import FirewallCard from "$lib/components/account/FirewallCard.svelte";
  import RecoveryCodes from "$lib/components/account/RecoveryCodes.svelte";
  import SecurityKeys from "$lib/components/account/SecurityKeys.svelte";
  import LanguageCard from "$lib/components/account/LanguageCard.svelte";
  import ThemeCard from "$lib/components/account/ThemeCard.svelte";
  import ApiTokensCard from "$lib/components/account/ApiTokensCard.svelte";
  import { intlLocale, t } from "$lib/i18n";
  import { formatDateTime } from "$lib/time";
  import { Alert, Badge, Button, Modal } from "$lib/components/ui";
  import { changePassword, setupTOTP, enableTOTP, disableTOTP, getTOTPStatus, type TOTPSetupResponse } from "$lib/api/auth";
  import {
    getLoginHistory, clearLoginHistory,
    getCertificateStatus, getCertificates, generateSelfSignedCert, deleteCertificate, serveCertificate, LOCAL_CA_ID,
    type LoginAttempt, type Certificate, type CertificateStatus
  } from "$lib/api/security";
  import {
    Shield, Key, Smartphone, Lock, CheckCircle, AlertCircle, Copy, Eye, EyeOff,
    Globe, History, Plus, Trash2, RefreshCw,
    Clock, User, MapPin, Monitor, FileKey
  } from "lucide-svelte";

  // Password change state
  let currentPassword = $state("");
  let newPassword = $state("");
  let confirmPassword = $state("");
  let passwordError = $state("");
  let passwordSuccess = $state("");
  // E.g. open SMB connections that could not be ended: shown until dismissed.
  let passwordWarnings = $state<string[]>([]);
  let changingPassword = $state(false);
  let showCurrentPassword = $state(false);
  let showNewPassword = $state(false);

  // 2FA state. Until the status has loaded, the card offers neither Enable nor Disable: a
  // status that could not be loaded is reported, not shown as off.
  let totpEnabled = $state(false);
  // Every account must have two-factor authentication: turning it off says what follows.
  let totpRequired = $state(false);
  let totpStatusLoaded = $state(false);
  let totpStatusLoading = $state(false);
  let totpStatusError = $state("");
  let totpLoading = $state(false);
  let totpSetupData = $state<TOTPSetupResponse | null>(null);
  let totpCode = $state("");
  let totpError = $state("");
  let totpSuccess = $state("");
  let showSetupModal = $state(false);
  let showDisableModal = $state(false);
  let disableCode = $state("");
  let secretCopied = $state(false);
  // The otpauth:// URI contains the TOTP secret, so the QR code is rendered in
  // the browser and never sent to a third-party service.
  let qrDataUrl = $state("");
  // Turning SMB on needs the two-factor code exactly while 2FA is on: the
  // SMB card reloads its status when 2FA changes here.
  let smbCard: SmbAccessCard | undefined;

  // Login History state
  let loginHistory = $state<LoginAttempt[]>([]);
  let loginHistoryTotal = $state(0);
  let loginHistoryFailed = $state(0);
  let loginHistorySuccess = $state(0);
  let loginHistoryLoading = $state(false);
  // Why the last load failed: shown in place of the counts and the list.
  let loginHistoryError = $state("");
  let showLoginHistory = $state(false);

  // Certificate state
  let certStatus = $state<CertificateStatus | null>(null);
  let certificates = $state<Certificate[]>([]);
  // Until the stored certificates have loaded, the card does not say that there are none.
  let certificatesLoaded = $state(false);
  let certLoading = $state(false);
  // Why the last load of the certificates failed: reset by every load.
  let certLoadError = $state("");
  // Why a delete failed; why a generation failed, shown in its dialog.
  let certError = $state("");
  let genCertError = $state("");
  let certSuccess = $state("");
  // The certificate whose serving waits for its confirmation: a stored one, or null for Caddy's
  // local CA (ADR-0404); why the server refused it, shown in the dialog.
  let serveTarget = $state<{ cert: Certificate | null } | null>(null);
  let serveBusy = $state(false);
  let serveError = $state("");
  let showCertModal = $state(false);
  let showGenCertModal = $state(false);
  let newCert = $state({
    domain: "",
    validity_days: 365,
    organization: "",
    country: ""
  });

  // The firewall and the certificates are administrator-only APIs; every account can
  // manage its own password, 2FA and see its own login history.
  let isAdmin = $derived($user?.is_admin === true);

  // The labels of the values that the server answers as words (Certificate of $lib/api/security):
  // keys of the catalog, by value.
  const CERTIFICATE_TYPES: Record<string, string> = {
    self_signed: "account.certs.selfSigned",
    custom: "account.certs.custom"
  };
  // What browsers get (served_by of CertificateStatus), as the web server answered the NAS (ADR-0404).
  const SERVED_BY: Record<string, string> = {
    caddy_local_ca: "account.certs.caddyLocalCa",
    stored_certificate: "account.certs.storedCertificate",
    unknown: "account.certs.servedUnknown"
  };
  const SERVED_BADGES: Record<string, "info" | "success" | "warning"> = {
    caddy_local_ca: "info",
    stored_certificate: "success",
    unknown: "warning"
  };

  /** The label of `value` in the language of the page; a value without one as it comes. */
  let labelOf = $derived((labels: Record<string, string>, value: string): string =>
    Object.prototype.hasOwnProperty.call(labels, value) ? $t(labels[value]) : value);

  /** A certificate's date (UTC with its offset, ADR-0401) in the language of the page and the browser's time zone. */
  let formatCertificateDate = $derived((value: string): string => formatDateTime(value, $intlLocale));

  /** The stored certificate of `id` among `listed`. */
  function storedCertificate(listed: Certificate[], id: string | null | undefined): Certificate | undefined {
    return id ? listed.find((cert) => cert.id === id) : undefined;
  }
  // What browsers get, in words: the served certificate's name, or what serves theirs.
  let servedCert = $derived(storedCertificate(certificates, certStatus?.served_id));
  let chosenCert = $derived(storedCertificate(certificates, certStatus?.chosen_id));
  let servedSource = $derived(
    !certStatus
      ? ""
      : certStatus.served_by === "stored_certificate" && servedCert
        ? servedCert.domain
        : labelOf(SERVED_BY, certStatus.served_by)
  );
  // A stored certificate was chosen, and the web server answered with another.
  let chosenNotServed = $derived(
    !!certStatus?.chosen_id &&
    certStatus.served_by !== "unknown" &&
    certStatus.served_id !== certStatus.chosen_id
  );

  onMount(async () => {
    await Promise.all([
      loadTOTPStatus(),
      ...(isAdmin ? [loadCertificateStatus()] : [])
    ]);
  });

  function messageOf(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  async function loadTOTPStatus() {
    totpStatusLoading = true;
    try {
      const status = await getTOTPStatus();
      totpEnabled = status.enabled;
      totpRequired = status.required;
      totpStatusLoaded = true;
      totpStatusError = "";
    } catch (e) {
      totpStatusError = messageOf(e);
    } finally {
      totpStatusLoading = false;
    }
  }

  async function handlePasswordChange() {
    passwordError = "";
    passwordSuccess = "";
    passwordWarnings = [];

    if (newPassword !== confirmPassword) {
      passwordError = $t("account.password.mismatch");
      return;
    }

    if (newPassword.length < 8) {
      passwordError = $t("account.password.tooShort");
      return;
    }

    changingPassword = true;
    try {
      const result = await changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      });
      passwordSuccess = $t("account.password.changed");
      passwordWarnings = result?.warnings ?? [];
      currentPassword = "";
      newPassword = "";
      confirmPassword = "";
    } catch (e: any) {
      passwordError = e.message || $t("account.password.failed");
    } finally {
      changingPassword = false;
    }
  }

  async function startTOTPSetup() {
    totpError = "";
    totpLoading = true;
    try {
      totpSetupData = await setupTOTP();
      try {
        // Twice the displayed size so it stays sharp on high-DPI screens.
        // i18n-ignore: the options of the QR code
        qrDataUrl = await toDataURL(totpSetupData.qr_uri, {
          width: 384,
          margin: 2,
          errorCorrectionLevel: "M"
        });
      } catch (e) {
        // The secret is still shown for manual entry.
        console.error("Failed to render QR code:", e);
        qrDataUrl = "";
      }
      showSetupModal = true;
    } catch (e: any) {
      totpError = e.message || $t("account.totp.setupFailed");
    } finally {
      totpLoading = false;
    }
  }

  async function confirmTOTPEnable() {
    if (totpCode.length !== 6) {
      totpError = $t("account.totp.codeRequired");
      return;
    }

    totpError = "";
    totpLoading = true;
    try {
      await enableTOTP(totpCode);
      totpEnabled = true;
      totpSuccess = $t("account.totp.enabled");
      void smbCard?.refresh();
      showSetupModal = false;
      totpSetupData = null;
      qrDataUrl = "";
      totpCode = "";
    } catch (e: any) {
      totpError = e.message || $t("account.totp.invalidCode");
    } finally {
      totpLoading = false;
    }
  }

  async function confirmTOTPDisable() {
    if (disableCode.length !== 6) {
      totpError = $t("account.totp.codeRequired");
      return;
    }

    totpError = "";
    totpLoading = true;
    try {
      await disableTOTP(disableCode);
      totpEnabled = false;
      totpSuccess = $t("account.totp.disabled");
      void smbCard?.refresh();
      showDisableModal = false;
      disableCode = "";
      // While it is required, an account past its grace period can now only set it up again: the
      // account, loaded again, says so, and the layout leads to the setup page.
      if (totpRequired) void loadCurrentUser().catch(() => {});
    } catch (e: any) {
      totpError = e.message || $t("account.totp.invalidCode");
    } finally {
      totpLoading = false;
    }
  }

  function cancelSetup() {
    showSetupModal = false;
    totpSetupData = null;
    qrDataUrl = "";
    totpCode = "";
    totpError = "";
  }

  function cancelDisable() {
    showDisableModal = false;
    disableCode = "";
    totpError = "";
  }

  async function copySecret() {
    if (totpSetupData?.secret) {
      await navigator.clipboard.writeText(totpSetupData.secret);
      secretCopied = true;
      setTimeout(() => secretCopied = false, 2000);
    }
  }

  // Login History functions
  async function loadLoginHistory() {
    loginHistoryLoading = true;
    loginHistoryError = "";
    try {
      const response = await getLoginHistory(50, 0);
      loginHistory = response.attempts;
      loginHistoryTotal = response.total;
      loginHistoryFailed = response.failed_count;
      loginHistorySuccess = response.success_count;
    } catch (e) {
      // Not an empty history: what the list and the counts would be is unknown.
      loginHistory = [];
      loginHistoryError = messageOf(e);
    } finally {
      loginHistoryLoading = false;
    }
  }

  async function toggleLoginHistory() {
    showLoginHistory = !showLoginHistory;
    if (showLoginHistory && loginHistory.length === 0) {
      await loadLoginHistory();
    }
  }

  // Reactive, so that an open list follows a change of the language. The times are in the
  // language of the page, in the browser's own format when it is of that language.
  let formatTimestamp = $derived((ts: string | null): string => {
    if (!ts) return $t("common.unknown");
    return formatDateTime(ts, $intlLocale);
  });

  // i18n-ignore: the names of browsers, the same in every language
  const BROWSERS = ["Chrome", "Firefox", "Safari", "Edge"];

  // The first of BROWSERS that the user agent names: Chrome's also names Safari.
  let parseUserAgent = $derived((ua: string | null): string => {
    if (!ua) return $t("common.unknown");
    return BROWSERS.find((name) => ua.includes(name)) ?? $t("account.history.browser");
  });

  // Certificate functions
  async function loadCertificateStatus() {
    certLoading = true;
    certLoadError = "";
    try {
      certStatus = await getCertificateStatus();
      const resp = await getCertificates();
      certificates = resp.certificates;
      // The later answer of the two.
      certStatus = resp.status;
      certificatesLoaded = true;
    } catch (e) {
      // No list is known: not the one of an earlier load, and not "none".
      certificates = [];
      certificatesLoaded = false;
      certLoadError = $t("account.certs.loadFailed", { error: messageOf(e) });
    } finally {
      certLoading = false;
    }
  }

  function openGenerateDialog() {
    genCertError = "";
    showGenCertModal = true;
  }

  async function handleGenerateCert() {
    if (!newCert.domain) {
      genCertError = $t("account.certs.domainRequired");
      return;
    }

    certLoading = true;
    genCertError = "";
    certSuccess = "";
    try {
      await generateSelfSignedCert({
        domain: newCert.domain,
        validity_days: newCert.validity_days,
        organization: newCert.organization || undefined,
        country: newCert.country || undefined
      });
      certSuccess = $t("account.certs.generated");
      showGenCertModal = false;
      newCert = { domain: "", validity_days: 365, organization: "", country: "" };
      await loadCertificateStatus();
    } catch (e: any) {
      genCertError = e.message || $t("account.certs.generateFailed");
    } finally {
      certLoading = false;
    }
  }

  function askServe(cert: Certificate | null) {
    serveError = "";
    serveTarget = { cert };
  }

  function closeServe() {
    if (!serveBusy) serveTarget = null;
  }

  async function confirmServe() {
    if (!serveTarget) return;
    const cert = serveTarget.cert;
    serveBusy = true;
    serveError = "";
    certSuccess = "";
    certError = "";
    try {
      const answer = await serveCertificate(cert ? cert.id : LOCAL_CA_ID);
      certStatus = answer.status;
      certSuccess = cert ? $t("account.certs.nowServed", { domain: cert.domain }) : $t("account.certs.nowLocalCa");
      serveBusy = false;
      serveTarget = null;
      await loadCertificateStatus();
    } catch (e) {
      serveError = messageOf(e);
    } finally {
      serveBusy = false;
    }
  }

  async function handleDeleteCert(cert: Certificate) {
    const question = cert.chosen
      ? $t("account.certs.confirmDeleteServed", { domain: cert.domain })
      : $t("account.certs.confirmDelete");
    if (!confirm(question)) return;

    certLoading = true;
    certError = "";
    certSuccess = "";
    try {
      await deleteCertificate(cert.id);
      certSuccess = $t("account.certs.deleted");
      await loadCertificateStatus();
    } catch (e: any) {
      certError = e.message || $t("account.certs.deleteFailed");
    } finally {
      certLoading = false;
    }
  }
</script>

<div class="space-y-6">
  <h2 class="text-xl font-semibold">{$t("account.title")}</h2>

  <LanguageCard />

  <ThemeCard />

  <!-- Two-Factor Authentication -->
  <div class="bg-surface-elevated rounded-lg p-4">
    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
      <Smartphone class="w-5 h-5" />
      {$t("account.totp.title")}
    </h3>

    {#if totpSuccess}
      <div class="mb-4 p-3 bg-status-success/20 border border-status-success text-status-success rounded-lg flex items-center gap-2">
        <CheckCircle class="w-5 h-5" />
        {totpSuccess}
      </div>
    {/if}

    {#if totpError && !showSetupModal && !showDisableModal}
      <div class="mb-4 p-3 bg-status-error/20 border border-status-error text-status-error rounded-lg flex items-center gap-2">
        <AlertCircle class="w-5 h-5" />
        {totpError}
      </div>
    {/if}

    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <p class="font-medium">{$t("account.totp.app")}</p>
        <p class="text-sm text-content-secondary">
          {$t("account.totp.appHint")}
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-3">
        {#if !totpStatusLoaded}
          {#if !totpStatusError}
            <span class="text-sm text-content-muted">{$t("common.loading")}</span>
          {/if}
        {:else if totpEnabled}
          <span class="px-3 py-1 bg-status-success/20 text-status-success rounded-lg text-sm flex items-center gap-1">
            <CheckCircle class="w-4 h-4" />
            {$t("account.totp.on")}
          </span>
          <Button variant="danger"
            onclick={() => { showDisableModal = true; totpError = ""; }}
           
          >
            {$t("account.totp.disable")}
          </Button>
        {:else}
          <span class="px-3 py-1 bg-surface-overlay text-content-secondary rounded-lg text-sm">
            {$t("account.totp.off")}
          </span>
          <Button variant="primary"
            onclick={startTOTPSetup}
            disabled={totpLoading}
           
          >
            {totpLoading ? $t("account.totp.settingUp") : $t("account.totp.enable")}
          </Button>
        {/if}
      </div>
    </div>

    {#if totpStatusError}
      <Alert variant="error" title={$t("account.totp.statusError")} class="mt-4">
        <p>{totpStatusError}</p>
        <Button variant="secondary" size="sm" class="mt-3" loading={totpStatusLoading} onclick={loadTOTPStatus}
          >{$t("common.retry")}</Button
        >
      </Alert>
    {/if}

    <!-- Turning 2FA off removes the codes and the security keys, and a new setup starts without
         any (ADR-0238, ADR-0409). -->
    {#if totpStatusLoaded && totpEnabled}
      <RecoveryCodes />
      <SecurityKeys />
    {:else if totpStatusLoaded}
      <p class="mt-4 pt-4 border-t border-border text-sm text-content-secondary">{$t("securitykeys.needsTotp")}</p>
    {/if}
  </div>

  <!-- Change Password -->
  <div class="bg-surface-elevated rounded-lg p-4">
    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
      <Key class="w-5 h-5" />
      {$t("account.password.title")}
    </h3>

    {#if passwordSuccess}
      <div class="mb-4 p-3 bg-status-success/20 border border-status-success text-status-success rounded-lg flex items-center gap-2">
        <CheckCircle class="w-5 h-5" />
        {passwordSuccess}
      </div>
    {/if}

    {#if passwordWarnings.length > 0}
      <Alert variant="warning" class="mb-4" dismissible ondismiss={() => (passwordWarnings = [])}>
        {#each passwordWarnings as warning}
          <p>{warning}</p>
        {/each}
      </Alert>
    {/if}

    {#if passwordError}
      <div class="mb-4 p-3 bg-status-error/20 border border-status-error text-status-error rounded-lg flex items-center gap-2">
        <AlertCircle class="w-5 h-5" />
        {passwordError}
      </div>
    {/if}

    <form onsubmit={(e) => { e.preventDefault(); handlePasswordChange(); }} class="space-y-4 max-w-md">
      <div>
        <label for="currentPassword" class="block text-sm font-medium text-content-secondary mb-1">
          {$t("account.password.current")}
        </label>
        <div class="relative">
          {#if showCurrentPassword}
            <input
              type="text"
              id="currentPassword"
              bind:value={currentPassword}
              class="form-input pr-10"
              required
            />
          {:else}
            <input
              type="password"
              id="currentPassword"
              bind:value={currentPassword}
              class="form-input pr-10"
              required
            />
          {/if}
          <Button variant="plain" class="absolute right-3 top-1/2 -translate-y-1/2 text-content-secondary hover:text-content-soft"
            type="button"
            onclick={() => showCurrentPassword = !showCurrentPassword}
           
          >
            {#if showCurrentPassword}
              <EyeOff class="w-4 h-4" />
            {:else}
              <Eye class="w-4 h-4" />
            {/if}
          </Button>
        </div>
      </div>

      <div>
        <label for="newPassword" class="block text-sm font-medium text-content-secondary mb-1">
          {$t("account.password.new")}
        </label>
        <div class="relative">
          {#if showNewPassword}
            <input
              type="text"
              id="newPassword"
              bind:value={newPassword}
              minlength="8"
              class="form-input pr-10"
              required
            />
          {:else}
            <input
              type="password"
              id="newPassword"
              bind:value={newPassword}
              minlength="8"
              class="form-input pr-10"
              required
            />
          {/if}
          <Button variant="plain" class="absolute right-3 top-1/2 -translate-y-1/2 text-content-secondary hover:text-content-soft"
            type="button"
            onclick={() => showNewPassword = !showNewPassword}
           
          >
            {#if showNewPassword}
              <EyeOff class="w-4 h-4" />
            {:else}
              <Eye class="w-4 h-4" />
            {/if}
          </Button>
        </div>
        <p class="text-xs text-content-muted mt-1">{$t("account.password.minimum")}</p>
      </div>

      <div>
        <label for="confirmPassword" class="block text-sm font-medium text-content-secondary mb-1">
          {$t("account.password.confirm")}
        </label>
        <input
          type="password"
          id="confirmPassword"
          bind:value={confirmPassword}
          class="form-input"
          required
        />
      </div>

      <Button variant="primary"
        type="submit"
        disabled={changingPassword}
       
      >
        {changingPassword ? $t("account.password.changing") : $t("account.password.submit")}
      </Button>
    </form>
  </div>

  <SmbAccessCard bind:this={smbCard} />

  <ApiTokensCard />

  {#if isAdmin}
  {#if totpStatusLoaded}
    <TwoFactorRequirementCard ownTwoFactor={totpEnabled} />
  {/if}

  <FirewallCard />

  <!-- SSL/TLS Certificates -->
  <div class="bg-surface-elevated rounded-lg p-4">
    <div class="flex flex-wrap items-center justify-between gap-2 mb-4">
      <h3 class="text-lg font-semibold flex items-center gap-2">
        <FileKey class="w-5 h-5" />
        {$t("account.certs.title")}
      </h3>
      <div class="flex flex-wrap items-center gap-2">
        {#if certStatus}
          <Badge variant={SERVED_BADGES[certStatus.served_by] ?? "info"}>{servedSource}</Badge>
        {/if}
        <Button variant="primary"
          onclick={() => showCertModal = !showCertModal}
         
        >
          {$t("account.certs.manage")}
        </Button>
      </div>
    </div>

    {#if certSuccess}
      <Alert variant="success" class="mb-4">{certSuccess}</Alert>
    {/if}

    {#if certError}
      <Alert variant="error" class="mb-4">{certError}</Alert>
    {/if}

    {#if certLoadError}
      <Alert variant="error" class="mb-4">
        <p>{certLoadError}</p>
        <Button variant="secondary" size="sm" class="mt-3" loading={certLoading} onclick={loadCertificateStatus}
          >{$t("common.retry")}</Button
        >
      </Alert>
    {/if}

    <p class="text-sm text-content-secondary">
      {$t("account.certs.description")}
    </p>

    {#if showCertModal}
      <div class="mt-4 space-y-4">
        {#if certStatus}
          <div class="p-3 bg-surface-muted rounded-lg min-w-0 break-words">
            <p class="font-medium">
              {$t("account.certs.servedBy", { source: servedSource })}
            </p>
            <p class="text-sm text-content-secondary">{$t("account.certs.servedHint")}</p>
            {#if certStatus.served_by === "unknown"}
              <p class="text-sm text-status-warning mt-2">{$t("account.certs.servedUnknownHint")}</p>
            {/if}
            {#if chosenNotServed}
              <Alert variant="warning" class="mt-3">
                {$t("account.certs.chosenNotServed", {
                  domain: chosenCert?.domain ?? certStatus.chosen_id ?? "",
                  source: servedSource
                })}
              </Alert>
            {/if}
            {#if certStatus.chosen_id || certStatus.served_by === "stored_certificate"}
              <Button variant="secondary" size="sm" class="mt-3" onclick={() => askServe(null)}>
                {$t("account.certs.useLocalCa")}
              </Button>
            {/if}
          </div>
        {/if}

        <div class="flex flex-wrap justify-between items-center gap-2">
          <h4 class="font-medium">{$t("account.certs.stored")}</h4>
          <Button variant="primary" size="sm"
            onclick={openGenerateDialog}
           
          >
            <Plus class="w-4 h-4" />
            {$t("account.certs.generateSelfSigned")}
          </Button>
        </div>

        {#if certificates.length === 0}
          {#if certificatesLoaded}
            <p class="text-sm text-content-secondary text-center py-4">{$t("account.certs.noneFound")}</p>
          {/if}
        {:else}
          <div class="space-y-2">
            {#each certificates as cert}
              <div class="flex flex-wrap items-center justify-between gap-2 p-3 bg-surface-overlay rounded-lg">
                <div class="flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0">
                  <span class="px-2 py-1 rounded text-xs font-medium {cert.is_valid ? 'bg-status-success/20 text-status-success' : 'bg-status-error/20 text-status-error'}">
                    {cert.is_valid ? $t("account.certs.validBadge") : $t("account.certs.expiredBadge")}
                  </span>
                  {#if cert.served}
                    <Badge variant="success">{$t("account.certs.servedBadge")}</Badge>
                  {:else if cert.chosen}
                    {#if certStatus?.served_by !== "unknown"}
                      <Badge variant="warning">{$t("account.certs.notLoadedBadge")}</Badge>
                    {/if}
                  {:else}
                    <Badge variant="neutral">{$t("account.certs.notServedBadge")}</Badge>
                  {/if}
                  {#if cert.expires_soon}
                    <Badge variant="warning">{$t("account.certs.expiresSoonBadge")}</Badge>
                  {/if}
                  <div class="min-w-0 break-words">
                    <p class="font-medium">{cert.domain}</p>
                    <p class="text-sm text-content-secondary">
                      {labelOf(CERTIFICATE_TYPES, cert.type)} |
                      {$t("account.certs.issuer", { issuer: cert.issuer || $t("account.certs.self") })}
                      {#if cert.valid_until}| {$t("account.certs.until", { date: formatCertificateDate(cert.valid_until) })}{/if}
                    </p>
                  </div>
                </div>
                <div class="flex flex-wrap items-center gap-2">
                  {#if cert.is_valid && !cert.served}
                    <Button variant="secondary" size="sm" onclick={() => askServe(cert)}
                      aria-label={$t("account.certs.serveLabel", { domain: cert.domain })}
                    >
                      {$t("account.certs.serve")}
                    </Button>
                  {/if}
                  {#if cert.id.startsWith('custom_')}
                    <Button variant="ghost" size="icon" class="text-status-error hover:text-status-error"
                      onclick={() => handleDeleteCert(cert)}
                      aria-label={$t("account.certs.delete", { domain: cert.domain })}
                    >
                      <Trash2 class="w-4 h-4" />
                    </Button>
                  {/if}
                </div>
              </div>
            {/each}
          </div>
        {/if}
      </div>
    {/if}
  </div>

  {/if}

  <!-- Login History -->
  <div class="bg-surface-elevated rounded-lg p-4">
    <div class="flex flex-wrap items-center justify-between gap-2 mb-4">
      <h3 class="text-lg font-semibold flex items-center gap-2">
        <History class="w-5 h-5" />
        {$t("account.history.title")}
      </h3>
      <Button variant="primary"
        onclick={toggleLoginHistory}
       
      >
        {showLoginHistory ? $t("account.history.hide") : $t("account.history.show")}
      </Button>
    </div>

    <p class="text-sm text-content-secondary">
      {$t("account.history.description")}
    </p>

    {#if showLoginHistory}
      <div class="mt-4 space-y-4">
        {#if loginHistoryLoading}
          <div class="text-center py-4 text-content-secondary">{$t("common.loading")}</div>
        {:else if loginHistoryError}
          <Alert variant="error" title={$t("account.history.loadError")}>
            <p>{loginHistoryError}</p>
            <Button variant="secondary" size="sm" class="mt-3" onclick={loadLoginHistory}>{$t("common.retry")}</Button>
          </Alert>
        {:else}
          <!-- Stats -->
          <div class="grid grid-cols-3 gap-4">
            <div class="p-3 bg-surface-overlay rounded-lg text-center">
              <p class="text-2xl font-bold">{loginHistoryTotal}</p>
              <p class="text-sm text-content-secondary">{$t("account.history.total")}</p>
            </div>
            <div class="p-3 bg-surface-overlay rounded-lg text-center">
              <p class="text-2xl font-bold text-status-success">{loginHistorySuccess}</p>
              <p class="text-sm text-content-secondary">{$t("account.history.successful")}</p>
            </div>
            <div class="p-3 bg-surface-overlay rounded-lg text-center">
              <p class="text-2xl font-bold text-status-error">{loginHistoryFailed}</p>
              <p class="text-sm text-content-secondary">{$t("account.history.failed")}</p>
            </div>
          </div>

          <!-- History List -->
          {#if loginHistory.length === 0}
            <p class="text-sm text-content-secondary text-center py-4">{$t("account.history.empty")}</p>
          {:else}
            <!-- Newest first; the times are the browser's local time (ADR-0262). -->
            <ul class="space-y-2 max-h-80 overflow-y-auto" aria-label={$t("account.history.list")}>
              {#each loginHistory as attempt (attempt.id)}
                <li class="flex items-center gap-3 p-3 bg-surface-overlay rounded-lg">
                  <Badge variant={attempt.success ? "success" : "error"}>
                    {attempt.success ? $t("account.history.success") : $t("account.history.failure")}
                  </Badge>
                  <!-- On a phone the details wrap, and a long name or IPv6 address breaks (ADR-0302). -->
                  <div class="min-w-0">
                    <div class="flex items-center gap-2">
                      <User class="w-4 h-4 text-content-secondary flex-shrink-0" />
                      <span class="font-medium break-all">{attempt.username}</span>
                    </div>
                    <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-content-secondary">
                      <span class="flex items-center gap-1">
                        <Clock class="w-3 h-3" />
                        <time datetime={attempt.timestamp ?? undefined}>{formatTimestamp(attempt.timestamp)}</time>
                      </span>
                      {#if attempt.ip_address}
                        <span class="flex items-center gap-1 min-w-0 break-all">
                          <MapPin class="w-3 h-3 flex-shrink-0" />
                          {attempt.ip_address}
                        </span>
                      {/if}
                      {#if attempt.user_agent}
                        <span class="flex items-center gap-1">
                          <Monitor class="w-3 h-3" />
                          {parseUserAgent(attempt.user_agent)}
                        </span>
                      {/if}
                    </div>
                    {#if attempt.failure_reason}
                      <p class="text-xs text-status-error mt-1">{attempt.failure_reason}</p>
                    {/if}
                  </div>
                </li>
              {/each}
            </ul>
            <Button variant="secondary" class="w-full"
              onclick={loadLoginHistory}
             
            >
              <RefreshCw class="w-4 h-4" />
              {$t("common.refresh")}
            </Button>
          {/if}
        {/if}
      </div>
    {/if}
  </div>
</div>

<!-- The dialogs below take Modal's overlay (ADR-0302): one taller than the window scrolls with
     it, so that its title and buttons stay reachable; my-auto centers it while it fits. -->

<!-- TOTP Setup Modal -->
{#if showSetupModal && totpSetupData}
  <div class="modal-overlay items-start overflow-y-auto p-4">
    <div class="bg-surface-elevated rounded-lg p-6 max-w-md w-full my-auto">
      <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
        <Lock class="w-5 h-5" />
        {$t("account.totp.setupTitle")}
      </h3>

      <div class="space-y-4">
        <p class="text-sm text-content-secondary">
          {$t("account.totp.scan")}
        </p>

        {#if qrDataUrl}
          <div class="flex justify-center">
            <div class="bg-content-on-brand p-2 rounded-lg">
              <img src={qrDataUrl} alt={$t("account.totp.qrAlt")} class="w-48 h-48" />
            </div>
          </div>
        {/if}

        <div class="text-center">
          <p class="text-xs text-content-muted mb-2">{$t("account.totp.manual")}</p>
          <div class="flex items-center justify-center gap-2">
            <code class="bg-surface-overlay px-3 py-1 rounded font-mono text-sm">
              {totpSetupData.secret}
            </code>
            <Button variant="ghost" size="icon-sm"
              type="button"
              onclick={copySecret}
              title={$t("account.totp.copy")}
            >
              <Copy class="w-4 h-4" />
            </Button>
          </div>
          {#if secretCopied}
            <p class="text-xs text-status-success mt-1">{$t("account.totp.copied")}</p>
          {/if}
        </div>

        <div>
          <label for="totpVerify" class="block text-sm font-medium text-content-secondary mb-1">
            {$t("account.totp.verifyLabel")}
          </label>
          <input
            type="text"
            id="totpVerify"
            bind:value={totpCode}
            class="form-input text-center text-2xl tracking-widest"
            placeholder="000000"
            maxlength="6"
            pattern="[0-9]*"
            inputmode="numeric"
          />
        </div>

        {#if totpError}
          <div class="p-3 bg-status-error/20 border border-status-error text-status-error rounded-lg text-sm">
            {totpError}
          </div>
        {/if}

        <div class="flex gap-3">
          <Button variant="secondary" class="flex-1"
            type="button"
            onclick={cancelSetup}
           
          >
            {$t("common.cancel")}
          </Button>
          <Button variant="primary" class="flex-1"
            type="button"
            onclick={confirmTOTPEnable}
            disabled={totpLoading || totpCode.length !== 6}
           
          >
            {totpLoading ? $t("account.totp.verifying") : $t("account.totp.enable2fa")}
          </Button>
        </div>
      </div>
    </div>
  </div>
{/if}

<!-- Disable 2FA Modal -->
{#if showDisableModal}
  <div class="modal-overlay items-start overflow-y-auto p-4">
    <div class="bg-surface-elevated rounded-lg p-6 max-w-md w-full my-auto">
      <h3 class="text-lg font-semibold mb-4 flex items-center gap-2 text-status-error">
        <AlertCircle class="w-5 h-5" />
        {$t("account.totp.disableTitle")}
      </h3>

      <div class="space-y-4">
        <p class="text-sm text-content-secondary">
          {$t("account.totp.disableHint")}
        </p>
        {#if totpRequired}
          <Alert variant="warning">{$t("twofactor.disableRequired")}</Alert>
        {/if}

        <div>
          <label for="disableCode" class="block text-sm font-medium text-content-secondary mb-1">
            {$t("account.totp.codeLabel")}
          </label>
          <input
            type="text"
            id="disableCode"
            bind:value={disableCode}
            class="form-input text-center text-2xl tracking-widest"
            placeholder="000000"
            maxlength="6"
            pattern="[0-9]*"
            inputmode="numeric"
          />
        </div>

        {#if totpError}
          <div class="p-3 bg-status-error/20 border border-status-error text-status-error rounded-lg text-sm">
            {totpError}
          </div>
        {/if}

        <div class="flex gap-3">
          <Button variant="secondary" class="flex-1"
            type="button"
            onclick={cancelDisable}
           
          >
            {$t("common.cancel")}
          </Button>
          <Button variant="danger" class="flex-1"
            type="button"
            onclick={confirmTOTPDisable}
            disabled={totpLoading || disableCode.length !== 6}
           
          >
            {totpLoading ? $t("account.totp.disabling") : $t("account.totp.disable2fa")}
          </Button>
        </div>
      </div>
    </div>
  </div>
{/if}

<!-- Serve a stored certificate, or Caddy's local CA again: every browser sees a new certificate (ADR-0404). -->
<Modal
  open={serveTarget !== null}
  title={serveTarget?.cert ? $t("account.certs.serveTitle") : $t("account.certs.localCaTitle")}
  size="md"
  closable={!serveBusy}
  onclose={closeServe}
>
  {#if serveTarget}
    <p class="break-words">
      {serveTarget.cert
        ? $t("account.certs.serveText", { domain: serveTarget.cert.domain })
        : $t("account.certs.localCaText")}
    </p>
    <Alert variant="warning" class="mt-3">{$t("account.certs.newCertificateWarning")}</Alert>
    {#if serveTarget.cert?.expires_soon && serveTarget.cert.valid_until}
      <Alert variant="warning" class="mt-3">
        {$t("account.certs.expiresSoonWarning", { date: formatCertificateDate(serveTarget.cert.valid_until) })}
      </Alert>
    {/if}
    {#if serveError}
      <Alert variant="error" class="mt-3 break-words">{serveError}</Alert>
    {/if}
  {/if}
  {#snippet footer()}
    <Button variant="secondary" class="flex-1" disabled={serveBusy} onclick={closeServe}>
      {$t("common.cancel")}
    </Button>
    <Button variant="primary" class="flex-1" loading={serveBusy} onclick={confirmServe}>
      {serveTarget?.cert ? $t("account.certs.serveConfirm") : $t("account.certs.useLocalCa")}
    </Button>
  {/snippet}
</Modal>

<!-- Generate Self-Signed Certificate Modal -->
{#if showGenCertModal}
  <div class="modal-overlay items-start overflow-y-auto p-4">
    <div class="bg-surface-elevated rounded-lg p-6 max-w-md w-full my-auto">
      <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
        <FileKey class="w-5 h-5" />
        {$t("account.certs.generateTitle")}
      </h3>

      <form onsubmit={(e) => { e.preventDefault(); handleGenerateCert(); }} class="space-y-4">
        <!-- In the dialog: behind its overlay a refusal could not be read. -->
        {#if genCertError}
          <Alert variant="error">{genCertError}</Alert>
        {/if}

        <div>
          <label class="block text-sm font-medium text-content-secondary mb-1">{$t("account.certs.domain")}</label>
          <input
            type="text"
            bind:value={newCert.domain}
            class="form-input"
            placeholder={$t("account.certs.domainPlaceholder")}
            required
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-content-secondary mb-1">{$t("account.certs.validity")}</label>
          <input
            type="number"
            bind:value={newCert.validity_days}
            min="1"
            max="3650"
            class="form-input"
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-content-secondary mb-1">{$t("account.certs.organization")}</label>
          <input
            type="text"
            bind:value={newCert.organization}
            maxlength="64"
            class="form-input"
            placeholder={$t("account.certs.organizationPlaceholder")}
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-content-secondary mb-1">{$t("account.certs.country")}</label>
          <input
            type="text"
            bind:value={newCert.country}
            minlength="2"
            maxlength="2"
            pattern="[A-Za-z][A-Za-z]"
            title={$t("account.certs.countryHint")}
            class="form-input"
            placeholder={$t("account.certs.countryPlaceholder")}
          />
        </div>

        <div class="flex gap-3">
          <Button variant="secondary" class="flex-1"
            type="button"
            onclick={() => showGenCertModal = false}
           
          >
            {$t("common.cancel")}
          </Button>
          <Button variant="primary" class="flex-1"
            type="submit"
            disabled={certLoading}
           
          >
            {certLoading ? $t("account.certs.generating") : $t("account.certs.generate")}
          </Button>
        </div>
      </form>
    </div>
  </div>
{/if}
