<script lang="ts">
  import { onMount } from "svelte";
  import { goto } from "$app/navigation";
  import { login, getAuthStatus, getMe, type SecurityKeyRequest, type SignIn } from "$lib/api/auth";
  import { ApiError } from "$lib/api/client";
  import {
    user,
    isLoggedIn,
    isSetupOnly,
    forgetFailedSignOut,
    signOutAgain as endUnconfirmedSession,
    signOutFailed,
    TWO_FACTOR_SETUP_PATH,
  } from "$lib/stores/auth";
  import { connectWebSocket } from "$lib/stores/stats";
  import { Alert, Button, Input } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import { browserErrorKey, getAssertion, securityKeysSupported } from "$lib/webauthn";
  import { KeyRound, Server, ShieldCheck, Usb } from "lucide-svelte";

  let username = "";
  let password = "";
  let totpCode = "";
  let error = "";
  let loading = false;
  let requiresTOTP = false;
  // At the code step: a recovery code in place of the authenticator app's code.
  let useRecoveryCode = false;
  let recoveryCode = "";
  // At the code step (ADR-0409): the account has security keys that work at this address, the
  // challenge of the password step while it is unused and fresh, and where its keys work.
  let keysHere = false;
  let keyRequest: SecurityKeyRequest | null = null;
  let keyRequestAt = 0;
  let keyOrigins: string[] = [];
  let usingKey = false;
  // A challenge lives two minutes on the server and is used up by the answer it gets: an older one,
  // or a try after an answer, takes a new password step. 50 s and the browser's 60 s for the touch
  // stay within the two minutes.
  const KEY_REQUEST_FRESH_MS = 50_000;

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

      if (result.requires_totp) {
        // 2FA is required, show TOTP input
        requiresTOTP = true;
        codeStep(result);
        loading = false;
        return;
      }

      await signedIn();
    } catch (e: any) {
      failed(e);
    } finally {
      loading = false;
    }
  }

  /** What the code step offers besides the app's code. */
  function codeStep(result: SignIn) {
    keyRequest = result.security_key ?? null;
    keyRequestAt = Date.now();
    keysHere = keyRequest !== null;
    keyOrigins = result.security_key_origins ?? [];
  }

  async function signedIn() {
    // Signed in: the session cookie replaced one that a failed sign-out left.
    forgetFailedSignOut();
    const me = await getMe();
    user.set(me);
    isLoggedIn.set(true);
    // No socket serves an account that can only set up two-factor authentication.
    if (!isSetupOnly(me)) connectWebSocket();
    // While every account must have two-factor authentication, one without it is asked to set
    // it up at each sign-in, and after its grace period can only do that.
    goto(me.two_factor_required ? TWO_FACTOR_SETUP_PATH : "/");
  }

  function failed(e: any) {
    const key = browserErrorKey(e);
    error = key ? $t(key) : e.message || $t("login.failed");
    // Only a rejected password leads back to the password form. An invalid
    // or already used code ("Invalid 2FA code", "This two-factor code was
    // already used; wait for the next one", "Invalid recovery code"), a refused
    // security key ("Invalid security key"), too many attempts (429) or a server
    // error keep the 2FA step.
    if (e instanceof ApiError && e.status === 401 && !/2FA|two-factor|recovery code|security key/i.test(error)) {
      requiresTOTP = false;
    }
  }

  /** Sign in with a touch of a security key, in place of the app's code. */
  async function useSecurityKey() {
    if (usingKey || loading) return;
    error = "";
    usingKey = true;
    try {
      let request = keyRequest;
      if (!request || Date.now() - keyRequestAt > KEY_REQUEST_FRESH_MS) {
        const again = await login(username, password);
        if (!again.requires_totp) {
          await signedIn();
          return;
        }
        codeStep(again);
        request = keyRequest;
        if (!request) return;
      }
      const answer = await getAssertion(request);
      // Sent: the server uses the challenge up, whatever it answers.
      keyRequest = null;
      await login(username, password, undefined, undefined, answer);
      await signedIn();
    } catch (e: any) {
      failed(e);
    } finally {
      usingKey = false;
    }
  }

  function resetForm() {
    requiresTOTP = false;
    totpCode = "";
    useRecoveryCode = false;
    recoveryCode = "";
    keysHere = false;
    keyRequest = null;
    keyOrigins = [];
    error = "";
  }

  let signingOut = false;

  async function signOutAgain() {
    signingOut = true;
    try {
      await endUnconfirmedSession();
    } finally {
      signingOut = false;
    }
  }

  function switchCode() {
    useRecoveryCode = !useRecoveryCode;
    totpCode = "";
    recoveryCode = "";
    error = "";
  }
