<script lang="ts">
  import { onMount } from "svelte";
  import { toDataURL } from "qrcode";
  import { user } from "$lib/stores/auth";
  import SmbAccessCard from "$lib/components/account/SmbAccessCard.svelte";
  import RecoveryCodes from "$lib/components/account/RecoveryCodes.svelte";
  import LanguageCard from "$lib/components/account/LanguageCard.svelte";
  import { intlLocale, t } from "$lib/i18n";
  import { Alert, Badge, Button } from "$lib/components/ui";
  import { changePassword, setupTOTP, enableTOTP, disableTOTP, getTOTPStatus, type TOTPSetupResponse } from "$lib/api/auth";
  import {
    getLoginHistory, clearLoginHistory,
    getFirewallStatus, enableFirewall, disableFirewall, createFirewallRule, deleteFirewallRule, resetFirewall,
    getCertificateStatus, getCertificates, generateSelfSignedCert, deleteCertificate,
    type LoginAttempt, type FirewallRule, type FirewallStatus, type Certificate, type CertificateStatus
  } from "$lib/api/security";
  import {
    Shield, Key, Smartphone, Lock, CheckCircle, AlertCircle, Copy, Eye, EyeOff,
    Flame, Globe, History, Plus, Trash2, RefreshCw, ShieldCheck, ShieldX,
    Clock, User, MapPin, Monitor, FileKey
  } from "lucide-svelte";

  // Password change state
  let currentPassword = "";
  let newPassword = "";
  let confirmPassword = "";
  let passwordError = "";
  let passwordSuccess = "";
  // E.g. open SMB connections that could not be ended: shown until dismissed.
  let passwordWarnings: string[] = [];
  let changingPassword = false;
  let showCurrentPassword = false;
  let showNewPassword = false;

  // 2FA state. Until the status has loaded, the card offers neither Enable nor Disable: a
  // status that could not be loaded is reported, not shown as off.
  let totpEnabled = false;
  let totpStatusLoaded = false;
  let totpStatusLoading = false;
  let totpStatusError = "";
  let totpLoading = false;
  let totpSetupData: TOTPSetupResponse | null = null;
  let totpCode = "";
  let totpError = "";
  let totpSuccess = "";
  let showSetupModal = false;
  let showDisableModal = false;
  let disableCode = "";
  let secretCopied = false;
  // The otpauth:// URI contains the TOTP secret, so the QR code is rendered in
  // the browser and never sent to a third-party service.
  let qrDataUrl = "";
  // Turning SMB on needs the two-factor code exactly while 2FA is on: the
  // SMB card reloads its status when 2FA changes here.
  let smbCard: SmbAccessCard | undefined;

  // Login History state
  let loginHistory: LoginAttempt[] = [];
  let loginHistoryTotal = 0;
  let loginHistoryFailed = 0;
  let loginHistorySuccess = 0;
  let loginHistoryLoading = false;
  // Why the last load failed: shown in place of the counts and the list.
  let loginHistoryError = "";
  let showLoginHistory = false;

  // Firewall state
  let firewallStatus: FirewallStatus | null = null;
  let firewallLoading = false;
  let firewallError = "";
  let firewallSuccess = "";
  let showFirewallModal = false;
  let showAddRuleModal = false;
  let newRule = {
    name: "",
    action: "allow" as "allow" | "deny" | "reject",
    direction: "in" as "in" | "out",
    protocol: "tcp" as "tcp" | "udp" | "any",
    port: "",
    source: "",
    comment: ""
  };

  // Certificate state
  let certStatus: CertificateStatus | null = null;
  let certificates: Certificate[] = [];
  let certLoading = false;
  let certError = "";
  let certSuccess = "";
  let showCertModal = false;
  let showGenCertModal = false;
  let newCert = {
    domain: "",
    validity_days: 365,
    organization: "",
    country: ""
  };

  // Firewall and certificates are administrator-only APIs; every account can
  // manage its own password, 2FA and see its own login history.
  $: isAdmin = $user?.is_admin === true;

  onMount(async () => {
    await Promise.all([
      loadTOTPStatus(),
      ...(isAdmin ? [loadFirewallStatus(), loadCertificateStatus()] : [])
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
  $: formatTimestamp = (ts: string | null): string => {
    if (!ts) return $t("common.unknown");
    try {
      return new Date(ts).toLocaleString($intlLocale);
    } catch {
      return ts;
    }
  };

  $: parseUserAgent = (ua: string | null): string => {
    if (!ua) return $t("common.unknown");
    if (ua.includes("Chrome")) return "Chrome";
    if (ua.includes("Firefox")) return "Firefox";
    if (ua.includes("Safari")) return "Safari";
    if (ua.includes("Edge")) return "Edge";
    return $t("account.history.browser");
  };

  // Firewall functions
  async function loadFirewallStatus() {
    firewallLoading = true;
    try {
      firewallStatus = await getFirewallStatus();
    } catch (e) {
      console.error("Failed to load firewall status:", e);
    } finally {
      firewallLoading = false;
    }
  }

  async function toggleFirewall() {
    firewallError = "";
    firewallSuccess = "";
    firewallLoading = true;
    try {
      if (firewallStatus?.enabled) {
        await disableFirewall();
        firewallSuccess = $t("account.firewall.disabledNotice");
      } else {
        await enableFirewall();
        firewallSuccess = $t("account.firewall.enabledNotice");
      }
      await loadFirewallStatus();
    } catch (e: any) {
      firewallError = e.message || $t("account.firewall.toggleFailed");
    } finally {
      firewallLoading = false;
    }
  }

  async function handleAddRule() {
    if (!newRule.name || !newRule.port) {
      firewallError = $t("account.firewall.nameAndPort");
      return;
    }

    firewallLoading = true;
    firewallError = "";
    try {
      await createFirewallRule({
        name: newRule.name,
        action: newRule.action,
        direction: newRule.direction,
        protocol: newRule.protocol,
        port: newRule.port || null,
        source: newRule.source || null,
        comment: newRule.comment || null
      });
      firewallSuccess = $t("account.firewall.added");
      showAddRuleModal = false;
      newRule = { name: "", action: "allow", direction: "in", protocol: "tcp", port: "", source: "", comment: "" };
      await loadFirewallStatus();
    } catch (e: any) {
      firewallError = e.message || $t("account.firewall.addFailed");
    } finally {
      firewallLoading = false;
    }
  }

  async function handleDeleteRule(ruleId: string) {
    if (!confirm($t("account.firewall.confirmDelete"))) return;

    firewallLoading = true;
    try {
      await deleteFirewallRule(ruleId);
      firewallSuccess = $t("account.firewall.deleted");
      await loadFirewallStatus();
    } catch (e: any) {
      firewallError = e.message || $t("account.firewall.deleteFailed");
    } finally {
      firewallLoading = false;
    }
  }

  async function handleResetFirewall() {
    if (!confirm($t("account.firewall.confirmReset"))) return;

    firewallLoading = true;
    try {
      await resetFirewall();
      firewallSuccess = $t("account.firewall.resetDone");
      await loadFirewallStatus();
    } catch (e: any) {
      firewallError = e.message || $t("account.firewall.resetFailed");
    } finally {
      firewallLoading = false;
    }
  }

  // Certificate functions
  async function loadCertificateStatus() {
    certLoading = true;
    try {
      certStatus = await getCertificateStatus();
      const resp = await getCertificates();
      certificates = resp.certificates;
    } catch (e) {
      console.error("Failed to load certificate status:", e);
    } finally {
      certLoading = false;
    }
  }

  async function handleGenerateCert() {
    if (!newCert.domain) {
      certError = $t("account.certs.domainRequired");
      return;
    }

    certLoading = true;
    certError = "";
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
      certError = e.message || $t("account.certs.generateFailed");
    } finally {
      certLoading = false;
    }
  }

  async function handleDeleteCert(certId: string) {
    if (!confirm($t("account.certs.confirmDelete"))) return;

    certLoading = true;
    try {
      await deleteCertificate(certId);
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

  <!-- Two-Factor Authentication -->
  <div class="bg-slate-800 rounded-lg p-4">
    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
      <Smartphone class="w-5 h-5" />
      {$t("account.totp.title")}
    </h3>

    {#if totpSuccess}
      <div class="mb-4 p-3 bg-green-500/20 border border-green-500 text-green-400 rounded-lg flex items-center gap-2">
        <CheckCircle class="w-5 h-5" />
        {totpSuccess}
      </div>
    {/if}

    {#if totpError && !showSetupModal && !showDisableModal}
      <div class="mb-4 p-3 bg-red-500/20 border border-red-500 text-red-400 rounded-lg flex items-center gap-2">
        <AlertCircle class="w-5 h-5" />
        {totpError}
      </div>
    {/if}

    <div class="flex items-center justify-between">
      <div>
        <p class="font-medium">{$t("account.totp.app")}</p>
        <p class="text-sm text-slate-400">
          {$t("account.totp.appHint")}
        </p>
      </div>
      <div class="flex items-center gap-3">
        {#if !totpStatusLoaded}
          {#if !totpStatusError}
            <span class="text-sm text-content-muted">{$t("common.loading")}</span>
          {/if}
        {:else if totpEnabled}
          <span class="px-3 py-1 bg-green-500/20 text-green-400 rounded-lg text-sm flex items-center gap-1">
            <CheckCircle class="w-4 h-4" />
            {$t("account.totp.on")}
          </span>
          <button
            on:click={() => { showDisableModal = true; totpError = ""; }}
            class="px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm transition-colors"
          >
            {$t("account.totp.disable")}
          </button>
        {:else}
          <span class="px-3 py-1 bg-slate-700 text-slate-400 rounded-lg text-sm">
            {$t("account.totp.off")}
          </span>
          <button
            on:click={startTOTPSetup}
            disabled={totpLoading}
            class="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 rounded-lg text-sm transition-colors"
          >
            {totpLoading ? $t("account.totp.settingUp") : $t("account.totp.enable")}
          </button>
        {/if}
      </div>
    </div>

    {#if totpStatusError}
      <Alert variant="error" title={$t("account.totp.statusError")} class="mt-4">
        <p>{totpStatusError}</p>
        <Button variant="secondary" size="sm" class="mt-3" loading={totpStatusLoading} on:click={loadTOTPStatus}
          >{$t("common.retry")}</Button
        >
      </Alert>
    {/if}

    <!-- Turning 2FA off removes the codes, and a new setup starts without any. -->
    {#if totpStatusLoaded && totpEnabled}
      <RecoveryCodes />
    {/if}
  </div>

  <!-- Change Password -->
  <div class="bg-slate-800 rounded-lg p-4">
    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
      <Key class="w-5 h-5" />
      {$t("account.password.title")}
    </h3>

    {#if passwordSuccess}
      <div class="mb-4 p-3 bg-green-500/20 border border-green-500 text-green-400 rounded-lg flex items-center gap-2">
        <CheckCircle class="w-5 h-5" />
        {passwordSuccess}
      </div>
    {/if}

    {#if passwordWarnings.length > 0}
      <Alert variant="warning" class="mb-4" dismissible on:dismiss={() => (passwordWarnings = [])}>
        {#each passwordWarnings as warning}
          <p>{warning}</p>
        {/each}
      </Alert>
    {/if}

    {#if passwordError}
      <div class="mb-4 p-3 bg-red-500/20 border border-red-500 text-red-400 rounded-lg flex items-center gap-2">
        <AlertCircle class="w-5 h-5" />
        {passwordError}
      </div>
    {/if}

    <form on:submit|preventDefault={handlePasswordChange} class="space-y-4 max-w-md">
      <div>
        <label for="currentPassword" class="block text-sm font-medium text-slate-400 mb-1">
          {$t("account.password.current")}
        </label>
        <div class="relative">
          {#if showCurrentPassword}
            <input
              type="text"
              id="currentPassword"
              bind:value={currentPassword}
              class="w-full px-4 py-2 pr-10 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
              required
            />
          {:else}
            <input
              type="password"
              id="currentPassword"
              bind:value={currentPassword}
              class="w-full px-4 py-2 pr-10 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
              required
            />
          {/if}
          <button
            type="button"
            on:click={() => showCurrentPassword = !showCurrentPassword}
            class="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-300"
          >
            {#if showCurrentPassword}
              <EyeOff class="w-4 h-4" />
            {:else}
              <Eye class="w-4 h-4" />
            {/if}
          </button>
        </div>
      </div>

      <div>
        <label for="newPassword" class="block text-sm font-medium text-slate-400 mb-1">
          {$t("account.password.new")}
        </label>
        <div class="relative">
          {#if showNewPassword}
            <input
              type="text"
              id="newPassword"
              bind:value={newPassword}
              minlength="8"
              class="w-full px-4 py-2 pr-10 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
              required
            />
          {:else}
            <input
              type="password"
              id="newPassword"
              bind:value={newPassword}
              minlength="8"
              class="w-full px-4 py-2 pr-10 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
              required
            />
          {/if}
          <button
            type="button"
            on:click={() => showNewPassword = !showNewPassword}
            class="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-300"
          >
            {#if showNewPassword}
              <EyeOff class="w-4 h-4" />
            {:else}
              <Eye class="w-4 h-4" />
            {/if}
          </button>
        </div>
        <p class="text-xs text-slate-500 mt-1">{$t("account.password.minimum")}</p>
      </div>

      <div>
        <label for="confirmPassword" class="block text-sm font-medium text-slate-400 mb-1">
          {$t("account.password.confirm")}
        </label>
        <input
          type="password"
          id="confirmPassword"
          bind:value={confirmPassword}
          class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
          required
        />
      </div>

      <button
        type="submit"
        disabled={changingPassword}
        class="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 rounded-lg transition-colors"
      >
        {changingPassword ? $t("account.password.changing") : $t("account.password.submit")}
      </button>
    </form>
  </div>

  <SmbAccessCard bind:this={smbCard} />

  {#if isAdmin}
  <!-- Firewall -->
  <div class="bg-slate-800 rounded-lg p-4">
    <div class="flex items-center justify-between mb-4">
      <h3 class="text-lg font-semibold flex items-center gap-2">
        <Flame class="w-5 h-5" />
        {$t("account.firewall.title")}
      </h3>
      <div class="flex items-center gap-2">
        {#if firewallStatus}
          {#if firewallStatus.enabled}
            <span class="px-3 py-1 bg-green-500/20 text-green-400 rounded-lg text-sm flex items-center gap-1">
              <ShieldCheck class="w-4 h-4" />
              {$t("account.firewall.active")}
            </span>
          {:else}
            <span class="px-3 py-1 bg-yellow-500/20 text-yellow-400 rounded-lg text-sm flex items-center gap-1">
              <ShieldX class="w-4 h-4" />
              {$t("account.firewall.inactive")}
            </span>
          {/if}
        {/if}
        <button
          on:click={() => showFirewallModal = !showFirewallModal}
          class="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm transition-colors"
        >
          {$t("account.firewall.configure")}
        </button>
      </div>
    </div>

    {#if firewallSuccess}
      <div class="mb-4 p-3 bg-green-500/20 border border-green-500 text-green-400 rounded-lg flex items-center gap-2">
        <CheckCircle class="w-5 h-5" />
        {firewallSuccess}
      </div>
    {/if}

    {#if firewallError}
      <div class="mb-4 p-3 bg-red-500/20 border border-red-500 text-red-400 rounded-lg flex items-center gap-2">
        <AlertCircle class="w-5 h-5" />
        {firewallError}
      </div>
    {/if}

    <p class="text-sm text-slate-400">
      {$t("account.firewall.description")}
    </p>

    {#if showFirewallModal && firewallStatus}
      <div class="mt-4 space-y-4">
        <div class="flex items-center justify-between p-3 bg-slate-700 rounded-lg">
          <div>
            <p class="font-medium">{$t("account.firewall.status")}</p>
            <p class="text-sm text-slate-400">
              {$t("account.firewall.defaults", {
                incoming: firewallStatus.default_incoming,
                outgoing: firewallStatus.default_outgoing
              })}
            </p>
          </div>
          <div class="flex gap-2">
            <button
              on:click={toggleFirewall}
              disabled={firewallLoading}
              class="px-4 py-2 rounded-lg text-sm transition-colors {firewallStatus.enabled ? 'bg-red-600 hover:bg-red-700' : 'bg-green-600 hover:bg-green-700'}"
            >
              {firewallStatus.enabled ? $t("account.firewall.disable") : $t("account.firewall.enable")}
            </button>
            <button
              on:click={handleResetFirewall}
              disabled={firewallLoading}
              class="px-4 py-2 bg-slate-600 hover:bg-slate-500 rounded-lg text-sm transition-colors flex items-center gap-1"
            >
              <RefreshCw class="w-4 h-4" />
              {$t("account.firewall.reset")}
            </button>
          </div>
        </div>

        <div class="flex justify-between items-center">
          <h4 class="font-medium">{$t("account.firewall.rules")}</h4>
          <button
            on:click={() => showAddRuleModal = true}
            class="px-3 py-1 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm flex items-center gap-1"
          >
            <Plus class="w-4 h-4" />
            {$t("account.firewall.addRule")}
          </button>
        </div>

        {#if firewallStatus.rules.length === 0}
          <p class="text-sm text-slate-400 text-center py-4">{$t("account.firewall.noRules")}</p>
        {:else}
          <div class="space-y-2">
            {#each firewallStatus.rules as rule}
              <div class="flex items-center justify-between p-3 bg-slate-700 rounded-lg">
                <div class="flex items-center gap-3">
                  <span class="px-2 py-1 rounded text-xs font-medium {rule.action === 'allow' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}">
                    {rule.action.toUpperCase()}
                  </span>
                  <div>
                    <p class="font-medium">{rule.name}</p>
                    <p class="text-sm text-slate-400">
                      {rule.direction.toUpperCase()} | {rule.protocol.toUpperCase()} |
                      {$t("account.firewall.port", { port: rule.port || $t("account.firewall.any") })}
                      {#if rule.source}| {$t("account.firewall.from", { source: rule.source })}{/if}
                    </p>
                  </div>
                </div>
                <button
                  on:click={() => handleDeleteRule(rule.id || '')}
                  class="p-2 text-red-400 hover:bg-red-500/20 rounded-lg transition-colors"
                >
                  <Trash2 class="w-4 h-4" />
                </button>
              </div>
            {/each}
          </div>
        {/if}
      </div>
    {/if}
  </div>

  <!-- SSL/TLS Certificates -->
  <div class="bg-slate-800 rounded-lg p-4">
    <div class="flex items-center justify-between mb-4">
      <h3 class="text-lg font-semibold flex items-center gap-2">
        <FileKey class="w-5 h-5" />
        {$t("account.certs.title")}
      </h3>
      <div class="flex items-center gap-2">
        {#if certStatus}
          {#if certStatus.has_certificate && certStatus.is_valid}
            <span class="px-3 py-1 bg-green-500/20 text-green-400 rounded-lg text-sm flex items-center gap-1">
              <CheckCircle class="w-4 h-4" />
              {$t("account.certs.valid")}
            </span>
          {:else if certStatus.has_certificate}
            <span class="px-3 py-1 bg-yellow-500/20 text-yellow-400 rounded-lg text-sm flex items-center gap-1">
              <AlertCircle class="w-4 h-4" />
              {$t("account.certs.expired")}
            </span>
          {:else}
            <span class="px-3 py-1 bg-slate-700 text-slate-400 rounded-lg text-sm">
              {$t("account.certs.none")}
            </span>
          {/if}
        {/if}
        <button
          on:click={() => showCertModal = !showCertModal}
          class="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm transition-colors"
        >
          {$t("account.certs.manage")}
        </button>
      </div>
    </div>

    {#if certSuccess}
      <div class="mb-4 p-3 bg-green-500/20 border border-green-500 text-green-400 rounded-lg flex items-center gap-2">
        <CheckCircle class="w-5 h-5" />
        {certSuccess}
      </div>
    {/if}

    {#if certError}
      <div class="mb-4 p-3 bg-red-500/20 border border-red-500 text-red-400 rounded-lg flex items-center gap-2">
        <AlertCircle class="w-5 h-5" />
        {certError}
      </div>
    {/if}

    <p class="text-sm text-slate-400">
      {$t("account.certs.description")}
    </p>

    {#if showCertModal}
      <div class="mt-4 space-y-4">
        {#if certStatus?.has_certificate}
          <div class="p-3 bg-slate-700 rounded-lg">
            <div class="flex items-center justify-between">
              <div>
                <p class="font-medium">{certStatus.domain || $t("account.certs.certificate")}</p>
                <p class="text-sm text-slate-400">
                  {$t("account.certs.type", {
                    type: certStatus.type?.replace('_', ' ').toUpperCase() || $t("common.unknown")
                  })}
                  {#if certStatus.valid_until}
                    | {$t("account.certs.expires", { date: certStatus.valid_until })}
                  {/if}
                </p>
              </div>
              {#if certStatus.auto_renew}
                <span class="px-2 py-1 bg-blue-500/20 text-blue-400 rounded text-xs">{$t("account.certs.autoRenew")}</span>
              {/if}
            </div>
          </div>
        {/if}

        <div class="flex justify-between items-center">
          <h4 class="font-medium">{$t("account.certs.all")}</h4>
          <button
            on:click={() => showGenCertModal = true}
            class="px-3 py-1 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm flex items-center gap-1"
          >
            <Plus class="w-4 h-4" />
            {$t("account.certs.generateSelfSigned")}
          </button>
        </div>

        {#if certificates.length === 0}
          <p class="text-sm text-slate-400 text-center py-4">{$t("account.certs.noneFound")}</p>
        {:else}
          <div class="space-y-2">
            {#each certificates as cert}
              <div class="flex items-center justify-between p-3 bg-slate-700 rounded-lg">
                <div class="flex items-center gap-3">
                  <span class="px-2 py-1 rounded text-xs font-medium {cert.is_valid ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}">
                    {cert.is_valid ? $t("account.certs.validBadge") : $t("account.certs.expiredBadge")}
                  </span>
                  <div>
                    <p class="font-medium">{cert.domain}</p>
                    <p class="text-sm text-slate-400">
                      {cert.type.replace('_', ' ')} |
                      {$t("account.certs.issuer", { issuer: cert.issuer || $t("account.certs.self") })}
                      {#if cert.valid_until}| {$t("account.certs.until", { date: cert.valid_until })}{/if}
                    </p>
                  </div>
                </div>
                {#if cert.id.startsWith('custom_')}
                  <button
                    on:click={() => handleDeleteCert(cert.id)}
                    class="p-2 text-red-400 hover:bg-red-500/20 rounded-lg transition-colors"
                  >
                    <Trash2 class="w-4 h-4" />
                  </button>
                {/if}
              </div>
            {/each}
          </div>
        {/if}
      </div>
    {/if}
  </div>

  {/if}

  <!-- Login History -->
  <div class="bg-slate-800 rounded-lg p-4">
    <div class="flex items-center justify-between mb-4">
      <h3 class="text-lg font-semibold flex items-center gap-2">
        <History class="w-5 h-5" />
        {$t("account.history.title")}
      </h3>
      <button
        on:click={toggleLoginHistory}
        class="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm transition-colors"
      >
        {showLoginHistory ? $t("account.history.hide") : $t("account.history.show")}
      </button>
    </div>

    <p class="text-sm text-slate-400">
      {$t("account.history.description")}
    </p>

    {#if showLoginHistory}
      <div class="mt-4 space-y-4">
        {#if loginHistoryLoading}
          <div class="text-center py-4 text-slate-400">{$t("common.loading")}</div>
        {:else if loginHistoryError}
          <Alert variant="error" title={$t("account.history.loadError")}>
            <p>{loginHistoryError}</p>
            <Button variant="secondary" size="sm" class="mt-3" on:click={loadLoginHistory}>{$t("common.retry")}</Button>
          </Alert>
        {:else}
          <!-- Stats -->
          <div class="grid grid-cols-3 gap-4">
            <div class="p-3 bg-slate-700 rounded-lg text-center">
              <p class="text-2xl font-bold">{loginHistoryTotal}</p>
              <p class="text-sm text-slate-400">{$t("account.history.total")}</p>
            </div>
            <div class="p-3 bg-slate-700 rounded-lg text-center">
              <p class="text-2xl font-bold text-green-400">{loginHistorySuccess}</p>
              <p class="text-sm text-slate-400">{$t("account.history.successful")}</p>
            </div>
            <div class="p-3 bg-slate-700 rounded-lg text-center">
              <p class="text-2xl font-bold text-red-400">{loginHistoryFailed}</p>
              <p class="text-sm text-slate-400">{$t("account.history.failed")}</p>
            </div>
          </div>

          <!-- History List -->
          {#if loginHistory.length === 0}
            <p class="text-sm text-slate-400 text-center py-4">{$t("account.history.empty")}</p>
          {:else}
            <!-- Newest first; the times are the browser's local time (ADR-0262). -->
            <ul class="space-y-2 max-h-80 overflow-y-auto" aria-label={$t("account.history.list")}>
              {#each loginHistory as attempt (attempt.id)}
                <li class="flex items-center gap-3 p-3 bg-surface-overlay rounded-lg">
                  <Badge variant={attempt.success ? "success" : "error"}>
                    {attempt.success ? $t("account.history.success") : $t("account.history.failure")}
                  </Badge>
                  <div>
                    <div class="flex items-center gap-2">
                      <User class="w-4 h-4 text-content-secondary" />
                      <span class="font-medium">{attempt.username}</span>
                    </div>
                    <div class="flex items-center gap-4 text-sm text-content-secondary">
                      <span class="flex items-center gap-1">
                        <Clock class="w-3 h-3" />
                        <time datetime={attempt.timestamp ?? undefined}>{formatTimestamp(attempt.timestamp)}</time>
                      </span>
                      {#if attempt.ip_address}
                        <span class="flex items-center gap-1">
                          <MapPin class="w-3 h-3" />
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
            <button
              on:click={loadLoginHistory}
              class="w-full py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm transition-colors flex items-center justify-center gap-2"
            >
              <RefreshCw class="w-4 h-4" />
              {$t("common.refresh")}
            </button>
          {/if}
        {/if}
      </div>
    {/if}
  </div>
</div>

<!-- TOTP Setup Modal -->
{#if showSetupModal && totpSetupData}
  <div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
    <div class="bg-slate-800 rounded-lg p-6 max-w-md w-full">
      <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
        <Lock class="w-5 h-5" />
        {$t("account.totp.setupTitle")}
      </h3>

      <div class="space-y-4">
        <p class="text-sm text-slate-400">
          {$t("account.totp.scan")}
        </p>

        {#if qrDataUrl}
          <div class="flex justify-center">
            <div class="bg-white p-2 rounded-lg">
              <img src={qrDataUrl} alt={$t("account.totp.qrAlt")} class="w-48 h-48" />
            </div>
          </div>
        {/if}

        <div class="text-center">
          <p class="text-xs text-slate-500 mb-2">{$t("account.totp.manual")}</p>
          <div class="flex items-center justify-center gap-2">
            <code class="bg-slate-700 px-3 py-1 rounded font-mono text-sm">
              {totpSetupData.secret}
            </code>
            <button
              type="button"
              on:click={copySecret}
              class="p-1 hover:bg-slate-700 rounded transition-colors"
              title={$t("account.totp.copy")}
            >
              <Copy class="w-4 h-4" />
            </button>
          </div>
          {#if secretCopied}
            <p class="text-xs text-green-400 mt-1">{$t("account.totp.copied")}</p>
          {/if}
        </div>

        <div>
          <label for="totpVerify" class="block text-sm font-medium text-slate-400 mb-1">
            {$t("account.totp.verifyLabel")}
          </label>
          <input
            type="text"
            id="totpVerify"
            bind:value={totpCode}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500 text-center text-2xl tracking-widest"
            placeholder="000000"
            maxlength="6"
            pattern="[0-9]*"
            inputmode="numeric"
          />
        </div>

        {#if totpError}
          <div class="p-3 bg-red-500/20 border border-red-500 text-red-400 rounded-lg text-sm">
            {totpError}
          </div>
        {/if}

        <div class="flex gap-3">
          <button
            type="button"
            on:click={cancelSetup}
            class="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
          >
            {$t("common.cancel")}
          </button>
          <button
            type="button"
            on:click={confirmTOTPEnable}
            disabled={totpLoading || totpCode.length !== 6}
            class="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 rounded-lg transition-colors"
          >
            {totpLoading ? $t("account.totp.verifying") : $t("account.totp.enable2fa")}
          </button>
        </div>
      </div>
    </div>
  </div>
{/if}

<!-- Disable 2FA Modal -->
{#if showDisableModal}
  <div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
    <div class="bg-slate-800 rounded-lg p-6 max-w-md w-full">
      <h3 class="text-lg font-semibold mb-4 flex items-center gap-2 text-red-400">
        <AlertCircle class="w-5 h-5" />
        {$t("account.totp.disableTitle")}
      </h3>

      <div class="space-y-4">
        <p class="text-sm text-slate-400">
          {$t("account.totp.disableHint")}
        </p>

        <div>
          <label for="disableCode" class="block text-sm font-medium text-slate-400 mb-1">
            {$t("account.totp.codeLabel")}
          </label>
          <input
            type="text"
            id="disableCode"
            bind:value={disableCode}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500 text-center text-2xl tracking-widest"
            placeholder="000000"
            maxlength="6"
            pattern="[0-9]*"
            inputmode="numeric"
          />
        </div>

        {#if totpError}
          <div class="p-3 bg-red-500/20 border border-red-500 text-red-400 rounded-lg text-sm">
            {totpError}
          </div>
        {/if}

        <div class="flex gap-3">
          <button
            type="button"
            on:click={cancelDisable}
            class="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
          >
            {$t("common.cancel")}
          </button>
          <button
            type="button"
            on:click={confirmTOTPDisable}
            disabled={totpLoading || disableCode.length !== 6}
            class="flex-1 px-4 py-2 bg-red-600 hover:bg-red-700 disabled:bg-red-800 rounded-lg transition-colors"
          >
            {totpLoading ? $t("account.totp.disabling") : $t("account.totp.disable2fa")}
          </button>
        </div>
      </div>
    </div>
  </div>
{/if}

<!-- Add Firewall Rule Modal -->
{#if showAddRuleModal}
  <div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
    <div class="bg-slate-800 rounded-lg p-6 max-w-md w-full">
      <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
        <Plus class="w-5 h-5" />
        {$t("account.firewall.addTitle")}
      </h3>

      <form on:submit|preventDefault={handleAddRule} class="space-y-4">
        <div>
          <label class="block text-sm font-medium text-slate-400 mb-1">{$t("account.firewall.name")}</label>
          <input
            type="text"
            bind:value={newRule.name}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            placeholder={$t("account.firewall.namePlaceholder")}
            required
          />
        </div>

        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-sm font-medium text-slate-400 mb-1">{$t("account.firewall.action")}</label>
            <select
              bind:value={newRule.action}
              class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            >
              <option value="allow">{$t("account.firewall.allow")}</option>
              <option value="deny">{$t("account.firewall.deny")}</option>
              <option value="reject">{$t("account.firewall.reject")}</option>
            </select>
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-400 mb-1">{$t("account.firewall.direction")}</label>
            <select
              bind:value={newRule.direction}
              class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            >
              <option value="in">{$t("account.firewall.incoming")}</option>
              <option value="out">{$t("account.firewall.outgoing")}</option>
            </select>
          </div>
        </div>

        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-sm font-medium text-slate-400 mb-1">{$t("account.firewall.protocol")}</label>
            <select
              bind:value={newRule.protocol}
              class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            >
              <option value="tcp">{$t("account.firewall.tcp")}</option>
              <option value="udp">{$t("account.firewall.udp")}</option>
              <option value="any">{$t("account.firewall.any")}</option>
            </select>
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-400 mb-1">{$t("account.firewall.portLabel")}</label>
            <input
              type="text"
              bind:value={newRule.port}
              class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
              placeholder={$t("account.firewall.portPlaceholder")}
              required
            />
          </div>
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-400 mb-1">{$t("account.firewall.source")}</label>
          <input
            type="text"
            bind:value={newRule.source}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            placeholder={$t("account.firewall.sourcePlaceholder")}
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-400 mb-1">{$t("account.firewall.comment")}</label>
          <input
            type="text"
            bind:value={newRule.comment}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            placeholder={$t("account.firewall.commentPlaceholder")}
          />
        </div>

        <div class="flex gap-3">
          <button
            type="button"
            on:click={() => showAddRuleModal = false}
            class="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
          >
            {$t("common.cancel")}
          </button>
          <button
            type="submit"
            disabled={firewallLoading}
            class="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 rounded-lg transition-colors"
          >
            {firewallLoading ? $t("account.firewall.adding") : $t("account.firewall.addRule")}
          </button>
        </div>
      </form>
    </div>
  </div>
{/if}

<!-- Generate Self-Signed Certificate Modal -->
{#if showGenCertModal}
  <div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
    <div class="bg-slate-800 rounded-lg p-6 max-w-md w-full">
      <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
        <FileKey class="w-5 h-5" />
        {$t("account.certs.generateTitle")}
      </h3>

      <form on:submit|preventDefault={handleGenerateCert} class="space-y-4">
        <div>
          <label class="block text-sm font-medium text-slate-400 mb-1">{$t("account.certs.domain")}</label>
          <input
            type="text"
            bind:value={newCert.domain}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            placeholder={$t("account.certs.domainPlaceholder")}
            required
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-400 mb-1">{$t("account.certs.validity")}</label>
          <input
            type="number"
            bind:value={newCert.validity_days}
            min="1"
            max="3650"
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-400 mb-1">{$t("account.certs.organization")}</label>
          <input
            type="text"
            bind:value={newCert.organization}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            placeholder={$t("account.certs.organizationPlaceholder")}
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-400 mb-1">{$t("account.certs.country")}</label>
          <input
            type="text"
            bind:value={newCert.country}
            maxlength="2"
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            placeholder={$t("account.certs.countryPlaceholder")}
          />
        </div>

        <div class="flex gap-3">
          <button
            type="button"
            on:click={() => showGenCertModal = false}
            class="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
          >
            {$t("common.cancel")}
          </button>
          <button
            type="submit"
            disabled={certLoading}
            class="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 rounded-lg transition-colors"
          >
            {certLoading ? $t("account.certs.generating") : $t("account.certs.generate")}
          </button>
        </div>
      </form>
    </div>
  </div>
{/if}
