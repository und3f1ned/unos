<script lang="ts">
  import { onMount } from "svelte";
  import { goto } from "$app/navigation";
  import { login, getAuthStatus, getMe } from "$lib/api/auth";
  import { ApiError } from "$lib/api/client";
  import { user, isLoggedIn, isSetupOnly, TWO_FACTOR_SETUP_PATH } from "$lib/stores/auth";
  import { connectWebSocket } from "$lib/stores/stats";
  import { Button, Input } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import { KeyRound, Server, ShieldCheck } from "lucide-svelte";

  let username = "";
  let password = "";
  let totpCode = "";
  let error = "";
  let loading = false;
  let requiresTOTP = false;
  // At the code step: a recovery code in place of the authenticator app's code.
  let useRecoveryCode = false;
  let recoveryCode = "";

  onMount(async () => {
    try {
      const status = await getAuthStatus();
      if (status.setup_required) {
        goto("/setup");
      }
    } catch {
      // API not available
    }
  });

  async function handleSubmit() {
    error = "";
    loading = true;

    try {
      const result = await login(
        username,
        password,
        requiresTOTP && !useRecoveryCode ? totpCode : undefined,
        requiresTOTP && useRecoveryCode ? recoveryCode : undefined
      );

      if (result.requires_totp && !result.access_token) {
        // 2FA is required, show TOTP input
        requiresTOTP = true;
        loading = false;
        return;
      }

      // Login successful - fetch user data and update stores
      const me = await getMe();
      user.set(me);
      isLoggedIn.set(true);
      // No socket serves an account that can only set up two-factor authentication.
      if (!isSetupOnly(me)) connectWebSocket();
      // While every account must have two-factor authentication, one without it is asked to set
      // it up at each sign-in, and after its grace period can only do that.
      goto(me.two_factor_required ? TWO_FACTOR_SETUP_PATH : "/");
    } catch (e: any) {
      error = e.message || $t("login.failed");
      // Only a rejected password leads back to the password form. An invalid
      // or already used code ("Invalid 2FA code", "This two-factor code was
      // already used; wait for the next one", "Invalid recovery code"), too
      // many attempts (429) or a server error keep the 2FA step.
      if (e instanceof ApiError && e.status === 401 && !/2FA|two-factor|recovery code/i.test(error)) {
        requiresTOTP = false;
      }
    } finally {
      loading = false;
    }
  }

  function resetForm() {
    requiresTOTP = false;
    totpCode = "";
    useRecoveryCode = false;
    recoveryCode = "";
    error = "";
  }

  function switchCode() {
    useRecoveryCode = !useRecoveryCode;
    totpCode = "";
    recoveryCode = "";
    error = "";
  }
</script>

<div class="min-h-screen flex items-center justify-center bg-slate-900">
  <div class="bg-slate-800 p-8 rounded-lg shadow-xl w-full max-w-md">
    <div class="flex items-center justify-center gap-2 mb-8">
      <Server class="w-10 h-10 text-blue-500" />
      <span class="text-2xl font-bold">{$t("common.product")}</span>
    </div>

    <form on:submit|preventDefault={handleSubmit} class="space-y-4">
      {#if error}
        <div
          class="bg-red-500/20 border border-red-500 text-red-400 px-4 py-2 rounded"
        >
          {error}
        </div>
      {/if}

      {#if !requiresTOTP}
        <div>
          <label
            for="username"
            class="block text-sm font-medium text-slate-400 mb-1"
          >
            {$t("common.username")}
          </label>
          <input
            type="text"
            id="username"
            bind:value={username}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg
                   focus:outline-none focus:border-blue-500"
            required
          />
        </div>

        <div>
          <label
            for="password"
            class="block text-sm font-medium text-slate-400 mb-1"
          >
            {$t("common.password")}
          </label>
          <input
            type="password"
            id="password"
            bind:value={password}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg
                   focus:outline-none focus:border-blue-500"
            required
          />
        </div>
      {:else if useRecoveryCode}
        <div class="text-center mb-4">
          <KeyRound class="w-12 h-12 text-status-info mx-auto mb-2" />
          <h2 class="text-lg font-semibold">{$t("login.recoveryTitle")}</h2>
          <p class="text-sm text-secondary">
            {$t("login.recoveryHint")}
          </p>
        </div>

        <Input
          id="recovery-code"
          label={$t("login.recoveryLabel")}
          bind:value={recoveryCode}
          placeholder={$t("login.recoveryPlaceholder")}
          autocomplete="off"
          maxlength={32}
          required
        />
      {:else}
        <!-- TOTP Input -->
        <div class="text-center mb-4">
          <ShieldCheck class="w-12 h-12 text-blue-500 mx-auto mb-2" />
          <h2 class="text-lg font-semibold">{$t("login.totpTitle")}</h2>
          <p class="text-sm text-slate-400">
            {$t("login.totpHint")}
          </p>
        </div>

        <div>
          <label
            for="totp"
            class="block text-sm font-medium text-slate-400 mb-1"
          >
            {$t("login.totpLabel")}
          </label>
          <input
            type="text"
            id="totp"
            bind:value={totpCode}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg
                   focus:outline-none focus:border-blue-500 text-center text-2xl tracking-widest"
            placeholder="000000"
            maxlength="6"
            pattern="[0-9]*"
            inputmode="numeric"
            autocomplete="one-time-code"
            required
          />
        </div>
      {/if}

      {#if requiresTOTP}
        <Button variant="ghost" class="w-full" on:click={switchCode}>
          {useRecoveryCode ? $t("login.useAppCode") : $t("login.useRecoveryCode")}
        </Button>

        <button
          type="button"
          on:click={resetForm}
          class="w-full text-sm text-slate-400 hover:text-slate-300"
        >
          {$t("login.back")}
        </button>
      {/if}

      <button
        type="submit"
        disabled={loading}
        class="w-full py-2 px-4 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800
               rounded-lg font-medium transition-colors"
      >
        {#if loading}
          {$t("login.signingIn")}
        {:else if requiresTOTP}
          {$t("login.verify")}
        {:else}
          {$t("login.signIn")}
        {/if}
      </button>
    </form>
  </div>
</div>
