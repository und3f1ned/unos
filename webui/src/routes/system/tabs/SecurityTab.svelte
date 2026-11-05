<script lang="ts">
  import { onMount } from "svelte";
  import { toDataURL } from "qrcode";
  import { user } from "$lib/stores/auth";
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
  let changingPassword = false;
  let showCurrentPassword = false;
  let showNewPassword = false;

  // 2FA state
  let totpEnabled = false;
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

  // Login History state
  let loginHistory: LoginAttempt[] = [];
  let loginHistoryTotal = 0;
  let loginHistoryFailed = 0;
  let loginHistorySuccess = 0;
  let loginHistoryLoading = false;
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

  async function loadTOTPStatus() {
    try {
      const status = await getTOTPStatus();
      totpEnabled = status.enabled;
    } catch (e) {
      console.error("Failed to load TOTP status:", e);
    }
  }

  async function handlePasswordChange() {
    passwordError = "";
    passwordSuccess = "";

    if (newPassword !== confirmPassword) {
      passwordError = "New passwords do not match";
      return;
    }

    if (newPassword.length < 8) {
      passwordError = "Password must be at least 8 characters";
      return;
    }

    changingPassword = true;
    try {
      await changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      });
      passwordSuccess = "Password changed successfully";
      currentPassword = "";
      newPassword = "";
      confirmPassword = "";
    } catch (e: any) {
      passwordError = e.message || "Failed to change password";
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
      totpError = e.message || "Failed to setup 2FA";
    } finally {
      totpLoading = false;
    }
  }

  async function confirmTOTPEnable() {
    if (totpCode.length !== 6) {
      totpError = "Please enter a 6-digit code";
      return;
    }

    totpError = "";
    totpLoading = true;
    try {
      await enableTOTP(totpCode);
      totpEnabled = true;
      totpSuccess = "Two-Factor Authentication enabled successfully";
      showSetupModal = false;
      totpSetupData = null;
      qrDataUrl = "";
      totpCode = "";
    } catch (e: any) {
      totpError = e.message || "Invalid code. Please try again.";
    } finally {
      totpLoading = false;
    }
  }

  async function confirmTOTPDisable() {
    if (disableCode.length !== 6) {
      totpError = "Please enter a 6-digit code";
      return;
    }

    totpError = "";
    totpLoading = true;
    try {
      await disableTOTP(disableCode);
      totpEnabled = false;
      totpSuccess = "Two-Factor Authentication disabled";
      showDisableModal = false;
      disableCode = "";
    } catch (e: any) {
      totpError = e.message || "Invalid code. Please try again.";
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
    try {
      const response = await getLoginHistory(50, 0);
      loginHistory = response.attempts;
      loginHistoryTotal = response.total;
      loginHistoryFailed = response.failed_count;
      loginHistorySuccess = response.success_count;
    } catch (e) {
      console.error("Failed to load login history:", e);
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

  function formatTimestamp(ts: string | null): string {
    if (!ts) return "Unknown";
    try {
      return new Date(ts).toLocaleString();
    } catch {
      return ts;
    }
  }

  function parseUserAgent(ua: string | null): string {
    if (!ua) return "Unknown";
    if (ua.includes("Chrome")) return "Chrome";
    if (ua.includes("Firefox")) return "Firefox";
    if (ua.includes("Safari")) return "Safari";
    if (ua.includes("Edge")) return "Edge";
    return "Browser";
  }

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
        firewallSuccess = "Firewall disabled";
      } else {
        await enableFirewall();
        firewallSuccess = "Firewall enabled";
      }
      await loadFirewallStatus();
    } catch (e: any) {
      firewallError = e.message || "Failed to toggle firewall";
    } finally {
      firewallLoading = false;
    }
  }

  async function handleAddRule() {
    if (!newRule.name || !newRule.port) {
      firewallError = "Name and port are required";
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
      firewallSuccess = "Rule added successfully";
      showAddRuleModal = false;
      newRule = { name: "", action: "allow", direction: "in", protocol: "tcp", port: "", source: "", comment: "" };
      await loadFirewallStatus();
    } catch (e: any) {
      firewallError = e.message || "Failed to add rule";
    } finally {
      firewallLoading = false;
    }
  }

  async function handleDeleteRule(ruleId: string) {
    if (!confirm("Are you sure you want to delete this rule?")) return;

    firewallLoading = true;
    try {
      await deleteFirewallRule(ruleId);
      firewallSuccess = "Rule deleted";
      await loadFirewallStatus();
    } catch (e: any) {
      firewallError = e.message || "Failed to delete rule";
    } finally {
      firewallLoading = false;
    }
  }

  async function handleResetFirewall() {
    if (!confirm("This will reset all firewall rules to defaults. Continue?")) return;

    firewallLoading = true;
    try {
      await resetFirewall();
      firewallSuccess = "Firewall reset to defaults";
      await loadFirewallStatus();
    } catch (e: any) {
      firewallError = e.message || "Failed to reset firewall";
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
      certError = "Domain is required";
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
      certSuccess = "Certificate generated successfully";
      showGenCertModal = false;
      newCert = { domain: "", validity_days: 365, organization: "", country: "" };
      await loadCertificateStatus();
    } catch (e: any) {
      certError = e.message || "Failed to generate certificate";
    } finally {
      certLoading = false;
    }
  }

  async function handleDeleteCert(certId: string) {
    if (!confirm("Are you sure you want to delete this certificate?")) return;

    certLoading = true;
    try {
      await deleteCertificate(certId);
      certSuccess = "Certificate deleted";
      await loadCertificateStatus();
    } catch (e: any) {
      certError = e.message || "Failed to delete certificate";
    } finally {
      certLoading = false;
    }
  }