</script>

<div class="min-h-screen flex items-center justify-center bg-surface">
  <div class="bg-surface-elevated p-8 rounded-lg shadow-xl w-full max-w-md">
    <div class="flex items-center justify-center gap-2 mb-8">
      <Server class="w-10 h-10 text-brand-ring" />
      <span class="text-2xl font-bold">{$t("common.product")}</span>
    </div>

    {#if $signOutFailed}
      <!-- The page cannot delete the session cookie: only the server's answer to the sign-out does
           (ADR-0397). -->
      <Alert variant="warning" class="mb-4">
        <p>{$t("login.signOutFailed")}</p>
        <Button variant="secondary" size="sm" class="mt-2" loading={signingOut} on:click={signOutAgain}>
          {$t("login.signOutAgain")}
        </Button>
      </Alert>
    {/if}

    <form on:submit|preventDefault={handleSubmit} class="space-y-4">
      {#if error}
        <div
          class="bg-status-error/20 border border-status-error text-status-error px-4 py-2 rounded"
        >
          {error}
        </div>
      {/if}

      {#if !requiresTOTP}
        <div>
          <label
            for="username"
            class="block text-sm font-medium text-content-secondary mb-1"
          >
            {$t("common.username")}
          </label>
          <input
            type="text"
            id="username"
            bind:value={username}
            class="form-input"
            required
          />
        </div>

        <div>
          <label
            for="password"
            class="block text-sm font-medium text-content-secondary mb-1"
          >
            {$t("common.password")}
          </label>
          <input
            type="password"
            id="password"
            bind:value={password}
            class="form-input"
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
          <ShieldCheck class="w-12 h-12 text-brand-ring mx-auto mb-2" />
          <h2 class="text-lg font-semibold">{$t("login.totpTitle")}</h2>
          <p class="text-sm text-content-secondary">
            {$t("login.totpHint")}
          </p>
        </div>

        <!-- A security key in place of the code (ADR-0409), where the account's keys work. -->
        {#if keysHere && securityKeysSupported()}
          <Button variant="secondary" class="w-full" icon={Usb} loading={usingKey} disabled={loading}
            on:click={useSecurityKey}>
            {usingKey ? $t("securitykeys.login.touch") : $t("securitykeys.login.use")}
          </Button>
          <p class="text-center text-sm text-content-muted">{$t("securitykeys.login.or")}</p>
        {:else if keysHere}
          <Alert variant="info">{$t("securitykeys.unsupported")}</Alert>
        {:else if keyOrigins.length > 0}
          <Alert variant="info">{$t("securitykeys.login.elsewhere", { origins: keyOrigins.join(", ") })}</Alert>
        {/if}

        <div>
          <label
            for="totp"
            class="block text-sm font-medium text-content-secondary mb-1"
          >
            {$t("login.totpLabel")}
          </label>
          <input
            type="text"
            id="totp"
            bind:value={totpCode}
            class="form-input text-center text-2xl tracking-widest"
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

        <Button variant="plain"
          type="button"
          on:click={resetForm}
          class="w-full text-sm text-content-secondary hover:text-content-soft"
        >
          {$t("login.back")}
        </Button>
      {/if}

      <Button variant="primary" class="w-full"
        type="submit"
        disabled={loading || usingKey}
       
      >
        {#if loading}
          {$t("login.signingIn")}
        {:else if requiresTOTP}
          {$t("login.verify")}
        {:else}
          {$t("login.signIn")}
        {/if}
      </Button>
    </form>
  </div>
</div>
