<script lang="ts">
  import { onMount } from "svelte";
  import { user } from "$lib/stores/auth";
  import { changePassword, setupTOTP, enableTOTP, disableTOTP, getTOTPStatus, type TOTPSetupResponse } from "$lib/api/auth";
  import { Shield, Key, Smartphone, Lock, CheckCircle, AlertCircle, Copy, Eye, EyeOff } from "lucide-svelte";

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

  onMount(async () => {
    await loadTOTPStatus();
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

  // Generate QR code URL using QR server API
  function getQRCodeUrl(uri: string): string {
    return "https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=" + encodeURIComponent(uri);
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

  <!-- Security Overview -->
  <div class="bg-slate-800 rounded-lg p-4">
    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
      <Shield class="w-5 h-5" />
      Security Overview
    </h3>

    <div class="space-y-4">
      <div class="flex items-center justify-between">
        <div>
          <p class="font-medium">Firewall</p>
          <p class="text-sm text-slate-400">Configure network firewall rules</p>
        </div>
        <span class="px-3 py-1 bg-slate-700 text-slate-400 rounded-lg text-sm">
          Coming Soon
        </span>
      </div>

      <div class="flex items-center justify-between">
        <div>
          <p class="font-medium">SSL/TLS Certificates</p>
          <p class="text-sm text-slate-400">Manage HTTPS certificates</p>
        </div>
        <span class="px-3 py-1 bg-slate-700 text-slate-400 rounded-lg text-sm">
          Coming Soon
        </span>
      </div>

      <div class="flex items-center justify-between">
        <div>
          <p class="font-medium">Login History</p>
          <p class="text-sm text-slate-400">View recent login attempts</p>
        </div>
        <span class="px-3 py-1 bg-slate-700 text-slate-400 rounded-lg text-sm">
          Coming Soon
        </span>
      </div>
    </div>
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

        <!-- QR Code -->
        <div class="flex justify-center">
          <div class="bg-white p-2 rounded-lg">
            <img
              src={getQRCodeUrl(totpSetupData.qr_uri)}
              alt="QR Code"
              class="w-48 h-48"
            />
          </div>
        </div>

        <!-- Manual entry -->
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

        <!-- Verification -->
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