</script>

<div class="space-y-6">
  <h2 class="text-xl font-semibold">Security Settings</h2>

  <!-- Two-Factor Authentication -->
  <div class="bg-slate-800 rounded-lg p-4">
    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
      <Smartphone class="w-5 h-5" />
      Two-Factor Authentication
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
        <p class="font-medium">Authenticator App</p>
        <p class="text-sm text-slate-400">
          Use an app like Google Authenticator or Authy to generate codes
        </p>
      </div>
      <div class="flex items-center gap-3">
        {#if totpEnabled}
          <span class="px-3 py-1 bg-green-500/20 text-green-400 rounded-lg text-sm flex items-center gap-1">
            <CheckCircle class="w-4 h-4" />
            Enabled
          </span>
          <button
            on:click={() => { showDisableModal = true; totpError = ""; }}
            class="px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm transition-colors"
          >
            Disable
          </button>
        {:else}
          <span class="px-3 py-1 bg-slate-700 text-slate-400 rounded-lg text-sm">
            Disabled
          </span>
          <button
            on:click={startTOTPSetup}
            disabled={totpLoading}
            class="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 rounded-lg text-sm transition-colors"
          >
            {totpLoading ? "Setting up..." : "Enable"}
          </button>
        {/if}
      </div>
    </div>
  </div>

  <!-- Change Password -->
  <div class="bg-slate-800 rounded-lg p-4">
    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
      <Key class="w-5 h-5" />
      Change Password
    </h3>

    {#if passwordSuccess}
      <div class="mb-4 p-3 bg-green-500/20 border border-green-500 text-green-400 rounded-lg flex items-center gap-2">
        <CheckCircle class="w-5 h-5" />
        {passwordSuccess}
      </div>
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
          Current Password
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
              id="currentPasswordHidden"
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
          New Password
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
              id="newPasswordHidden"
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
        <p class="text-xs text-slate-500 mt-1">Minimum 8 characters</p>
      </div>

      <div>
        <label for="confirmPassword" class="block text-sm font-medium text-slate-400 mb-1">
          Confirm New Password
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
        {changingPassword ? "Changing..." : "Change Password"}
      </button>
    </form>
  </div>

  {#if isAdmin}
  <!-- Firewall -->
  <div class="bg-slate-800 rounded-lg p-4">
    <div class="flex items-center justify-between mb-4">
      <h3 class="text-lg font-semibold flex items-center gap-2">
        <Flame class="w-5 h-5" />
        Firewall
      </h3>
      <div class="flex items-center gap-2">
        {#if firewallStatus}
          {#if firewallStatus.enabled}
            <span class="px-3 py-1 bg-green-500/20 text-green-400 rounded-lg text-sm flex items-center gap-1">
              <ShieldCheck class="w-4 h-4" />
              Active
            </span>
          {:else}
            <span class="px-3 py-1 bg-yellow-500/20 text-yellow-400 rounded-lg text-sm flex items-center gap-1">
              <ShieldX class="w-4 h-4" />
              Inactive
            </span>
          {/if}
        {/if}
        <button
          on:click={() => showFirewallModal = !showFirewallModal}
          class="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm transition-colors"
        >
          Configure
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
      Configure network firewall rules to control incoming and outgoing traffic.
    </p>

    {#if showFirewallModal && firewallStatus}
      <div class="mt-4 space-y-4">
        <div class="flex items-center justify-between p-3 bg-slate-700 rounded-lg">
          <div>
            <p class="font-medium">Firewall Status</p>
            <p class="text-sm text-slate-400">
              Default: Incoming {firewallStatus.default_incoming}, Outgoing {firewallStatus.default_outgoing}
            </p>
          </div>
          <div class="flex gap-2">
            <button
              on:click={toggleFirewall}
              disabled={firewallLoading}
              class="px-4 py-2 rounded-lg text-sm transition-colors {firewallStatus.enabled ? 'bg-red-600 hover:bg-red-700' : 'bg-green-600 hover:bg-green-700'}"
            >
              {firewallStatus.enabled ? 'Disable' : 'Enable'}
            </button>
            <button
              on:click={handleResetFirewall}
              disabled={firewallLoading}
              class="px-4 py-2 bg-slate-600 hover:bg-slate-500 rounded-lg text-sm transition-colors flex items-center gap-1"
            >
              <RefreshCw class="w-4 h-4" />
              Reset
            </button>
          </div>
        </div>

        <div class="flex justify-between items-center">
          <h4 class="font-medium">Firewall Rules</h4>
          <button
            on:click={() => showAddRuleModal = true}
            class="px-3 py-1 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm flex items-center gap-1"
          >
            <Plus class="w-4 h-4" />
            Add Rule
          </button>
        </div>

        {#if firewallStatus.rules.length === 0}
          <p class="text-sm text-slate-400 text-center py-4">No custom rules configured</p>
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
                      {rule.direction.toUpperCase()} | {rule.protocol.toUpperCase()} | Port: {rule.port || 'Any'}
                      {#if rule.source}| From: {rule.source}{/if}
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
        SSL/TLS Certificates
      </h3>
      <div class="flex items-center gap-2">
        {#if certStatus}
          {#if certStatus.has_certificate && certStatus.is_valid}
            <span class="px-3 py-1 bg-green-500/20 text-green-400 rounded-lg text-sm flex items-center gap-1">
              <CheckCircle class="w-4 h-4" />
              Valid
            </span>
          {:else if certStatus.has_certificate}
            <span class="px-3 py-1 bg-yellow-500/20 text-yellow-400 rounded-lg text-sm flex items-center gap-1">
              <AlertCircle class="w-4 h-4" />
              Expired
            </span>
          {:else}
            <span class="px-3 py-1 bg-slate-700 text-slate-400 rounded-lg text-sm">
              No Certificate
            </span>
          {/if}
        {/if}
        <button
          on:click={() => showCertModal = !showCertModal}
          class="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm transition-colors"
        >
          Manage
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
      Manage HTTPS certificates for secure connections.
    </p>

    {#if showCertModal}
      <div class="mt-4 space-y-4">
        {#if certStatus?.has_certificate}
          <div class="p-3 bg-slate-700 rounded-lg">
            <div class="flex items-center justify-between">
              <div>
                <p class="font-medium">{certStatus.domain || 'Certificate'}</p>
                <p class="text-sm text-slate-400">
                  Type: {certStatus.type?.replace('_', ' ').toUpperCase() || 'Unknown'}
                  {#if certStatus.valid_until}
                    | Expires: {certStatus.valid_until}
                  {/if}
                </p>
              </div>
              {#if certStatus.auto_renew}
                <span class="px-2 py-1 bg-blue-500/20 text-blue-400 rounded text-xs">Auto-renew</span>
              {/if}
            </div>
          </div>
        {/if}

        <div class="flex justify-between items-center">
          <h4 class="font-medium">All Certificates</h4>
          <button
            on:click={() => showGenCertModal = true}
            class="px-3 py-1 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm flex items-center gap-1"
          >
            <Plus class="w-4 h-4" />
            Generate Self-Signed
          </button>
        </div>

        {#if certificates.length === 0}
          <p class="text-sm text-slate-400 text-center py-4">No certificates found</p>
        {:else}
          <div class="space-y-2">
            {#each certificates as cert}
              <div class="flex items-center justify-between p-3 bg-slate-700 rounded-lg">
                <div class="flex items-center gap-3">
                  <span class="px-2 py-1 rounded text-xs font-medium {cert.is_valid ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}">
                    {cert.is_valid ? 'VALID' : 'EXPIRED'}
                  </span>
                  <div>
                    <p class="font-medium">{cert.domain}</p>
                    <p class="text-sm text-slate-400">
                      {cert.type.replace('_', ' ')} | Issuer: {cert.issuer || 'Self'}
                      {#if cert.valid_until}| Until: {cert.valid_until}{/if}
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
        Login History
      </h3>
      <button
        on:click={toggleLoginHistory}
        class="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm transition-colors"
      >
        {showLoginHistory ? 'Hide' : 'View History'}
      </button>
    </div>

    <p class="text-sm text-slate-400">
      View recent login attempts and monitor account activity.
    </p>

    {#if showLoginHistory}
      <div class="mt-4 space-y-4">
        <!-- Stats -->
        <div class="grid grid-cols-3 gap-4">
          <div class="p-3 bg-slate-700 rounded-lg text-center">
            <p class="text-2xl font-bold">{loginHistoryTotal}</p>
            <p class="text-sm text-slate-400">Total Attempts</p>
          </div>
          <div class="p-3 bg-slate-700 rounded-lg text-center">
            <p class="text-2xl font-bold text-green-400">{loginHistorySuccess}</p>
            <p class="text-sm text-slate-400">Successful</p>
          </div>
          <div class="p-3 bg-slate-700 rounded-lg text-center">
            <p class="text-2xl font-bold text-red-400">{loginHistoryFailed}</p>
            <p class="text-sm text-slate-400">Failed</p>
          </div>
        </div>

        <!-- History List -->
        {#if loginHistoryLoading}
          <div class="text-center py-4 text-slate-400">Loading...</div>
        {:else if loginHistory.length === 0}
          <p class="text-sm text-slate-400 text-center py-4">No login history found</p>
        {:else}
          <div class="space-y-2 max-h-80 overflow-y-auto">
            {#each loginHistory as attempt}
              <div class="flex items-center justify-between p-3 bg-slate-700 rounded-lg">
                <div class="flex items-center gap-3">
                  <span class="px-2 py-1 rounded text-xs font-medium {attempt.success ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}">
                    {attempt.success ? 'SUCCESS' : 'FAILED'}
                  </span>
                  <div>
                    <div class="flex items-center gap-2">
                      <User class="w-4 h-4 text-slate-400" />
                      <span class="font-medium">{attempt.username}</span>
                    </div>
                    <div class="flex items-center gap-4 text-sm text-slate-400">
                      <span class="flex items-center gap-1">
                        <Clock class="w-3 h-3" />
                        {formatTimestamp(attempt.timestamp)}
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
                      <p class="text-xs text-red-400 mt-1">{attempt.failure_reason}</p>
                    {/if}
                  </div>
                </div>
              </div>
            {/each}
          </div>
          <button
            on:click={loadLoginHistory}
            class="w-full py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm transition-colors flex items-center justify-center gap-2"
          >
            <RefreshCw class="w-4 h-4" />
            Refresh
          </button>
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
        Setup Two-Factor Authentication
      </h3>

      <div class="space-y-4">
        <p class="text-sm text-slate-400">
          Scan this QR code with your authenticator app (Google Authenticator, Authy, etc.)
        </p>

        {#if qrDataUrl}
          <div class="flex justify-center">
            <div class="bg-white p-2 rounded-lg">
              <img src={qrDataUrl} alt="QR code for your authenticator app" class="w-48 h-48" />
            </div>
          </div>
        {/if}

        <div class="text-center">
          <p class="text-xs text-slate-500 mb-2">Or enter this code manually:</p>
          <div class="flex items-center justify-center gap-2">
            <code class="bg-slate-700 px-3 py-1 rounded font-mono text-sm">
              {totpSetupData.secret}
            </code>
            <button
              type="button"
              on:click={copySecret}
              class="p-1 hover:bg-slate-700 rounded transition-colors"
              title="Copy to clipboard"
            >
              <Copy class="w-4 h-4" />
            </button>
          </div>
          {#if secretCopied}
            <p class="text-xs text-green-400 mt-1">Copied!</p>
          {/if}
        </div>

        <div>
          <label for="totpVerify" class="block text-sm font-medium text-slate-400 mb-1">
            Enter the 6-digit code from your app
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
            Cancel
          </button>
          <button
            type="button"
            on:click={confirmTOTPEnable}
            disabled={totpLoading || totpCode.length !== 6}
            class="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 rounded-lg transition-colors"
          >
            {totpLoading ? "Verifying..." : "Enable 2FA"}
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
        Disable Two-Factor Authentication
      </h3>

      <div class="space-y-4">
        <p class="text-sm text-slate-400">
          This will remove the extra security layer from your account.
          Enter a code from your authenticator app to confirm.
        </p>

        <div>
          <label for="disableCode" class="block text-sm font-medium text-slate-400 mb-1">
            Authentication Code
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
            Cancel
          </button>
          <button
            type="button"
            on:click={confirmTOTPDisable}
            disabled={totpLoading || disableCode.length !== 6}
            class="flex-1 px-4 py-2 bg-red-600 hover:bg-red-700 disabled:bg-red-800 rounded-lg transition-colors"
          >
            {totpLoading ? "Disabling..." : "Disable 2FA"}
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
        Add Firewall Rule
      </h3>

      <form on:submit|preventDefault={handleAddRule} class="space-y-4">
        <div>
          <label class="block text-sm font-medium text-slate-400 mb-1">Rule Name</label>
          <input
            type="text"
            bind:value={newRule.name}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            placeholder="e.g., Web Server"
            required
          />
        </div>

        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-sm font-medium text-slate-400 mb-1">Action</label>
            <select
              bind:value={newRule.action}
              class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            >
              <option value="allow">Allow</option>
              <option value="deny">Deny</option>
              <option value="reject">Reject</option>
            </select>
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-400 mb-1">Direction</label>
            <select
              bind:value={newRule.direction}
              class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            >
              <option value="in">Incoming</option>
              <option value="out">Outgoing</option>
            </select>
          </div>
        </div>

        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-sm font-medium text-slate-400 mb-1">Protocol</label>
            <select
              bind:value={newRule.protocol}
              class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            >
              <option value="tcp">TCP</option>
              <option value="udp">UDP</option>
              <option value="any">Any</option>
            </select>
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-400 mb-1">Port</label>
            <input
              type="text"
              bind:value={newRule.port}
              class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
              placeholder="e.g., 80 or 80:443"
              required
            />
          </div>
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-400 mb-1">Source IP (optional)</label>
          <input
            type="text"
            bind:value={newRule.source}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            placeholder="e.g., 192.168.1.0/24"
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-400 mb-1">Comment (optional)</label>
          <input
            type="text"
            bind:value={newRule.comment}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            placeholder="Description of this rule"
          />
        </div>

        <div class="flex gap-3">
          <button
            type="button"
            on:click={() => showAddRuleModal = false}
            class="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={firewallLoading}
            class="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 rounded-lg transition-colors"
          >
            {firewallLoading ? "Adding..." : "Add Rule"}
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
        Generate Self-Signed Certificate
      </h3>

      <form on:submit|preventDefault={handleGenerateCert} class="space-y-4">
        <div>
          <label class="block text-sm font-medium text-slate-400 mb-1">Domain Name</label>
          <input
            type="text"
            bind:value={newCert.domain}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            placeholder="e.g., nas.local or 192.168.1.100"
            required
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-400 mb-1">Validity (days)</label>
          <input
            type="number"
            bind:value={newCert.validity_days}
            min="1"
            max="3650"
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-400 mb-1">Organization (optional)</label>
          <input
            type="text"
            bind:value={newCert.organization}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            placeholder="e.g., My Home"
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-slate-400 mb-1">Country Code (optional)</label>
          <input
            type="text"
            bind:value={newCert.country}
            maxlength="2"
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            placeholder="e.g., US"
          />
        </div>

        <div class="flex gap-3">
          <button
            type="button"
            on:click={() => showGenCertModal = false}
            class="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={certLoading}
            class="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 rounded-lg transition-colors"
          >
            {certLoading ? "Generating..." : "Generate"}
          </button>
        </div>
      </form>
    </div>
  </div>
{/if}
